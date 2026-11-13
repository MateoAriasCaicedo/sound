"""Conversión de MP4 fragmentado (como lo entrega YouTube) a MP4 normal, sin recodificar."""
import struct
import time

import pytest

from app.core import mp4fix
from app.ui.playback import PlaybackMixin


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def full(payload: bytes, flags: int = 0, version: int = 0) -> bytes:
    return struct.pack(">I", (version << 24) | flags) + payload


SCALE = 44100
FRAME = 1024  # duración de cada muestra AAC


def _moov(duration: int = 0) -> bytes:
    mvhd = full(struct.pack(">IIII", 0, 0, SCALE, duration) + b"\0" * 80)
    tkhd = full(struct.pack(">IIIII", 0, 0, 1, 0, duration) + b"\0" * 60, flags=3)
    mdhd = full(struct.pack(">IIIIHH", 0, 0, SCALE, duration, 0x55C4, 0))
    stbl = box(b"stbl", box(b"stsd", full(struct.pack(">I", 1) + box(b"mp4a", b"\0" * 28)))
               + box(b"stts", full(struct.pack(">I", 0))) + box(b"stsc", full(struct.pack(">I", 0)))
               + box(b"stsz", full(struct.pack(">II", 0, 0))) + box(b"stco", full(struct.pack(">I", 0))))
    minf = box(b"minf", box(b"smhd", full(b"\0" * 4)) + stbl)
    mdia = box(b"mdia", box(b"mdhd", mdhd) + box(b"hdlr", full(b"\0" * 4 + b"soun" + b"\0" * 13)) + minf)
    edts = box(b"edts", box(b"elst", full(struct.pack(">IIIhh", 1, 0, 1024, 1, 0))))
    trak = box(b"trak", box(b"tkhd", tkhd) + edts + mdia)
    mvex = box(b"mvex", box(b"trex", full(struct.pack(">IIIII", 1, 1, 0, 0, 0))))
    udta = box(b"udta", box(b"meta", full(b"") + box(b"ilst", box(b"\xa9nam", b"titulo"))))
    return box(b"moov", box(b"mvhd", mvhd) + mvex + trak + udta)


def _fragment(seq: int, samples: list[bytes]) -> bytes:
    """moof + mdat con base en el propio moof (como YouTube: tfhd 0x02002a, trun 0x201)."""
    tfhd = full(struct.pack(">IIII", 1, 1, FRAME, 0), flags=0x02002A)
    tfdt = full(struct.pack(">I", 0))

    def build(offset: int) -> bytes:
        trun = full(struct.pack(">Ii", len(samples), offset)
                    + b"".join(struct.pack(">I", len(s)) for s in samples), flags=0x201)
        traf = box(b"traf", box(b"tfhd", tfhd) + box(b"tfdt", tfdt) + box(b"trun", trun))
        return box(b"moof", box(b"mfhd", full(struct.pack(">I", seq))) + traf)

    moof = build(0)
    moof = build(len(moof) + 8)  # los datos empiezan tras el moof y la cabecera del mdat
    return moof + box(b"mdat", b"".join(samples))


def _fragmented(chunks: list[list[bytes]]) -> bytes:
    total = sum(len(c) for c in chunks) * FRAME
    out = box(b"ftyp", b"dash" + struct.pack(">I", 0) + b"iso6mp41") + _moov(total)
    out += box(b"sidx", full(b"\0" * 24))
    for seq, samples in enumerate(chunks, start=1):
        out += _fragment(seq, samples)
    return out


def _children(data: bytes, start: int = 0, end: int | None = None) -> dict[bytes, tuple[int, int]]:
    return {k: (b, e) for k, _s, b, e in mp4fix._boxes(data, start, len(data) if end is None else end)}


def _path(data: bytes, *kinds: bytes) -> tuple[int, int]:
    start, end = 0, len(data)
    for kind in kinds:
        start, end = _children(data, start, end)[kind]
    return start, end


CHUNKS = [[b"A" * 10, b"BB" * 7, b"C" * 3], [b"D" * 12, b"E" * 5]]


