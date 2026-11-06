"""Letras sincronizadas desde LRCLIB (opcional, con caché en disco).

Solo se consulta si el usuario activa la opción: la petición envía título, artista, álbum y
duración a un servicio de terceros. La respuesta se guarda por canción para no repetirla; un
«no hay letra» también se recuerda unos días, pero un fallo de red no se cachea.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .. import __version__
from . import config
from . import log as _log
from .atomic import write_text_atomic

log = _log.get("lyrics")
TIMEOUT_S = 8
MAX_BYTES = 1024 * 1024
NEGATIVE_TTL_S = 7 * 24 * 3600
BASE = "https://lrclib.net/api"
USER_AGENT = f"Sound/{__version__} (https://github.com/MateoAriasCaicedo/sound)"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_TS = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_TS = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.I)
_META = re.compile(r"^\[(ar|ti|al|by|offset|length|re|ve):[^\]]*\]\s*", re.I)


def _cache_path(video_id: str) -> Path:
    if not _SAFE_ID.fullmatch(video_id):
        raise ValueError(f"identificador no válido: {video_id!r}")
    return Path(config.LYRICS_DIR) / f"{video_id}.json"


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """Convierte LRC en [(milisegundos, línea)], ordenado y sin marcas de metadatos."""
    if not text:
        return []
    offset = 0
    match = _OFFSET.search(text)
    if match:
        try:
            offset = max(-60_000, min(60_000, int(match.group(1))))
        except ValueError:
            offset = 0
    rows: list[tuple[int, str]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        line = _WORD_TS.sub("", _TS.sub("", _META.sub("", raw))).strip()
        if not line:
            continue
        for stamp in stamps:
            minutes, seconds = int(stamp.group(1)), int(stamp.group(2))
            fraction = stamp.group(3) or "0"
            millis = int(fraction) * (100 if len(fraction) == 1 else 10 if len(fraction) == 2 else 1)
            # `[offset:+]` adelanta la letra: se resta a las marcas
            when = max(0, (minutes * 60 + seconds) * 1000 + millis - offset)
            rows.append((when, line))
    rows.sort(key=lambda row: row[0])
    return rows


def line_at(lines: list[tuple[int, str]], position_ms: int) -> int:
    """Índice de la última línea que ya empezó; -1 antes de la primera."""
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if lines[mid][0] <= position_ms:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def cached(video_id: str) -> dict | None:
    """La letra ya guardada (también un «no hay»), o None si nunca se consultó."""
    try:
        data = json.loads(_cache_path(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def store(video_id: str, payload: dict) -> None:
    try:
        path = _cache_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(payload, ensure_ascii=False))
    except OSError as exc:
        log.warning("no se pudo guardar la letra de %s: %s", video_id, exc)


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None
    return json.loads(data.decode("utf-8", "replace"))


def _request(url: str, timeout: float) -> Any:
    """Como `_get_json`, pero un 404 significa «no hay letra», no un fallo."""
    try:
        return _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _payload(found: dict) -> dict | None:
    synced = parse_lrc(found.get("syncedLyrics") or "")
    plain = (found.get("plainLyrics") or "").strip()
    if not synced and not plain:
        return None
    return {"synced": synced, "plain": plain, "source": "lrclib", "at": time.time()}


def _best_match(results: Any, duration: Any) -> dict | None:
    """De una búsqueda, la versión con letra más parecida en duración (prefiere la sincronizada)."""
    if not isinstance(results, list):
        return None
    candidates = [r for r in results if isinstance(r, dict)
                  and (r.get("syncedLyrics") or r.get("plainLyrics"))]
    if not candidates:
        return None
    synced = [r for r in candidates if r.get("syncedLyrics")]
    pool = synced or candidates
    if isinstance(duration, (int, float)) and duration > 0:
        pool.sort(key=lambda r: abs(float(r.get("duration") or 0) - duration))
    return pool[0]


def fetch(track: dict, timeout: float = TIMEOUT_S) -> dict | None:
    """Letra de una canción. None solo si no se pudo preguntar (sin red, título vacío…).

    Un «no hay letra» se devuelve como payload con `negative: True` y se cachea unos días.
    """
    video_id = track.get("video_id")
    if not video_id or not _SAFE_ID.fullmatch(str(video_id)):
        return None
    previous = cached(video_id)
    if previous is not None:
        if not previous.get("negative"):
            return previous
        try:
            if time.time() - float(previous.get("at") or 0) < NEGATIVE_TTL_S:
                return previous
        except (TypeError, ValueError):
            pass

    artist = (track.get("artist") or "").strip()
    title = (track.get("title") or "").strip()
    if not title:
        return None
    duration = track.get("duration")
    params = {"artist_name": artist, "track_name": title}
    album = (track.get("album") or "").strip()
    if album:
        params["album_name"] = album
    if isinstance(duration, (int, float)) and duration > 0:
        params["duration"] = int(duration)

    try:
        found = _request(f"{BASE}/get?{urllib.parse.urlencode(params)}", timeout)
        payload = _payload(found) if isinstance(found, dict) else None
        if payload is None:  # `get` exige coincidencia exacta; la búsqueda es más tolerante
            query = {"artist_name": artist, "track_name": title}
            best = _best_match(
                _request(f"{BASE}/search?{urllib.parse.urlencode(query)}", timeout), duration)
            payload = _payload(best) if best else None
    except Exception as exc:  # noqa: BLE001 - sin red la canción suena igual
        log.warning("no se pudo consultar la letra de %s: %s", video_id, exc)
        return None

    if payload is None:
        payload = {"synced": [], "plain": "", "source": "lrclib", "at": time.time(),
                   "negative": True}
    store(video_id, payload)
    return payload
