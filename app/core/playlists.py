"""Listas de reproducción propias («Para entrenar», «Domingo»…).

Viven en la base de datos, sin tocar los archivos. Cada canción guarda su título, artista,
álbum y duración, así una lista puede tener canciones que aún no están descargadas (suenan por
streaming); si la canción está en la biblioteca, se muestran sus datos actuales. Una canción
aparece una sola vez en cada lista, en la posición que le des.
"""
from contextlib import closing
from pathlib import Path

from . import library

_TRACKS = (
    "SELECT pt.video_id, COALESCE(t.title, pt.title) AS title,"
    " COALESCE(t.artist, pt.artist) AS artist, COALESCE(t.album, pt.album) AS album,"
    " t.genre, COALESCE(t.duration, pt.duration) AS duration, t.path, t.year, t.track_no,"
    " t.added_at, pt.position"
    " FROM playlist_tracks pt LEFT JOIN tracks t ON t.video_id = pt.video_id"
    " WHERE pt.playlist_id = ? ORDER BY pt.position"
)


def unique_name(base: str = "Nueva lista", db_path: Path | None = None) -> str:
    """`base`, o «base 2», «base 3»… si ya hay una lista con ese nombre."""
    names = {p["name"].casefold() for p in all_playlists(db_path)}
    if base.casefold() not in names:
        return base
    n = 2
    while f"{base} {n}".casefold() in names:
        n += 1
    return f"{base} {n}"


def create(name: str, db_path: Path | None = None) -> int:
    name = name.strip() or unique_name(db_path=db_path)
    with closing(library.connect(db_path)) as conn, conn:
        return conn.execute("INSERT INTO playlists (name) VALUES (?)", (name,)).lastrowid


def rename(playlist_id: int, name: str, db_path: Path | None = None) -> bool:
    name = name.strip()
    if not name:
        return False
    with closing(library.connect(db_path)) as conn, conn:
        return conn.execute("UPDATE playlists SET name = ? WHERE id = ?",
                            (name, playlist_id)).rowcount > 0


def delete(playlist_id: int, db_path: Path | None = None) -> None:
    with closing(library.connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))


def all_playlists(db_path: Path | None = None) -> list[dict]:
    """Las listas por orden de creación, con cuántas canciones tienen y lo que duran."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT p.id, p.name, COUNT(pt.video_id) AS count,"
            " COALESCE(SUM(COALESCE(t.duration, pt.duration)), 0) AS duration"
            " FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id"
            " LEFT JOIN tracks t ON t.video_id = pt.video_id"
            " GROUP BY p.id ORDER BY p.id").fetchall()
    return [dict(r) for r in rows]


def get(playlist_id: int, db_path: Path | None = None) -> dict | None:
    return next((p for p in all_playlists(db_path) if p["id"] == playlist_id), None)


def tracks(playlist_id: int, db_path: Path | None = None) -> list[dict]:
    """Las canciones de una lista en su orden. `path` es None si no están descargadas."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute(_TRACKS, (playlist_id,)).fetchall()
    out = []
    for row in rows:
        track = dict(row)
        track.pop("position", None)
        if not track.get("path"):
            track.pop("path", None)  # como los resultados de YouTube: sin ruta = por streaming
        out.append(track)
    return out


def add(playlist_id: int, items: list[dict], db_path: Path | None = None) -> int:
    """Añade al final las canciones que aún no estén. Devuelve cuántas añadió."""
    added = 0
    with closing(library.connect(db_path)) as conn, conn:
        last = conn.execute("SELECT COALESCE(MAX(position), -1) FROM playlist_tracks"
                            " WHERE playlist_id = ?", (playlist_id,)).fetchone()[0]
        for t in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO playlist_tracks"
                " (playlist_id, video_id, position, title, artist, album, duration)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (playlist_id, t["video_id"], last + 1, t.get("title") or t["video_id"],
                 t.get("artist") or "", t.get("album"), t.get("duration")))
            if cur.rowcount:
                last += 1
                added += 1
    return added


def remove(playlist_id: int, video_ids: list[str], db_path: Path | None = None) -> int:
    with closing(library.connect(db_path)) as conn, conn:
        removed = conn.executemany(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?",
            [(playlist_id, vid) for vid in video_ids]).rowcount
    return removed


def reorder(playlist_id: int, video_ids: list[str], db_path: Path | None = None) -> None:
    """Deja las canciones en el orden dado; las que no aparezcan van detrás, como estaban."""
    current = [t["video_id"] for t in tracks(playlist_id, db_path)]
    wanted = [vid for vid in dict.fromkeys(video_ids) if vid in set(current)]
    order = wanted + [vid for vid in current if vid not in set(wanted)]
    with closing(library.connect(db_path)) as conn, conn:
        conn.executemany("UPDATE playlist_tracks SET position = ? WHERE playlist_id = ?"
                         " AND video_id = ?",
                         [(i, playlist_id, vid) for i, vid in enumerate(order)])


def moved(order: list[str], picked: set[str], delta: int) -> list[str]:
    """El orden tras subir (delta -1) o bajar (+1) las canciones elegidas un puesto.

    Las elegidas se mueven juntas y sin saltarse unas a otras; si una ya está en el borde, el
    bloque se queda donde está.
    """
    order = list(order)
    indices = [i for i, vid in enumerate(order) if vid in picked]
    if not indices or delta == 0:
        return order
    if (delta < 0 and indices[0] == 0) or (delta > 0 and indices[-1] == len(order) - 1):
        return order
    for i in (indices if delta < 0 else reversed(indices)):
        j = i + delta
        order[i], order[j] = order[j], order[i]
    return order


def containing(video_ids: list[str], db_path: Path | None = None) -> set[int]:
    """Listas que ya tienen todas esas canciones (para marcarlas en el menú «Añadir a lista»)."""
    if not video_ids:
        return set()
    wanted = set(video_ids)
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute("SELECT playlist_id, video_id FROM playlist_tracks").fetchall()
    have: dict[int, set[str]] = {}
    for row in rows:
        if row["video_id"] in wanted:
            have.setdefault(row["playlist_id"], set()).add(row["video_id"])
    return {pid for pid, vids in have.items() if vids >= wanted}
