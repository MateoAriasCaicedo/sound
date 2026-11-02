import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import filters, library, ytsearch
from .config import GENRE_CACHE

CACHE_TTL = 7 * 24 * 3600
MIN_PER_PLAYLIST = 5
PLAYLIST_FETCH = 300  # tope por playlist; se piden páginas solo mientras haga falta


norm = filters.fold  # minúsculas y sin tildes, para comparar 'Español' con 'espanol'


_base_title = filters.base_title

# Géneros recomendados: primero lo colombiano y lo más escuchado en Colombia, y después el resto de los
# géneros de YouTube. Cada entrada es (nombre que se muestra, receta). Sin receta se resuelve como un
# género oficial o un término de playlist. Con receta, 'query' busca canciones y 'titles' (opcional)
# busca playlists oficiales cuyo título cumple esa expresión: no se pone 'titles' cuando daría falsos
# positivos ('porro' coincide con «Club de Porros», una playlist de hip-hop).
COLOMBIAN = (
    ("Música colombiana", {"titles": r"colombia", "query": "música colombiana"}),
    ("Vallenato", None),
    ("Salsa colombiana", {"query": "salsa colombiana"}),
    ("Cumbia colombiana", {"query": "cumbia colombiana"}),
    ("Champeta", {"query": "champeta colombiana"}),
    ("Música popular", {"query": "música de despecho colombiana"}),
    ("Carranga", {"query": "carranga"}),
    ("Música llanera", {"query": "música llanera"}),
    ("Bambuco", {"query": "bambuco colombiano"}),
    ("Porro", {"query": "porro colombiano"}),
    ("Música del Pacífico", {"query": "música del pacífico colombiano"}),
    ("Reggaetón colombiano", {"query": "reggaeton colombiano"}),
    ("Pop colombiano", {"query": "pop colombiano"}),
    ("Rock colombiano", {"query": "rock colombiano"}),
)
POPULAR_IN_COLOMBIA = (
    "Urbano", "Tropical", "Salsa", "Cumbia", "Bachata", "Merengue", "Pop Latino", "Baladas",
    "Rock en Español", "Hip-hop en Español",
)
CURATED = {filters.fold(name): spec for name, spec in COLOMBIAN if spec}
# Recetas extra del sidebar que no están en COLOMBIAN (solo «Parrandera» por ahora).
CURATED[filters.fold("Parrandera")] = {"query": "música parrandera"}

# Géneros que se muestran en el sidebar de la ventana (y en `sound generos`).
SIDEBAR_GENRES = (
    "Música popular",
    "Vallenato",
    "Reggaeton",
    "Salsa",
    "Merengue",
    "Música mexicana",
    "Bachata",
    "Parrandera",
    "Cumbia",
    "Champeta",
)


def recommended_names(official) -> list[str]:
    """Géneros para ofrecer: lo colombiano primero, luego lo popular en Colombia y al final el
    resto de los oficiales (en su orden), sin repetir ninguno."""
    head = [name for name, _ in COLOMBIAN] + list(POPULAR_IN_COLOMBIA)
    seen = {norm(name) for name in head}
    return head + [name for name in official if norm(name) not in seen]


def sidebar_genres() -> list[str]:
    """Géneros fijos que se muestran en el sidebar (y en `sound generos`)."""
    return list(SIDEBAR_GENRES)


def _all_playlists() -> list[dict]:
    """Índice de todas las playlists de género de YouTube Music, con caché en disco."""
    try:
        cached = json.loads(GENRE_CACHE.read_text(encoding="utf-8"))
        if time.time() - cached["saved_at"] < CACHE_TTL:
            return cached["playlists"]
    except (OSError, ValueError, KeyError):
        pass  # sin caché válido: se reconstruye

    categories = ytsearch.genre_categories()

    def fetch(item: tuple[str, str]) -> list[dict]:
        name, params = item
        return [{**p, "genre": name} for p in ytsearch.mood_playlists(params)]

    with ThreadPoolExecutor(5) as pool:
        found = [p for batch in pool.map(fetch, categories.items()) for p in batch]
    unique = list({p["playlist_id"]: p for p in found}.values())
    try:
        from .atomic import write_text_atomic

        write_text_atomic(GENRE_CACHE,
                          json.dumps({"saved_at": time.time(), "playlists": unique},
                                     ensure_ascii=False))
    except OSError:
        pass  # sin caché se vuelve a consultar la próxima vez; no es un fallo de la app
    return unique


