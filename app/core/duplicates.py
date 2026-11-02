"""Canciones repetidas en la biblioteca: la misma grabación con distinto video_id.

Típico: la versión del álbum y el videoclip de la misma canción. Se agrupan por (artista
principal, título base) y además deben durar casi lo mismo: así 'Intro (Parte 1)' y
'Intro (Parte 2)', que comparten título base, no se toman por la misma.
"""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from . import filters, library
from . import log as _log

log = _log.get("duplicates")

DURATION_SLACK_S = 5
DURATION_SLACK_PCT = 0.03


@dataclass
class Group:
    keep: dict         # la que conviene conservar
    extras: list[dict]  # las repetidas


def _same_length(a: dict, b: dict) -> bool:
    da, db = a.get("duration"), b.get("duration")
    if not da or not db:
        return True  # sin duración no se puede descartar
    return abs(da - db) <= max(DURATION_SLACK_S, max(da, db) * DURATION_SLACK_PCT)


def _quality(track: dict) -> tuple:
    """Mejor primero: con carátula, con álbum, con año, y la más antigua (la primera que llegó)."""
    return (not track.get("has_cover"), not track.get("album"), not track.get("year"),
            track.get("added_at") or "", track["video_id"])


def find(db_path: Path | None = None) -> list[Group]:
    """Grupos de canciones repetidas (de a dos o más), cada uno con la que conservar."""
    with closing(library.connect(db_path)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT video_id, title, artist, album, genre, duration, path, year, has_cover, added_at"
            " FROM tracks")]
    by_key: dict[tuple[str, str], list[dict]] = {}
    for track in rows:
        by_key.setdefault(filters.song_key(track["artist"], track["title"]), []).append(track)

    groups = []
    for members in by_key.values():
        if len(members) < 2:
            continue
        members.sort(key=_quality)
        remaining = members
        while remaining:  # varias grabaciones de distinta duración bajo el mismo título: grupos aparte
            keep = remaining[0]
            same = [t for t in remaining[1:] if _same_length(keep, t)]
            if same:
                groups.append(Group(keep, same))
            remaining = [t for t in remaining[1:] if t not in same]
    return sorted(groups, key=lambda g: filters.song_key(g.keep["artist"], g.keep["title"]))


def remove_extras(groups: list[Group], db_path: Path | None = None) -> tuple[int, list[tuple[dict, str]]]:
    """Borra del disco y de la biblioteca las repetidas, dejando la que se conserva de cada grupo.

    Devuelve (cuántas borró, [(canción, motivo)] de las que no pudo borrar). Si el archivo no se
    puede borrar (p. ej. está sonando), la canción se queda en la biblioteca: nunca queda una
    entrada apuntando a un archivo que sigue ahí sin que lo sepa la base de datos.
    """
    removed, failed = 0, []
    for group in groups:
        for track in group.extras:
            try:
                Path(track["path"]).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("no se pudo borrar %s", track["path"], exc_info=True)
                failed.append((track, str(exc)))
                continue
            library.remove_track(track["video_id"], db_path)
            removed += 1
    return removed, failed
