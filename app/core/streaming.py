"""URLs de streaming de YouTube Music, resueltas con antelación.

Se usa el cliente «android» de YouTube a través de yt-dlp: no impone la espera de ~4 s del cliente
web/TV ni necesita ejecutar JavaScript, y su URL (formato 18, MP4 con audio AAC) se puede reproducir
directamente. Resolver cuesta ~1,5 s, así que se hace por adelantado y se guarda en memoria.
"""
import json
import threading
import time
import urllib.parse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from .config import DATA_DIR

_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "format": "18",
    "extractor_args": {"youtube": {"player_client": ["android"]}},
}
_cache: dict[str, tuple[str, float]] = {}
_pending: dict[str, Future] = {}
_local = threading.local()
_lock = threading.Lock()
_CACHE_FILE = Path(DATA_DIR) / "stream_cache.json"


def _load_cache() -> None:
    global _cache
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        for vid, entry in (data or {}).items():
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                url, exp = entry
                if isinstance(url, str) and isinstance(exp, (int, float)) and exp > now:
                    _cache[vid] = (url, float(exp))
    except Exception:  # noqa: BLE001 - caché opcional
        pass


def _save_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
    except Exception:  # noqa: BLE001 - caché opcional
        pass


_load_cache()
# Dos grupos de hilos: lo que se va a reproducir ya no espera detrás de las resoluciones por adelantado.
_now = ThreadPoolExecutor(2, thread_name_prefix="stream-now")
_ahead = ThreadPoolExecutor(3, thread_name_prefix="stream-ahead")
_WARM_ID = "BSTsnWoslP4"


def _expiry(url: str) -> float:
    expire = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("expire")
    return float(expire[0]) - 600 if expire else time.time() + 1800  # margen de 10 min


def cached(video_id: str) -> str | None:
    """URL ya resuelta y todavía válida, o None."""
    entry = _cache.get(video_id)
    return entry[0] if entry and entry[1] > time.time() else None


def _extract(video_id: str) -> str:
    if url := cached(video_id):
        return url
    ydl = getattr(_local, "ydl", None)
    if ydl is None:
        ydl = _local.ydl = yt_dlp.YoutubeDL(_OPTS)  # una instancia por hilo: ahorra el arranque
    try:
        info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
    except Exception:
        _local.ydl = None  # tras un error su estado es dudoso
        raise
    url = info["url"]
    _cache[video_id] = (url, _expiry(url))
    _save_cache()
    return url


def resolve(video_id: str) -> str:
    """URL de streaming ahora mismo (bloquea ~1,5 s si no estaba resuelta). Lanza si falla."""
    if url := cached(video_id):
        return url
    from . import downloader

    if not downloader.is_online():
        raise ConnectionError("sin conexión")
    last: Exception | None = None
    for attempt in range(3):
        try:
            return _now.submit(_extract, video_id).result()
        except Exception as exc:  # noqa: BLE001 - se reintenta ante cortes transitorios
            last = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    assert last is not None
    raise last


def resolve_ahead(video_id: str) -> Future | None:
    """Resuelve en segundo plano. Devuelve el Future, o None si ya estaba resuelta."""
    if cached(video_id):
        return None
    with _lock:
        fut = _pending.get(video_id)
        if fut is None or fut.done():
            fut = _pending[video_id] = _ahead.submit(_extract, video_id)
            fut.add_done_callback(lambda _f, v=video_id: _pending.pop(v, None))
    return fut


def warm_up() -> None:
    """Crea la instancia de yt-dlp de cada hilo antes de que haga falta."""
    for pool, n in ((_now, 2), (_ahead, 3)):
        for _ in range(n):
            pool.submit(lambda: _safe(_extract, _WARM_ID))


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - sin internet: se resolverá cuando haga falta
        pass