def resolve(term: str) -> tuple[str, list[dict]] | None:
    """Devuelve (etiqueta, playlists) para un género o término; None si no hay coincidencia oficial."""
    wanted = norm(term)
    spec = CURATED.get(wanted)
    if spec:  # receta propia: no se adivina por el título de playlists ajenas
        pattern = spec.get("titles")
        playlists = [p for p in _all_playlists() if re.search(pattern, norm(p["title"]))] if pattern else []
        return term, playlists
    categories = ytsearch.genre_categories()
    by_name = {norm(name): name for name in categories}

    if wanted in by_name:  # género oficial exacto
        name = by_name[wanted]
        return name, ytsearch.mood_playlists(categories[name])

    by_title = [p for p in _all_playlists() if wanted in norm(p["title"])]
    if by_title:  # término fino, p. ej. "salsa" dentro de Tropical
        return term, by_title

    partial = [name for key, name in by_name.items() if wanted in key]
    if partial:  # p. ej. "latino" -> Pop Latino
        return partial[0], ytsearch.mood_playlists(categories[partial[0]])
    return None


def known_from_library(include_queue: bool = True) -> tuple[set[str], set[tuple[str, str]]]:
    """(ids, canciones) ya descargados (y encolados, si include_queue) para traer solo novedades.

    'Canción' es (artista principal, título base): detecta el mismo tema publicado con otro
    video_id, p. ej. la versión de álbum y el videoclip.
    """
    rows = library.known(include_queue)
    ids = {video_id for video_id, _, _ in rows}
    songs = {(norm(artist.split(",")[0]), _base_title(title)) for _, artist, title in rows}
    return ids, songs


class _Picker:
    """Acumula canciones respetando el total, los repetidos y el tope por artista."""

    def __init__(
        self,
        n: int,
        per_artist: int,
        strict: bool = True,
        skip_ids: set[str] | None = None,
        skip_songs: set[tuple[str, str]] | None = None,
    ) -> None:
        self.n = n
        self.per_artist = per_artist
        self.strict = strict
        self.picked: list[dict] = []
        self.rejected: dict[str, tuple[dict, str]] = {}  # video_id -> (canción, motivo), sin repetir
        # Lo que ya se tiene cuenta como 'visto': no se vuelve a elegir y no gasta cupo del lote.
        self._ids: set[str] = set(skip_ids or ())
        self._songs: set[tuple[str, str]] = set(skip_songs or ())
        self._artists: dict[str, int] = {}

    @property
    def full(self) -> bool:
        return len(self.picked) >= self.n

    def take(self, tracks: list[dict], limit: int | None) -> None:
        taken = 0
        for track in tracks:
            if self.full or (limit is not None and taken >= limit):
                return
            reason = filters.reject_reason(track) if self.strict else None
            if reason:
                self.rejected.setdefault(track["video_id"], (track, reason))
                continue
            artist = norm(track["artist"].split(",")[0])
            song = (artist, _base_title(track["title"]))
            if (
                track["video_id"] in self._ids
                or song in self._songs
                or self._artists.get(artist, 0) >= self.per_artist
            ):
                continue
            self._ids.add(track["video_id"])
            self._songs.add(song)
            self._artists[artist] = self._artists.get(artist, 0) + 1
            self.picked.append(track)
            taken += 1


Picker = _Picker  # nombre público: lo reutiliza prompt.py


def select_tracks(
    term: str,
    n: int,
    per_artist: int = 3,
    strict: bool = True,
    rejected: list[tuple[dict, str]] | None = None,
    skip_ids: set[str] | None = None,
    skip_songs: set[tuple[str, str]] | None = None,
) -> tuple[str, list[dict]]:
    """Reúne n canciones variadas: varias playlists, sin repetidos y con tope por artista.

    Con strict (por defecto) solo pasan las grabaciones originales: sin mixes, covers, directos
    ni karaokes. Si se pasa `rejected`, se llena con (canción, motivo) de lo descartado.
    `skip_ids` y `skip_songs` (ver known_from_library) evitan repetir lo que ya se tiene.
    """
    resolved = resolve(term)
    if resolved is None:
        # Género no oficial: la búsqueda de canciones da pistas reales; las playlists de usuarios no.
        # Se pide de más porque el filtro descarta parte de los resultados.
        label, source = term, iter([ytsearch.search_songs(term, max(n * 4, 60))])
    else:
        label, playlists = resolved
        source = (ytsearch.playlist_tracks(p["playlist_id"], PLAYLIST_FETCH) for p in playlists)

    picker = _Picker(n, per_artist, strict, skip_ids, skip_songs)
    per_playlist = max(MIN_PER_PLAYLIST, math.ceil(n / 6))  # fuerza a mezclar varias playlists
    fetched: list[list[dict]] = []
    for tracks in source:
        fetched.append(tracks)
        picker.take(tracks, per_playlist)
        if picker.full:
            break
    for tracks in fetched:  # si no alcanzó, se rellena sin el tope por playlist
        picker.take(tracks, None)
    if resolved is not None and not picker.full:
        # Las playlists no alcanzaron (o este género no tiene): se completa buscando canciones. Pasa con
        # géneros colombianos sin playlists oficiales y con playlists de artistas sueltos (champeta).
        query = (CURATED.get(norm(term)) or {}).get("query") or term
        picker.take(ytsearch.search_songs(query, max(n * 4, 60)), None)
    if rejected is not None:
        rejected.extend(picker.rejected.values())
    return label, picker.picked