def test_conversion_keeps_every_byte_of_audio_in_order_with_its_tables():
    fixed = mp4fix.defragment_bytes(_fragmented(CHUNKS))
    top = _children(fixed)
    assert b"moof" not in top and b"sidx" not in top
    assert fixed[8:12] == b"M4A "
    b, e = _path(fixed, b"moov", b"trak", b"mdia", b"minf", b"stbl")
    tables = _children(fixed, b, e)
    count = struct.unpack_from(">I", fixed, tables[b"stsz"][0] + 8)[0]
    sizes = struct.unpack_from(f">{count}I", fixed, tables[b"stsz"][0] + 12)
    assert list(sizes) == [len(s) for chunk in CHUNKS for s in chunk]
    assert struct.unpack_from(">III", fixed, tables[b"stts"][0] + 4) == (1, 5, FRAME)
    n = struct.unpack_from(">I", fixed, tables[b"stco"][0] + 4)[0]
    offsets = struct.unpack_from(f">{n}I", fixed, tables[b"stco"][0] + 8)
    stsc = struct.unpack_from(">IIIIIII", fixed, tables[b"stsc"][0] + 4)
    assert stsc == (2, 1, 3, 1, 2, 2, 1)  # trozo 1: 3 muestras; desde el 2: 2 muestras
    audio = b"".join(fixed[o:o + sum(len(s) for s in chunk)] for o, chunk in zip(offsets, CHUNKS))
    assert audio == b"".join(s for chunk in CHUNKS for s in chunk)
    assert b"stsd" in tables  # la descripción del códec se conserva


def test_conversion_drops_fragment_only_boxes_and_sets_durations():
    fixed = mp4fix.defragment_bytes(_fragmented(CHUNKS))
    moov = _children(fixed, *_path(fixed, b"moov"))
    assert b"mvex" not in moov
    assert b"edts" not in _children(fixed, *_path(fixed, b"moov", b"trak"))
    total = 5 * FRAME
    mvhd = fixed[moov[b"mvhd"][0]:moov[b"mvhd"][1]]
    assert struct.unpack_from(">I", mvhd, 16)[0] == total
    b, e = _path(fixed, b"moov", b"trak", b"mdia", b"mdhd")
    assert struct.unpack_from(">I", fixed, b + 16)[0] == total
    assert b"titulo" in fixed[moov[b"udta"][0]:moov[b"udta"][1]]  # las etiquetas se copian


def test_plain_or_broken_files_are_not_touched():
    with pytest.raises(mp4fix.Mp4Error):
        mp4fix.defragment_bytes(box(b"ftyp", b"M4A \0\0\0\0") + _moov())
    broken = bytearray(_fragmented(CHUNKS))
    broken[-30:] = b""  # el último mdat queda cortado
    with pytest.raises(mp4fix.Mp4Error):
        mp4fix.defragment_bytes(bytes(broken))


def test_defragment_rewrites_the_file_in_place_once(tmp_path):
    song = tmp_path / "cancion.m4a"
    song.write_bytes(_fragmented(CHUNKS))
    assert mp4fix.is_fragmented(song)
    assert mp4fix.defragment(song) is True
    assert not mp4fix.is_fragmented(song)
    assert mp4fix.defragment(song) is False  # ya era normal
    assert list(tmp_path.iterdir()) == [song]  # sin temporales olvidados


def test_a_file_that_cannot_be_converted_stays_as_it_was(tmp_path):
    song = tmp_path / "rota.m4a"
    data = bytearray(_fragmented(CHUNKS))
    data[-30:] = b""
    song.write_bytes(bytes(data))
    assert mp4fix.defragment(song) is False
    assert song.read_bytes() == bytes(data)
    assert list(tmp_path.iterdir()) == [song]


class _Clock:
    _clock_ref = (0, 0.0)


def test_impossible_end_notices_a_song_that_ends_before_it_could():
    fake = _Clock()
    fake._clock_ref = (0, time.monotonic())
    assert PlaybackMixin._impossible_end(fake, 257_800)       # 4:18 «sonó» en un instante
    assert not PlaybackMixin._impossible_end(fake, 2_000)     # el margen de arranque
    fake._clock_ref = (250_000, time.monotonic())             # tras saltar casi al final
    assert not PlaybackMixin._impossible_end(fake, 251_000)
    fake._clock_ref = (0, time.monotonic() - 240)             # cuatro minutos sonando
    assert not PlaybackMixin._impossible_end(fake, 239_000)
