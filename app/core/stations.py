"""Estaciones de Sound: qué radios sin fin ofrecer, hechas con lo que escuchas.

Cada estación empieza por una canción (la semilla), sigue con unas cuantas tuyas parecidas
(`extras`) y luego con lo que YouTube Music pone en la «radio» de la semilla, sin fin. Esto solo
decide cuáles ofrecer y con qué empiezan; sonar es cosa de la ventana (ui/stations.py).

- «Radio de Shakira»: tus artistas más escuchados (empieza por su canción que más suena).
- «Radio de “Vivir mi vida”»: tus canciones más escuchadas.
- «Radio de tus Me gusta»: si tienes unas cuantas marcadas.
- «Radio Rock»: los géneros con más canciones en tu biblioteca.
- Sin biblioteca ni escuchas: los géneros del lateral (la semilla se busca en YouTube al pulsar).

Funciones puras (sin Tk, red ni base de datos): la ventana les pasa lo ya leído.
"""
from __future__ import annotations

import random

from .filters import song_key

ARTISTS, SONGS, GENRES = 4, 2, 3  # cuántas de cada tipo
EXTRAS = 4                        # canciones tuyas tras la semilla, antes de las parecidas
MIN_LIKED = 3
SKIP_GENRES = {"", "sin género"}


def _main_artist(track: dict) -> str:
    return (track.get("artist") or "").split(",")[0].strip()


def _pick(rng: random.Random, tracks: list[dict], exclude: str, n: int = EXTRAS) -> list[dict]:
    pool = [t for t in tracks if t["video_id"] != exclude]
    rng.shuffle(pool)
    return pool[:n]


def station(kind: str, title: str, subtitle: str, seed: dict | None = None,
            extras: list[dict] | None = None, **extra) -> dict:
    return {"id": f"{kind}:{title.casefold()}", "kind": kind, "title": title,
            "subtitle": subtitle, "seed": seed, "extras": list(extras or []), **extra}


def for_track(track: dict, extras: list[dict] | None = None) -> dict:
    """«Radio de “canción”»: desde el menú de una canción."""
    return station("track", f"Radio de «{track.get('title') or 'esta canción'}»",
                   f"{_main_artist(track) or 'Canciones'} y parecidas", track, extras)


def for_artist(artist: str, tracks: list[dict], counts: dict[str, int], day: int) -> dict | None:
    """«Radio de artista»: empieza por su canción más escuchada (o una al azar) de tu biblioteca."""
    mine = [t for t in tracks if song_key(_main_artist(t), "")[0] == song_key(artist, "")[0]]
    if not mine:
        return None
    rng = random.Random(day)
    played = sorted(mine, key=lambda t: counts.get(t["video_id"], 0), reverse=True)
    seed = played[0] if counts.get(played[0]["video_id"]) else rng.choice(mine)
    return station("artist", f"Radio de {artist}", f"{artist} y artistas parecidos", seed,
                   _pick(rng, mine, seed["video_id"]))


def suggest(top: list[dict], liked: list[dict], library: list[dict], counts: dict[str, int],
            day: int, sidebar_genres: list[str]) -> list[dict]:
    """Las estaciones de «Para ti», de lo más tuyo a lo más general, sin repetir semilla."""
    by_id = {t["video_id"]: t for t in library}
    rng = random.Random(day)
    out: list[dict] = []
    seeds: set[str] = set()

    def add(item: dict | None) -> None:
        if item and item["seed"] and item["seed"]["video_id"] not in seeds:
            seeds.add(item["seed"]["video_id"])
            out.append(item)

    played = [by_id[t["video_id"]] for t in top if t.get("video_id") in by_id]
    plays_by_artist: dict[str, int] = {}
    names: dict[str, str] = {}
    for t in played:
        key = song_key(_main_artist(t), "")[0]
        if key:
            plays_by_artist[key] = plays_by_artist.get(key, 0) + counts.get(t["video_id"], 1)
            names.setdefault(key, _main_artist(t))
    for key, _plays in sorted(plays_by_artist.items(), key=lambda kv: -kv[1])[:ARTISTS]:
        add(for_artist(names[key], library, counts, day))
    for t in played[:SONGS + ARTISTS]:
        if sum(1 for s in out if s["kind"] == "track") >= SONGS:
            break
        add(for_track(t, _pick(rng, [x for x in library if x.get("genre") == t.get("genre")],
                               t["video_id"])))
    mine_liked = [by_id[t["video_id"]] for t in liked if t.get("video_id") in by_id]
    if len(mine_liked) >= MIN_LIKED:
        seed = rng.choice(mine_liked)
        add(station("liked", "Radio de tus Me gusta", "Lo que marcaste y parecidas", seed,
                    _pick(rng, mine_liked, seed["video_id"])))
    genres: dict[str, list[dict]] = {}
    for t in library:
        genre = (t.get("genre") or "").strip()
        if genre.casefold() not in SKIP_GENRES:
            genres.setdefault(genre, []).append(t)
    for genre, tracks in sorted(genres.items(), key=lambda kv: -len(kv[1]))[:GENRES]:
        seed = rng.choice(tracks)
        add(station("genre", f"Radio {genre}", f"{genre} sin fin", seed,
                    _pick(rng, tracks, seed["video_id"])))
    if not out:  # sin nada tuyo todavía: los géneros del lateral, con la semilla de YouTube
        out = [station("yt_genre", f"Radio {g}", "Lo popular y parecidas", genre=g)
               for g in sidebar_genres[:6]]
    return out
