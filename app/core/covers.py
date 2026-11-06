"""Carátulas de las canciones: caché en disco, archivo local o miniatura de YouTube.

Es la única vía para obtener una portada, la use el reproductor o la vista de cuadrícula. La
caché es la vía rápida, pero si el archivo local se modificó después (etiquetas nuevas o una
descarga repetida) se vuelve a leer su carátula incrustada. Por último, para resultados que aún
no están en la biblioteca, se usa la URL de YouTube.
"""
import os
import threading
import urllib.request
from pathlib import Path

from . import config
from . import log as _log

log = _log.get("covers")
MAX_BYTES = 8 * 1024 * 1024  # una carátula mayor no es una carátula: se descarta


def cache_path(video_id: str) -> Path:
    """Archivo de caché de una canción (se crea la carpeta al guardar)."""
    return Path(config.COVER_DIR) / f"{video_id}.bin"


def cached(video_id: str) -> bytes | None:
    """La portada ya guardada en disco, si existe."""
    try:
        data = cache_path(video_id).read_bytes()
    except OSError:
        return None
    return data or None


def _store(video_id: str, data: bytes | None) -> None:
    if not data or len(data) > MAX_BYTES:
        return
    try:
        path = cache_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # el reproductor y la cuadrícula pueden extraer la misma portada a la vez: nunca se deja
        # un archivo a medio escribir que luego se lea como si estuviera completo
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:  # sin caché se sigue viendo: solo se vuelve a extraer la próxima vez
        _log.failure(log, f"no se pudo guardar la portada de {video_id}", exc)


def _embedded(path: str | Path | None) -> bytes | None:
    """Carátula incrustada en el archivo local (MP4/M4A), si la tiene."""
    if not path:
        return None
    try:
        from mutagen.mp4 import MP4

        covr = (MP4(str(path)).tags or {}).get("covr")
        return bytes(covr[0]) if covr else None
    except Exception as exc:  # noqa: BLE001 - un archivo raro no debe impedir ver la lista
        _log.failure(log, f"sin carátula incrustada en {path}", exc)
        return None


def _download(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read(MAX_BYTES + 1)
        return data if data and len(data) <= MAX_BYTES else None
    except Exception as exc:  # noqa: BLE001 - sin internet se usa el degradado
        _log.failure(log, f"no se pudo descargar la portada {url}", exc)
        return None


def _newer_than_cache(path: str | Path | None, video_id: str) -> bool:
    """El archivo local cambió después de guardar la caché (por ejemplo, al reetiquetarlo)."""
    if not path:
        return False
    try:
        return Path(path).stat().st_mtime > cache_path(video_id).stat().st_mtime
    except OSError:
        return False


def load(track: dict, path: str | Path | None = None) -> bytes | None:
    """Portada de `track`: archivo local → caché → URL. Devuelve los bytes o None.

    La caché manda salvo que el archivo local sea más nuevo: al editar las etiquetas o volver a
    descargar una canción, su carátula incrustada puede haber cambiado.
    """
    video_id = track.get("video_id")
    if not video_id:
        return None
    path = path or track.get("path")
    data = None if _newer_than_cache(path, video_id) else cached(video_id)
    if data:
        return data
    data = _embedded(path)
    if data:
        _store(video_id, data)
        return data
    data = cached(video_id)  # no había incrustada: sirve la que ya tuviéramos
    if data:
        return data
    data = _download(track.get("cover"))
    if data:
        _store(video_id, data)
    return data
