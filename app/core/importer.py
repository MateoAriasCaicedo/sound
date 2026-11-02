"""Importar un álbum, playlist, artista o canción desde una URL de YouTube Music.

Acepta enlaces normales de YouTube/Music y también los identificadores a pelo (`PL…`, `MPREb_…`,
`UC…`, un id de vídeo de 11 caracteres). No filtra versiones: si pides un álbum o una lista, se
importa tal cual; el filtro de «solo originales» es para las búsquedas por género.
"""
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from . import ytsearch

_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST = re.compile(r"^(VL)?(PL|RD|OLAK5uy_)[A-Za-z0-9_-]+$")


class ImportFailure(Exception):
    """URL no reconocida, sin canciones o fallo de YouTube. El mensaje es apto para mostrarlo."""


@dataclass(frozen=True)
class Reference:
    kind: str  # playlist | album | artist | song
    id: str


def _browse(browse_id: str) -> Reference | None:
    if browse_id.startswith("MPREb_"):
        return Reference("album", browse_id)
    if browse_id.startswith("UC"):
        return Reference("artist", browse_id)
    return None


def parse(url: str) -> Reference:
    """Convierte una URL o un id en una referencia. Lanza ImportFailure si no la reconoce."""
    text = (url or "").strip()
    if not text:
        raise ImportFailure("Falta la URL o el identificador")

    # Identificadores a pelo (sin URL): útiles para copiar de la barra de direcciones.
    # Los prefijos explícitos van antes que el id de vídeo: `OLAK5uy_abc` también mide 11.
    if text.startswith(("OLAK5uy_", "MPREb_")):
        return Reference("album", text)
    if _PLAYLIST.fullmatch(text):
        return Reference("playlist", text.removeprefix("VL"))
    if text.startswith("UC") and len(text) >= 20:
        return Reference("artist", text)
    if _VIDEO.fullmatch(text):
        return Reference("song", text)

    parsed = urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.lower().removeprefix("www.")
    query = parse_qs(parsed.query)
    path = parsed.path

    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
        if _VIDEO.fullmatch(video_id):
            return Reference("song", video_id)

    if query.get("list"):
        playlist_id = query["list"][0]
        kind = "album" if playlist_id.startswith("OLAK5uy_") else "playlist"
        return Reference(kind, playlist_id.removeprefix("VL"))

    if query.get("v"):
        return Reference("song", query["v"][0])

    if "/browse/" in path:
        ref = _browse(path.split("/browse/", 1)[1].split("/")[0])
        if ref:
            return ref
    if "/channel/" in path:
        channel_id = path.split("/channel/", 1)[1].split("/")[0]
        if channel_id.startswith("UC"):
            return Reference("artist", channel_id)

    raise ImportFailure("No reconozco esa URL. Pega un enlace de álbum, playlist, artista o canción "
                        "de YouTube Music.")


def _dedupe(tracks: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for track in tracks:
        video_id = track.get("video_id")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        out.append(track)
    return out


def fetch(url: str, limit: int = 200) -> tuple[str, list[dict]]:
    """Devuelve (etiqueta, canciones) de la URL. Lanza ImportFailure si no hay nada."""
    ref = parse(url)
    try:
        if ref.kind == "playlist":
            label, tracks = ytsearch.playlist_details(ref.id, limit)
        elif ref.kind == "album":
            browse_id = ytsearch.album_browse_id(ref.id) if ref.id.startswith("OLAK5uy_") else ref.id
            if not browse_id:
                raise ImportFailure("YouTube no devolvió los datos de ese álbum")
            label, tracks = ytsearch.album_details(browse_id, limit)
        elif ref.kind == "artist":
            label, tracks = ytsearch.artist_details(ref.id, limit)
        else:
            track = ytsearch.song_track(ref.id)
            if not track:
                raise ImportFailure("No encontré esa canción en YouTube Music")
            label = f"{track.get('artist') or 'Artista'} - {track.get('title') or 'Canción'}"
            tracks = [track]
    except ImportFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - se muestra un mensaje corto en la interfaz
        raise ImportFailure(f"No se pudo leer la lista en YouTube Music: {exc}") from exc

    tracks = _dedupe(tracks)[:limit]
    if not tracks:
        raise ImportFailure(f"'{label}' no tiene canciones disponibles")
    return label, tracks
