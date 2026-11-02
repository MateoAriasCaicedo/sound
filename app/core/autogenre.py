"""Género para las canciones que llegan sin él.

YouTube Music no dice el género de una canción, así que se le pregunta a iTunes (sin clave) por
artista y título. Es un extra: si no hay coincidencia o falla la red, la canción se descarga
igual, sin género.
"""
import threading
import time

import requests

from . import filters
from . import log as _log

log = _log.get("autogenre")

SEARCH_URL = "https://itunes.apple.com/search"
COUNTRY = "CO"
MIN_INTERVAL = 3.0  # iTunes limita a ~20 peticiones por minuto
TIMEOUT = 10
MAX_RESULTS = 15

# Nombres de iTunes que tienen un equivalente más corto en la aplicación.
_ALIASES = {  # claves ya pasadas por filters.fold (minúsculas y sin tildes)
    "salsa y tropical": "Salsa",
    "salsa": "Salsa",
    "musica tropical": "Tropical",
    "musica latina": "Latino",
    "latin": "Latino",
    "urbano latino": "Urbano",
    "reggaeton": "Reggaeton",
    "pop en espanol": "Pop Latino",
    "pop latino": "Pop Latino",
    "musica mexicana": "Música mexicana",
}

_lock = threading.Lock()
_last_request = 0.0


def _canonical(name: str) -> str:
    return _ALIASES.get(filters.fold(name), name)


def _score(track: dict, result: dict) -> int:
    """Cuánto se parece el resultado a la canción; 0 si no es la misma (artista y título)."""
    artist = filters.fold((track.get("artist") or "").split(",")[0])
    found_artist = filters.fold(result.get("artistName") or "")
    if not artist or not found_artist or not (artist in found_artist or found_artist in artist):
        return 0

    title = filters.base_title(track.get("title") or "")
    found_title = filters.base_title(result.get("trackName") or "")
    if not title or not found_title or not (title in found_title or found_title in title):
        return 0

    score = 6
    if title == found_title:
        score += 1
    album = filters.fold(track.get("album") or "")
    found_album = filters.fold(result.get("collectionName") or "")
    if album and found_album and (album in found_album or found_album in album):
        score += 2  # con el mismo álbum se elige la versión correcta (single, directo, etc.)
    return score


def _best_genre(track: dict, results: list[dict]) -> str | None:
    scored = [(_score(track, result), index, result) for index, result in enumerate(results)]
    score, _, best = max(scored, default=(0, 0, {}))
    if score < 6:
        return None
    name = (best.get("primaryGenreName") or "").strip()
    return _canonical(name) if name else None


def _wait_turn() -> None:
    """Deja pasar al menos MIN_INTERVAL entre peticiones, también entre hilos."""
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def lookup(track: dict) -> str | None:
    """Género de la canción según iTunes, o None si no se puede saber."""
    artist = (track.get("artist") or "").split(",")[0].strip()
    title = (track.get("title") or "").strip()
    if not artist or not title or filters.fold(artist) in ("desconocido", "sin artista"):
        return None

    with _lock:  # una petición a la vez: el límite de iTunes es por IP
        _wait_turn()
        try:
            response = requests.get(
                SEARCH_URL,
                params={"term": f"{artist} {title}", "entity": "song",
                        "country": COUNTRY, "limit": MAX_RESULTS},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (requests.RequestException, ValueError, AttributeError) as exc:
            log.info("sin género para %s - %s: %s", artist, title, exc)
            return None
    return _best_genre(track, results)
