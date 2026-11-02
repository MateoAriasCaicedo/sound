"""Playlists propias: listas ordenadas de canciones de la biblioteca, guardadas en SQLite.

Una playlist solo contiene canciones descargadas. Si una se borra de la biblioteca, desaparece
también de las listas (library.remove_track); si se borra la playlist, las canciones no se tocan.
"""
import sqlite3
from contextlib import closing
from pathlib import Path

from . import library


class PlaylistError(Exception):
    """Nombre vacío o repetido, o playlist inexistente. El mensaje es apto para mostrarlo."""


def _id(conn: sqlite3.Connection, ref: int | str) -> int:
    if isinstance(ref, int):
        row = conn.execute("SELECT id FROM playlists WHERE id = ?", (ref,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM playlists WHERE name = ?", (ref.strip(),)).fetchone()
    if row is None:
        raise PlaylistError(f"no existe la playlist '{ref}'")
    return row["id"]


def _clean(name: str) -> str:
    name = name.strip()
    if not name:
        raise PlaylistError("el nombre de la playlist no puede estar vacío")
    return name


def create(name: str, db_path: Path | None = None) -> int:
    """Crea una playlist vacía y devuelve su id. El nombre no distingue mayúsculas."""
    name = _clean(name)
    try:
        with closing(library.connect(db_path)) as conn, conn:
            return conn.execute("INSERT INTO playlists (name) VALUES (?)", (name,)).lastrowid
    except sqlite3.IntegrityError:
        raise PlaylistError(f"ya existe una playlist llamada '{name}'") from None


def rename(ref: int | str, new_name: str, db_path: Path | None = None) -> None:
    new_name = _clean(new_name)
    try:
        with closing(library.connect(db_path)) as conn, conn:
            conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (new_name, _id(conn, ref)))
    except sqlite3.IntegrityError:
        raise PlaylistError(f"ya existe una playlist llamada '{new_name}'") from None


def delete(ref: int | str, db_path: Path | None = None) -> None:
    """Borra la playlist (no las canciones)."""
    with closing(library.connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM playlists WHERE id = ?", (_id(conn, ref),))


def all_lists(db_path: Path | None = None) -> list[dict]:
    """Todas las playlists por nombre, con cuántas canciones tiene cada una."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT p.id, p.name, p.created_at, COUNT(t.video_id) AS tracks FROM playlists p"
            " LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id"
            " LEFT JOIN tracks t ON t.video_id = pt.video_id"
            " GROUP BY p.id ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def add(ref: int | str, video_ids: list[str], db_path: Path | None = None) -> int:
    """Añade al final las canciones de la biblioteca que aún no estén. Devuelve cuántas añadió.

    Se ignoran los ids que no están en la biblioteca y los que ya estaban en la lista.
    """
    with closing(library.connect(db_path)) as conn, conn:
        playlist = _id(conn, ref)
        position = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM playlist_tracks WHERE playlist_id = ?", (playlist,)
        ).fetchone()[0]
        added = 0
        for video_id in dict.fromkeys(video_ids):  # sin repetidos, en el orden dado
            inserted = conn.execute(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, video_id, position)"
                " SELECT ?, video_id, ? FROM tracks WHERE video_id = ?",
                (playlist, position + 1, video_id),
            ).rowcount
            position += inserted
            added += inserted
        return added


def remove(ref: int | str, video_ids: list[str], db_path: Path | None = None) -> int:
    """Quita canciones de la playlist (siguen en la biblioteca). Devuelve cuántas quitó."""
    with closing(library.connect(db_path)) as conn, conn:
        playlist = _id(conn, ref)
        return conn.executemany(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?",
            [(playlist, video_id) for video_id in video_ids],
        ).rowcount


def tracks(ref: int | str, db_path: Path | None = None) -> list[dict]:
    """Las canciones de la playlist en su orden, con los mismos campos que library.all_tracks."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT t.video_id, t.title, t.artist, t.album, t.genre, t.duration, t.path"
            " FROM playlist_tracks pt JOIN tracks t ON t.video_id = pt.video_id"
            " WHERE pt.playlist_id = ? ORDER BY pt.position",
            (_id(conn, ref),),
        ).fetchall()
    return [dict(r) for r in rows]


def move(ref: int | str, video_id: str, new_index: int, db_path: Path | None = None) -> None:
    """Cambia una canción de sitio dentro de la playlist (0 = la primera)."""
    with closing(library.connect(db_path)) as conn, conn:
        playlist = _id(conn, ref)
        ids = [r["video_id"] for r in conn.execute(
            "SELECT video_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist,))]
        if video_id not in ids:
            raise PlaylistError("esa canción no está en la playlist")
        ids.remove(video_id)
        ids.insert(max(0, min(new_index, len(ids))), video_id)
        conn.executemany(
            "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND video_id = ?",
            [(pos, playlist, vid) for pos, vid in enumerate(ids, 1)],
        )
