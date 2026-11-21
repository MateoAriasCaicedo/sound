"""«Descubre en YouTube»: canciones que aún no tienes, para Inicio y la bienvenida.

- Con historial: la «radio» de YouTube Music de lo que más escuchas (hasta tres canciones
  semilla, de artistas distintos), intercalada para que no salgan seguidas del mismo artista.
  Es lo mismo que ya usa la cola automática con la canción que suena.
- Sin historial: lo popular de unos géneros del lateral (cambian cada día).

Fuera lo que ya tienes (también el mismo tema publicado con otro vídeo) y lo que no es la
grabación original (mixes, covers, directos). Se guarda en `discover.json` para enseñarlo al
momento al abrir Sound; se renueva cada pocas horas o cuando cambian las semillas.
"""
from __future__ import annotations

import itertools
import json
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, filters

POOL = 24           # canciones que se guardan: Inicio enseña una fila; la bienvenida, por tandas
PER_ARTIST = 2      # como mucho, por artista
SEEDS = 3           # canciones semilla con historial
TOP_POOL = 10       # las semillas salen de las diez más escuchadas, distintas cada día
POPULAR_GENRES = 3  # sin historial: «Música popular» y dos géneros más del lateral
CACHE_TTL_S = 6 * 3600


def cache_path() -> Path:
    return config.DATA_DIR / "discover.json"


def pick_seeds(top: list[dict], recent: list[dict], have_ids: set[str], day: int,
               n: int = SEEDS) -> list[dict]:
    """Semillas: de las más escuchadas que sigan en la biblioteca (o, sin escuchas contadas,
    de lo último que sonó), `n` de artistas distintos; el día decide cuáles, para que cambie."""
    pool = [t for t in top if t.get("video_id") in have_ids][:TOP_POOL]
    if not pool:
        pool = [t for t in recent if t.get("video_id") in have_ids][:TOP_POOL]
    random.Random(day).shuffle(pool)
    seeds, artists = [], set()
    for t in pool:
        artist = filters.song_key(t.get("artist") or "", "")[0]
        if artist in artists:
            continue
        artists.add(artist)
        seeds.append(t)
        if len(seeds) >= n:
            break
    return seeds


def known() -> tuple[set[str], set[tuple[str, str]]]:
    """(ids, canciones) de lo descargado y lo que está en cola: no se sugiere lo que ya tienes."""
    from . import library
    rows = library.known(include_queue=True)
    return ({vid for vid, _a, _t in rows},
            {filters.song_key(artist or "", title or "") for _v, artist, title in rows})


def mix(batches: list[list[dict]], have_ids: set[str], have_songs: set[tuple[str, str]],
        n: int = POOL, per_artist: int = PER_ARTIST) -> list[dict]:
    """Intercala las listas (una de cada a la vez) y se queda con lo nuevo de verdad: sin lo
    que ya tienes, sin repetidos, sin versiones que no son la original y con tope por artista."""
    out: list[dict] = []
    ids, songs, artists = set(have_ids), set(have_songs), {}
    for batch in itertools.zip_longest(*batches):
        for t in batch:
            if t is None or not t.get("video_id") or t["video_id"] in ids:
                continue
            if filters.reject_reason(t):
                continue
            key = filters.song_key(t.get("artist") or "", t.get("title") or "")
            if key in songs or artists.get(key[0], 0) >= per_artist:
                continue
            ids.add(t["video_id"])
            songs.add(key)
            artists[key[0]] = artists.get(key[0], 0) + 1
            out.append(t)
            if len(out) >= n:
                return out
    return out


def _radio_batches(seeds: list[dict], radio: Callable) -> list[list[dict]]:
    def one(seed: dict) -> list[dict]:
        try:
            return radio(seed["video_id"], 25)
        except Exception:  # noqa: BLE001 - una semilla que falla no tumba las demás
            return []

    with ThreadPoolExecutor(SEEDS) as pool:
        return list(pool.map(one, seeds))


def popular_batches(day: int) -> list[list[dict]]:
    """Lo popular de «Música popular» y de otros dos géneros del lateral (según el día)."""
    from . import genres
    names = genres.sidebar_genres()
    picked = names[:1] + random.Random(day).sample(names[1:], min(POPULAR_GENRES - 1,
                                                                   len(names) - 1))
    with ThreadPoolExecutor(POPULAR_GENRES) as pool:
        return list(pool.map(lambda g: genres.select_tracks(g, 12)[1], picked))


def fetch(seeds: list[dict], have_ids: set[str], have_songs: set[tuple[str, str]], day: int,
          radio: Callable | None = None,
          popular: Callable[[int], list[list[dict]]] | None = None) -> list[dict]:
    """Las canciones de Descubre. `radio` y `popular` se sustituyen en las pruebas."""
    if radio is None:
        from . import ytsearch
        radio = ytsearch.radio_tracks
    if seeds:
        tracks = mix(_radio_batches(seeds, radio), have_ids, have_songs)
        if tracks:
            return tracks
    return mix((popular or popular_batches)(day), have_ids, have_songs)


def seed_key(seeds: list[dict]) -> str:
    """Identifica de qué salió la caché: si cambian las semillas, toca renovarla."""
    return ",".join(sorted(t["video_id"] for t in seeds)) or "popular"


def load_cache(key: str, path: Path | None = None,
               now: float | None = None) -> tuple[list[dict], bool]:
    """(canciones, ¿siguen valiendo?) de la última vez. Aunque no valgan (otras semillas o
    demasiado viejas) se enseñan mientras llegan las nuevas."""
    try:
        data = json.loads((path or cache_path()).read_text(encoding="utf-8"))
        tracks = [t for t in data["tracks"] if isinstance(t, dict) and t.get("video_id")]
        saved = float(data["saved_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return [], False
    fresh = data.get("key") == key and (now or time.time()) - saved < CACHE_TTL_S
    return tracks, fresh


def save_cache(key: str, tracks: list[dict], path: Path | None = None) -> None:
    try:
        from .atomic import write_text_atomic
        keep = ("video_id", "title", "artist", "album", "duration", "cover", "year")
        payload = {"saved_at": time.time(), "key": key,
                   "tracks": [{k: t.get(k) for k in keep} for t in tracks]}
        write_text_atomic(path or cache_path(), json.dumps(payload, ensure_ascii=False))
    except OSError:
        pass  # sin caché se vuelve a pedir la próxima vez
