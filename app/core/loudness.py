"""Volumen por canción (ReplayGain) medido con ffmpeg, si está instalado.

No modifica los archivos: mide la sonoridad integrada (LUFS) y guarda una ganancia en una caché
JSON. La ventana la aplica al volumen del reproductor. Sin ffmpeg todo esto se ignora.
"""
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import log as _log
from .config import LOUDNESS_CACHE

log = _log.get("loudness")
TARGET_LUFS = -14.0  # referencia habitual de streaming
MAX_GAIN_DB = 12.0
MIN_GAIN_DB = -12.0
TIMEOUT_S = 90
_LUFS = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_lock = threading.Lock()


def available(which=shutil.which) -> bool:
    return which("ffmpeg") is not None


def parse_lufs(text: str) -> float | None:
    """Última medición integrada del resumen de `ebur128` (ffmpeg)."""
    matches = _LUFS.findall(text or "")
    return float(matches[-1]) if matches else None


def gain_db(lufs: float | None, target: float = TARGET_LUFS) -> float:
    """Ganancia (dB) que acerca la canción al objetivo, con un tope prudente."""
    if lufs is None:
        return 0.0
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, target - lufs))


def measure_file(path: Path, timeout: float = TIMEOUT_S) -> float | None:
    """LUFS integrados del archivo. None si no hay ffmpeg, falla o no se puede leer."""
    if not available():
        return None
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("no se pudo medir %s: %s", path, exc)
        return None
    return parse_lufs(result.stderr)


def _entry(path: Path, lufs: float) -> dict:
    stat = path.stat()
    return {"mtime": int(stat.st_mtime), "size": stat.st_size, "lufs": lufs}


def _cache(path: Path | None) -> dict:
    path = Path(path or LOUDNESS_CACHE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(cache: dict, path: Path | None) -> None:
    path = Path(path or LOUDNESS_CACHE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("no se pudo guardar la caché de volumen en %s", path, exc_info=True)


def cached_gain(video_id: str, path: Path, cache_path: Path | None = None) -> float | None:
    """Ganancia ya medida para ese archivo, o None si no hay medición válida.

    Se invalida sola si el archivo cambió de tamaño o de fecha.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    with _lock:
        entry = _cache(cache_path).get(video_id)
    if not isinstance(entry, dict):
        return None
    if entry.get("size") != stat.st_size or entry.get("mtime") != int(stat.st_mtime):
        return None
    lufs = entry.get("lufs")
    return gain_db(float(lufs)) if isinstance(lufs, (int, float)) else None


def measure_and_store(video_id: str, path: Path,
                      cache_path: Path | None = None) -> float | None:
    """Mide, guarda y devuelve la ganancia. None si no se pudo medir."""
    path = Path(path)
    lufs = measure_file(path)
    if lufs is None:
        return None
    try:
        entry = _entry(path, lufs)
    except OSError:
        return None
    with _lock:
        cache = _cache(cache_path)
        cache[video_id] = entry
        _save(cache, cache_path)
    log.info("volumen medido para %s: %.1f LUFS (%.1f dB)", video_id, lufs, gain_db(lufs))
    return gain_db(lufs)


def scan(tracks: list[dict], on_result=None, cache_path: Path | None = None) -> int:
    """Mide las canciones que tengan archivo. Devuelve cuántas se midieron."""
    measured = 0
    for track in tracks:
        path = Path(track.get("path") or "")
        if not track.get("video_id") or not path.exists():
            continue
        lufs = measure_file(path)
        if lufs is None:
            continue
        try:
            entry = _entry(path, lufs)
        except OSError:
            continue
        with _lock:
            cache = _cache(cache_path)
            cache[track["video_id"]] = entry
            _save(cache, cache_path)
        measured += 1
        if on_result:
            on_result(track, gain_db(lufs))
    return measured
