import re

from ytmusicapi import YTMusic
from ytmusicapi.navigation import (
    CAROUSEL_CONTENTS,
    GRID_ITEMS,
    MTRIR,
    SECTION_LIST,
    SINGLE_COLUMN_TAB,
    nav,
)
from ytmusicapi.parsers.browsing import parse_playlist

_yt: YTMusic | None = None


def _client() -> YTMusic:
    global _yt
    if _yt is None:
        _yt = YTMusic()
    return _yt


def _track(item: dict) -> dict | None:
    if not item.get("videoId"):
        return None
    artists = item.get("artists") or []
    return {
        "video_id": item["videoId"],
        "title": item.get("title", "Sin título"),
        "artist": ", ".join(a["name"] for a in artists) or "Desconocido",
        "album": (item.get("album") or {}).get("name"),
        "duration": item.get("duration_seconds"),
        "cover": _cover_url(item.get("thumbnails")),
        "video_type": item.get("videoType"),
        "year": item.get("year"),
    }


def _cover_url(thumbnails: list[dict] | None) -> str | None:
    """Carátula cuadrada del álbum (JPEG), pedida a 600px en vez del tamaño mini por defecto."""
    if not thumbnails:
        return None
    return re.sub(r"=w\d+-h\d+.*$", "=w600-h600", thumbnails[-1]["url"])


def search_songs(query: str, limit: int = 20) -> list[dict]:
    results = _client().search(query, filter="songs", limit=limit)
    return [t for t in map(_track, results) if t][:limit]  # la API devuelve páginas enteras, no el tope exacto


def search_playlists(genre: str, limit: int = 10) -> list[dict]:
    results = _client().search(genre, filter="playlists", limit=limit)
    return [
        {
            "playlist_id": r["browseId"].removeprefix("VL"),
            "title": r["title"],
            "author": r.get("author"),
            "count": r.get("itemCount"),
        }
        for r in results
        if r.get("browseId")
    ]


def song_details(video_id: str) -> dict:
    """Álbum y año de una canción. Los videoclips oficiales no los traen en las playlists,
    pero el 'watch playlist' de YouTube Music sí los devuelve para el propio video."""
    tracks = _client().get_watch_playlist(videoId=video_id, limit=1).get("tracks", [])
    if not tracks or tracks[0].get("videoId") != video_id:
        return {}
    year = tracks[0].get("year")
    return {
        "album": (tracks[0].get("album") or {}).get("name"),
        "year": int(year) if year and str(year).isdigit() else None,
    }


def genre_categories() -> dict[str, str]:
    """Géneros oficiales de YouTube Music: nombre -> params para pedir sus playlists."""
    sections = _client().get_mood_categories()
    return {c["title"]: c["params"] for c in sections.get("Genres", [])}


def mood_playlists(params: str) -> list[dict]:
    """Playlists de una categoría. Reimplementa get_mood_playlists de ytmusicapi porque
    esa función revienta entera si un solo elemento de la lista viene sin enlace."""
    response = _client()._send_request(
        "browse", {"browseId": "FEmusic_moods_and_genres_category", "params": params}
    )
    found = []
    for section in nav(response, SINGLE_COLUMN_TAB + SECTION_LIST):
        if "gridRenderer" in section:
            path = GRID_ITEMS
        elif "musicCarouselShelfRenderer" in section:
            path = CAROUSEL_CONTENTS
        else:
            continue
        for result in nav(section, path):
            if MTRIR not in result:
                continue
            try:
                parsed = parse_playlist(result[MTRIR])
            except KeyError:
                continue  # elemento sin enlace: se ignora
            found.append({"playlist_id": parsed["playlistId"], "title": parsed["title"]})
    return found


def playlist_tracks(playlist_id: str, limit: int = 100) -> list[dict]:
    try:
        data = _client().get_playlist(playlist_id, limit=limit)
    except KeyError:
        return []  # playlist vacía o no disponible: se ignora en lugar de abortar todo
    tracks = [t for t in map(_track, data.get("tracks", [])) if t]
    return tracks[:limit]  # la API devuelve páginas completas, ignora limit como tope exacto


