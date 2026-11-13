"""Convierte audio MP4 fragmentado (el que entrega YouTube) en un MP4 normal, sin recodificar.

YouTube sirve el audio .m4a «fragmentado»: la cabecera (`moov`) no trae ninguna muestra y el
audio va repartido en trozos (`moof` + `mdat`). Con ffmpeg instalado, yt-dlp lo arregla al
descargar; sin él el archivo se queda así y Media Foundation (el reproductor de Windows) lo da
por terminado nada más empezar, porque ve cero muestras y una duración de varios minutos. Sound
saltaba entonces de canción en canción sin que sonara ninguna. (El streaming no tiene el
problema: Windows sí recorre los fragmentos cuando llegan por la red.)

Aquí se reconstruye el archivo como un MP4 corriente: las mismas muestras de audio, en el mismo
orden, con sus tablas (`stts`, `stsz`, `stsc`, `stco`) en la cabecera y todo el audio en un solo
`mdat`. Las etiquetas (título, artista, portada…) se copian tal cual. No se toca ni un byte del
audio.

Solo se aceptan archivos de una pista; ante cualquier cosa inesperada (varias pistas, tamaños que
no cuadran) se lanza `Mp4Error` y el archivo original queda intacto.
"""
from __future__ import annotations

import os
import struct
import threading
from pathlib import Path

from . import log as _log

log = _log.get("mp4fix")
_lock = threading.Lock()  # una conversión a la vez: el repaso inicial y las descargas no chocan

# tablas de muestras que se rehacen; las demás cajas de `stbl` (stsd…) se conservan
_SAMPLE_TABLES = {b"stts", b"stsc", b"stsz", b"stz2", b"stco", b"co64", b"stss", b"ctts",
                  b"sdtp", b"sgpd", b"sbgp"}


class Mp4Error(ValueError):
    """El archivo no tiene la forma esperada: no se convierte."""


