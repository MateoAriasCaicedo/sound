"""Canciones marcadas «Me gusta»: una tabla aparte, sin tocar los archivos.

Solo se pueden marcar canciones de la biblioteca (descargadas). Si una se borra de la biblioteca,
su «me gusta» se conserva: si la vuelves a descargar, sigue marcada. Las canciones que solo
suenan por streaming no se guardan aquí.
"""
from contextlib import closing
from pathlib import Path

from . import library

_SELECT = (
    "SELECT t.video_id, t.title, t.artist, t.album, t.genre, t.duration, t.path"
    " FROM likes l JOIN tracks t ON t.video_id = l.video_id"
)


def _in_library(conn, video_id: str) -> bool:
    return conn.execute("SELECT 1 FROM tracks WHERE video_id = ?", (video_id,)).fetchone() is not None


def set_liked(video_id: str, liked: bool = True, db_path: Path | None = None) -> bool:
    """Marca o desmarca una canción. Devuelve el estado final.

    False también si la canción no está en la biblioteca: no se guardan «me gusta» de canciones
    que solo existen en YouTube.
    """
    with closing(library.connect(db_path)) as conn, conn:
        if not _in_library(conn, video_id):
            return False
        if liked:
            conn.execute("INSERT OR IGNORE INTO likes (video_id) VALUES (?)", (video_id,))
        else:
            conn.execute("DELETE FROM likes WHERE video_id = ?", (video_id,))
    return bool(liked)


def toggle(video_id: str, db_path: Path | None = None) -> bool:
    """Invierte el «me gusta» y devuelve el estado nuevo. False si no está en la biblioteca."""
    with closing(library.connect(db_path)) as conn, conn:
        if not _in_library(conn, video_id):
            return False
        removed = conn.execute("DELETE FROM likes WHERE video_id = ?", (video_id,)).rowcount
        if removed:
            return False
        conn.execute("INSERT INTO likes (video_id) VALUES (?)", (video_id,))
        return True


def liked_ids(db_path: Path | None = None) -> set[str]:
    """Ids de las canciones de la biblioteca marcadas con «me gusta»."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT l.video_id FROM likes l JOIN tracks t ON t.video_id = l.video_id"
        ).fetchall()
    return {row["video_id"] for row in rows}


def liked_tracks(db_path: Path | None = None) -> list[dict]:
    """Las canciones marcadas, la última en marcarse primero. Mismos campos que library.all_tracks."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(f"{_SELECT} ORDER BY l.rowid DESC").fetchall()
    return [dict(row) for row in rows]


def count(db_path: Path | None = None) -> int:
    """Cuántas canciones de la biblioteca están marcadas."""
    with closing(library.connect(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM likes l JOIN tracks t ON t.video_id = l.video_id"
        ).fetchone()[0]
