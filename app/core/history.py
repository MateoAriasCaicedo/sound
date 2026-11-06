"""Historial de reproducciones: qué se escuchó y cuántas veces.

Registra también lo que suena por streaming (sin estar en la biblioteca), por eso guarda título y
artista en cada reproducción. `path` solo viene si la canción sigue descargada.
"""
from contextlib import closing
from pathlib import Path

from . import library

MIN_LISTEN_S = 30  # una reproducción cuenta tras escuchar esto (o la mitad, si la canción es más corta)


def counts_as_play(listened_s: float, duration_s: float | None = None) -> bool:
    """¿Se escuchó lo bastante para contarla? Saltar una canción a los 5 s no es escucharla."""
    threshold = MIN_LISTEN_S
    if duration_s:
        threshold = min(MIN_LISTEN_S, duration_s / 2)
    return listened_s >= threshold


def record(track: dict, db_path: Path | None = None) -> None:
    with closing(library.connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO plays (video_id, title, artist) VALUES (?, ?, ?)",
            (track["video_id"], track["title"], track["artist"]),
        )


_FIELDS = (
    "p.video_id, p.title, p.artist, p.played_at AS last_played, c.last_id, c.plays, t.path"
)


def recent(limit: int = 50, db_path: Path | None = None) -> list[dict]:
    """Lo último escuchado, sin repetir canciones: la más reciente primero."""
    query = (
        f"SELECT {_FIELDS} FROM plays p"
        " JOIN (SELECT video_id, MAX(id) AS last_id, COUNT(*) AS plays"
        "       FROM plays GROUP BY video_id) c ON p.id = c.last_id"
        " LEFT JOIN tracks t ON t.video_id = p.video_id"
        " ORDER BY c.last_id DESC LIMIT ?"
    )
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(query, (limit,)).fetchall()
    return [dict(r) for r in rows]


def most_played(limit: int = 20, days: int | None = None, db_path: Path | None = None) -> list[dict]:
    """Lo más escuchado, opcionalmente solo de los últimos `days` días. Empata por recencia."""
    where, params = "", []
    if days is not None:
        where = " WHERE played_at >= datetime('now', ?)"
        params = [f"-{int(days)} days"]
    query = (
        f"SELECT {_FIELDS} FROM plays p"
        " JOIN (SELECT video_id, MAX(id) AS last_id, COUNT(*) AS plays"
        f"       FROM plays{where} GROUP BY video_id) c ON p.id = c.last_id"
        " LEFT JOIN tracks t ON t.video_id = p.video_id"
        " ORDER BY c.plays DESC, c.last_id DESC LIMIT ?"
    )
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(query, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def prune(days: int = 730, db_path: Path | None = None) -> int:
    """Borra reproducciones más antiguas que `days` días. Devuelve cuántas quitó.

    El historial no se poda solo: se ofrece como mantenimiento explícito para no borrar datos
    del usuario sin avisar.
    """
    if days < 0:
        raise ValueError("los días no pueden ser negativos")
    with closing(library.connect(db_path)) as conn, conn:
        return conn.execute(
            "DELETE FROM plays WHERE played_at < datetime('now', ?)", (f"-{int(days)} days",)
        ).rowcount


def clear(db_path: Path | None = None) -> int:
    """Borra todo el historial. Devuelve cuántas reproducciones había."""
    with closing(library.connect(db_path)) as conn, conn:
        return conn.execute("DELETE FROM plays").rowcount
