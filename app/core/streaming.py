"""URLs de streaming de YouTube Music, resueltas con antelación.

Se usa el cliente «android» de YouTube a través de yt-dlp: no impone la espera de ~4 s del cliente
web/TV ni necesita ejecutar JavaScript, y su URL (formato 18, MP4 con audio AAC) se puede reproducir
directamente. Resolver cuesta ~1,5 s, así que se hace por adelantado y se guarda en memoria.
"""
import threading
import time
import urllib.parse
from concurrent.futures import Future, ThreadPoolExecutor

import yt_dlp

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
    return url


def resolve(video_id: str) -> str:
    """URL de streaming ahora mismo (bloquea ~1,5 s si no estaba resuelta). Lanza si falla."""
    return cached(video_id) or _now.submit(_extract, video_id).result()


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