# ---------- cajas ----------
def _boxes(buf: bytes, start: int, end: int):
    """(tipo, inicio, inicio del contenido, fin) de cada caja entre `start` y `end`."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise Mp4Error("caja truncada")
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise Mp4Error(f"caja {kind!r} con tamaño imposible")
        yield kind, pos, pos + header, pos + size
        pos += size


def _child(buf: bytes, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    """(inicio del contenido, fin) de la primera caja `kind`."""
    return next(((b, e) for k, _s, b, e in _boxes(buf, start, end) if k == kind), None)


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _full(payload: bytes, version: int = 0) -> bytes:
    return struct.pack(">I", version << 24) + payload


def is_fragmented(path: str | Path) -> bool:
    """¿Lleva fragmentos (`moof`)? Solo lee las cabeceras de las cajas de primer nivel."""
    try:
        with open(path, "rb") as fh:
            while True:
                head = fh.read(8)
                if len(head) < 8:
                    return False
                size, kind = struct.unpack(">I4s", head)
                if kind == b"moof":
                    return True
                if size == 1:
                    size = struct.unpack(">Q", fh.read(8))[0]
                    fh.seek(size - 16, 1)
                elif size < 8:  # 0 (hasta el final) o roto
                    return False
                else:
                    fh.seek(size - 8, 1)
    except (OSError, struct.error):
        return False


# ---------- muestras de los fragmentos ----------
def _fragments(data: bytes, default_duration: int,
               default_size: int) -> tuple[list[tuple[int, int, int]], list[int], list[int]]:
    """Recorre los `moof`: trozos de audio [(inicio, bytes, muestras)] y la duración y el
    tamaño de cada muestra, en el orden en que suenan."""
    chunks: list[tuple[int, int, int]] = []
    durations: list[int] = []
    sizes: list[int] = []
    for kind, start, body, end in _boxes(data, 0, len(data)):
        if kind != b"moof":
            continue
        trafs = [(b, e) for k, _s, b, e in _boxes(data, body, end) if k == b"traf"]
        if len(trafs) != 1:
            raise Mp4Error("se esperaba una sola pista por fragmento")
        tbody, tend = trafs[0]
        duration0, size0 = default_duration, default_size
        base = start  # por defecto los desplazamientos cuentan desde el propio moof
        cursor: int | None = None
        for k, _s, b, _e in _boxes(data, tbody, tend):
            flags = int.from_bytes(data[b + 1:b + 4], "big")
            if k == b"tfhd":
                p = b + 8  # versión y flags (4) + track_ID (4)
                if flags & 0x1:
                    base = struct.unpack_from(">Q", data, p)[0]
                    p += 8
                if flags & 0x2:
                    p += 4
                if flags & 0x8:
                    duration0 = struct.unpack_from(">I", data, p)[0]
                    p += 4
                if flags & 0x10:
                    size0 = struct.unpack_from(">I", data, p)[0]
            elif k == b"trun":
                count = struct.unpack_from(">I", data, b + 4)[0]
                p = b + 8
                offset = cursor if cursor is not None else base
                if flags & 0x1:
                    offset = base + struct.unpack_from(">i", data, p)[0]
                    p += 4
                if flags & 0x4:
                    p += 4
                run = 0
                for _ in range(count):
                    duration, size = duration0, size0
                    if flags & 0x100:
                        duration = struct.unpack_from(">I", data, p)[0]
                        p += 4
                    if flags & 0x200:
                        size = struct.unpack_from(">I", data, p)[0]
                        p += 4
                    p += (4 if flags & 0x400 else 0) + (4 if flags & 0x800 else 0)
                    durations.append(duration)
                    sizes.append(size)
                    run += size
                if offset < 0 or offset + run > len(data):
                    raise Mp4Error("los datos de un fragmento caen fuera del archivo")
                if count:
                    chunks.append((offset, run, count))
                cursor = offset + run
    if not chunks:
        raise Mp4Error("no hay fragmentos con audio")
    return chunks, durations, sizes


# ---------- tablas y cabeceras nuevas ----------
def _stts(durations: list[int]) -> bytes:
    runs: list[list[int]] = []
    for d in durations:
        if runs and runs[-1][1] == d:
            runs[-1][0] += 1
        else:
            runs.append([1, d])
    return _box(b"stts", _full(struct.pack(">I", len(runs)) + b"".join(
        struct.pack(">II", n, d) for n, d in runs)))


def _stsc(counts: list[int]) -> bytes:
    entries: list[tuple[int, int]] = []
    for chunk, n in enumerate(counts, start=1):
        if not entries or entries[-1][1] != n:
            entries.append((chunk, n))
    return _box(b"stsc", _full(struct.pack(">I", len(entries)) + b"".join(
        struct.pack(">III", first, n, 1) for first, n in entries)))


def _stsz(sizes: list[int]) -> bytes:
    return _box(b"stsz", _full(struct.pack(">II", 0, len(sizes)) + b"".join(
        struct.pack(">I", s) for s in sizes)))


def _stco(offsets: list[int]) -> bytes:
    if offsets and offsets[-1] > 0xFFFFFFFF:
        return _box(b"co64", _full(struct.pack(">I", len(offsets)) + b"".join(
            struct.pack(">Q", o) for o in offsets)))
    return _box(b"stco", _full(struct.pack(">I", len(offsets)) + b"".join(
        struct.pack(">I", o) for o in offsets)))


def _timescale(header: bytes) -> int:
    """Escala de tiempo de un mvhd/mdhd (contenido de la caja, sin su cabecera)."""
    return struct.unpack_from(">I", header, 4 + (16 if header[0] == 1 else 8))[0] or 1


def _with_duration(kind: bytes, header: bytes, duration: int) -> bytes:
    """mvhd, tkhd o mdhd con otra duración (versión 0 o 1)."""
    v1 = header[0] == 1
    at = 4 + (16 if v1 else 8) + (8 if kind == b"tkhd" else 4)  # tras fechas y escala/pista
    if v1:
        return header[:at] + struct.pack(">Q", duration) + header[at + 8:]
    return header[:at] + struct.pack(">I", min(duration, 0xFFFFFFFF)) + header[at + 4:]


# ---------- reconstrucción ----------
def defragment_bytes(data: bytes) -> bytes:
    """El mismo audio como MP4 normal. Lanza Mp4Error si el archivo no es el esperado."""
    top = list(_boxes(data, 0, len(data)))
    if not any(k == b"moof" for k, *_ in top):
        raise Mp4Error("no es un MP4 fragmentado")
    moov = next(((b, e) for k, _s, b, e in top if k == b"moov"), None)
    if moov is None:
        raise Mp4Error("falta moov")
    children = list(_boxes(data, *moov))
    traks = [(b, e) for k, _s, b, e in children if k == b"trak"]
    mvhd = next((data[b:e] for k, _s, b, e in children if k == b"mvhd"), None)
    if len(traks) != 1 or mvhd is None:
        raise Mp4Error("se esperaba una sola pista y su mvhd")
    trex_duration = trex_size = 0
    mvex = _child(data, *moov, b"mvex")
    if mvex and (trex := _child(data, *mvex, b"trex")):
        trex_duration, trex_size = struct.unpack_from(">II", data, trex[0] + 12)

    chunks, durations, sizes = _fragments(data, trex_duration, trex_size)
    mdia = _child(data, *traks[0], b"mdia")
    mdhd = _child(data, *mdia, b"mdhd") if mdia else None
    minf = _child(data, *mdia, b"minf") if mdia else None
    stbl = _child(data, *minf, b"stbl") if minf else None
    if not (mdhd and stbl):
        raise Mp4Error("pista sin mdhd o stbl")
    media_duration = sum(durations)
    movie_duration = round(media_duration * _timescale(mvhd) / _timescale(data[mdhd[0]:mdhd[1]]))

    def stbl_box(offsets: list[int]) -> bytes:
        kept = b"".join(data[s:e] for k, s, _b, e in _boxes(data, *stbl) if k not in _SAMPLE_TABLES)
        return _box(b"stbl", kept + _stts(durations) + _stsc([n for *_x, n in chunks])
                    + _stsz(sizes) + _stco(offsets))

    def rebuild(start: int, end: int, offsets: list[int]) -> bytes:
        """El contenido de una caja con las piezas cambiadas dentro (recorre trak → stbl)."""
        out = []
        for k, s, b, e in _boxes(data, start, end):
            if k in (b"mvex", b"edts"):
                continue  # sin fragmentos sobran; la lista de edición fragmentada no vale
            if k == b"mvhd":
                out.append(_box(k, _with_duration(k, data[b:e], movie_duration)))
            elif k == b"tkhd":
                out.append(_box(k, _with_duration(k, data[b:e], movie_duration)))
            elif k == b"mdhd":
                out.append(_box(k, _with_duration(k, data[b:e], media_duration)))
            elif k == b"stbl":
                out.append(stbl_box(offsets))
            elif k in (b"trak", b"mdia", b"minf"):
                out.append(_box(k, rebuild(b, e, offsets)))
            else:
                out.append(data[s:e])
        return b"".join(out)

    ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
    audio_size = sum(size for _o, size, _n in chunks)
    big = audio_size + 8 > 0xFFFFFFFF
    mdat_header = 16 if big else 8

    def layout(moov_size: int) -> list[int]:
        offsets, at = [], len(ftyp) + moov_size + mdat_header
        for _o, size, _n in chunks:
            offsets.append(at)
            at += size
        return offsets

    # el tamaño de moov no depende del valor de los desplazamientos (salvo si pasan a 64 bits)
    moov_box = _box(b"moov", rebuild(*moov, [0] * len(chunks)))
    for _ in range(2):
        candidate = _box(b"moov", rebuild(*moov, layout(len(moov_box))))
        if len(candidate) == len(moov_box):
            moov_box = candidate
            break
        moov_box = candidate
    audio = b"".join(data[o:o + size] for o, size, _n in chunks)
    head = (struct.pack(">I4sQ", 1, b"mdat", audio_size + 16) if big
            else struct.pack(">I4s", audio_size + 8, b"mdat"))
    return ftyp + moov_box + head + audio


def defragment(path: str | Path) -> bool:
    """Convierte el archivo en su sitio. True si lo convirtió; False si no hacía falta o no se
    pudo (el original queda intacto). Escribe a un temporal y lo sustituye de golpe."""
    path = Path(path)
    if not is_fragmented(path):
        return False
    with _lock:
        if not is_fragmented(path):  # otro hilo la convirtió mientras esperaba
            return False
        return _convert(path)


def _convert(path: Path) -> bool:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.mp4fix.tmp")
    try:
        fixed = defragment_bytes(path.read_bytes())
        with open(tmp, "wb") as fh:
            fh.write(fixed)
            fh.flush()
            os.fsync(fh.fileno())
        if is_fragmented(tmp):
            raise Mp4Error("la conversión dejó fragmentos")
        os.replace(tmp, path)
    except Mp4Error as exc:
        log.info("no se convirtió %s: %s", path.name, exc)
        return False
    except OSError as exc:  # en uso (sonando) o sin permiso: se intentará otra vez
        log.info("no se pudo convertir %s ahora: %s", path.name, exc)
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return True