def playlist_details(playlist_id: str, limit: int = 200) -> tuple[str, list[dict]]:
    """Título y canciones de una playlist (incluye las listas de álbum de YouTube)."""
    playlist_id = playlist_id.removeprefix("VL")
    try:
        data = _client().get_playlist(playlist_id, limit=limit)
    except KeyError:
        return "Playlist", []
    tracks = [t for t in map(_track, data.get("tracks", [])) if t]
    return data.get("title") or "Playlist", tracks[:limit]


def album_details(browse_id: str, limit: int | None = None) -> tuple[str, list[dict]]:
    """Título y pistas de un álbum (browseId MPREb_...)."""
    data = _client().get_album(browse_id)
    title = data.get("title") or "Álbum"
    cover = _cover_url(data.get("thumbnails"))
    year = data.get("year")
    year = int(year) if year and str(year).isdigit() else None
    tracks = []
    for item in data.get("tracks", []):
        if not item.get("videoId") or item.get("isAvailable") is False:
            continue
        artists = item.get("artists") or []
        tracks.append({
            "video_id": item["videoId"],
            "title": item.get("title", "Sin título"),
            "artist": ", ".join(a["name"] for a in artists) or "Desconocido",
            "album": item.get("album") or title,
            "duration": item.get("duration_seconds"),
            "cover": _cover_url(item.get("thumbnails")) or cover,
            "year": year,
        })
    return title, tracks[:limit] if limit else tracks


def album_browse_id(playlist_id: str) -> str | None:
    """Convierte el id de una playlist de álbum (OLAK5uy_...) en su browseId (MPREb_...)."""
    return _client().get_album_browse_id(playlist_id)


def album_tracks(playlist_id: str, limit: int = 200) -> list[dict]:
    """Pistas de un álbum a partir de su id de playlist (OLAK5uy_...)."""
    browse_id = album_browse_id(playlist_id)
    if not browse_id:
        return []
    return album_details(browse_id, limit)[1]


def artist_details(channel_id: str, limit: int = 200) -> tuple[str, list[dict]]:
    """Nombre y canciones más conocidas de un artista (canal UC...)."""
    data = _client().get_artist(channel_id)
    name = data.get("name") or "Artista"
    songs = data.get("songs") or {}
    playlist_id = songs.get("browseId")
    if playlist_id:
        _, tracks = playlist_details(playlist_id, limit)
        if tracks:
            return name, tracks
    found = []
    for item in songs.get("results") or []:
        if not item.get("videoId"):
            continue
        found.append({
            "video_id": item["videoId"],
            "title": item.get("title", "Sin título"),
            "artist": item.get("artist") or name,
            "album": item.get("album"),
            "duration": None,
            "cover": _cover_url(item.get("thumbnails")),
        })
    return name, (found or search_songs(name, limit))[:limit]


def song_track(video_id: str) -> dict | None:
    """Los datos de una canción concreta, para importar un enlace de vídeo."""
    tracks = _client().get_watch_playlist(videoId=video_id, limit=1).get("tracks", [])
    if not tracks or tracks[0].get("videoId") != video_id:
        return None
    return _track(tracks[0])


def _length_seconds(length: str | None) -> int | None:
    """'5:55' o '1:02:03' -> segundos."""
    try:
        seconds = 0
        for part in (length or "").split(":"):
            seconds = seconds * 60 + int(part)
        return seconds or None
    except ValueError:
        return None


def radio_tracks(video_id: str, limit: int = 25) -> list[dict]:
    """Canciones similares a una (la «radio» de YouTube Music), sin incluir la propia."""
    data = _client().get_watch_playlist(videoId=video_id, radio=True, limit=limit + 1)
    tracks = []
    for item in data.get("tracks", []):
        if not item.get("videoId") or item["videoId"] == video_id:
            continue
        tracks.append({
            "video_id": item["videoId"],
            "title": item.get("title", "Sin título"),
            "artist": ", ".join(a["name"] for a in item.get("artists") or []) or "Desconocido",
            "album": (item.get("album") or {}).get("name"),
            "duration": _length_seconds(item.get("length")),
            "cover": _cover_url(item.get("thumbnail")),
        })
    return tracks[:limit]
