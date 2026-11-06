"""Cola de respaldo sin internet: primero canciones del mismo género y, solo si no alcanzan,
de otros géneros. La cola «inspirada» con internet viene de `ytsearch.radio_tracks`."""
import random
from collections import defaultdict
from collections.abc import Callable


def reason_for(track: dict, genre: str | None) -> str:
    """Por qué esta canción entró en la cola de respaldo, en una frase corta."""
    wanted = (genre or "").strip().lower()
    if not wanted:
        return "de tu biblioteca"
    return "mismo género" if _genre(track) == wanted else "otro género"


def _artist(track: dict) -> str:
    return (track.get("artist") or "").split(",")[0].strip().lower()


def _genre(track: dict) -> str | None:
    return (track.get("genre") or "").strip().lower() or None


def _spread(tracks: list[dict], rng: random.Random) -> list[dict]:
    """Baraja y reparte por artista (una canción de cada artista por vuelta): no encadena al mismo."""
    by_artist: dict[str, list[dict]] = defaultdict(list)
    for track in rng.sample(tracks, len(tracks)):
        by_artist[_artist(track)].append(track)
    groups = list(by_artist.values())
    rng.shuffle(groups)
    out: list[dict] = []
    while groups:
        for group in list(groups):
            out.append(group.pop())
            if not group:
                groups.remove(group)
    return out


def _energy_close(tracks: list[dict], energy: Callable[[dict], float | None] | None,
                  target: float | None, tolerance: float) -> list[dict]:
    """Descarta saltos de intensidad extremos, si aún quedan candidatos suficientes.

    Sin onda medida (`None`) una canción no se descarta: no hay motivo para castigarla.
    """
    if energy is None or target is None:
        return tracks
    close = []
    for track in tracks:
        value = energy(track)
        if value is None or abs(value - target) <= tolerance:
            close.append(track)
    return close if len(close) >= min(4, len(tracks)) else tracks


def backup_queue(library: list[dict], genre: str | None, exclude: set[str], n: int = 25,
                 rng: random.Random | None = None,
                 energy: Callable[[dict], float | None] | None = None,
                 target_energy: float | None = None) -> list[dict]:
    """Hasta `n` canciones de la biblioteca para seguir sin internet.

    Mismo género primero. Los otros géneros solo entran si el género no alcanza, y de uno en uno
    (se agota un género antes de pasar al siguiente) para que la cola siga siendo coherente.
    `exclude` son los ids que no deben repetirse (lo ya sonado y lo ya en cola). Con `energy` y
    `target_energy` se evitan saltos bruscos de intensidad cuando hay alternativas.
    """
    rng = rng or random
    wanted = (genre or "").strip().lower() or None
    pool = [t for t in library if t["video_id"] not in exclude]
    same = _energy_close([t for t in pool if _genre(t) == wanted], energy, target_energy, 0.35)
    out = _spread(same, rng)[:n]
    if len(out) < n:
        others: dict[str | None, list[dict]] = defaultdict(list)
        for track in pool:
            if _genre(track) != wanted:
                others[_genre(track)].append(track)
        order = list(others)
        rng.shuffle(order)
        for key in order:
            out.extend(_spread(others[key], rng))
            if len(out) >= n:
                break
    return out[:n]
