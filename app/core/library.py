import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from . import log as _log
from .config import DB_PATH
from .filters import fold

log = _log.get("library")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    genre TEXT,
    duration INTEGER,
    path TEXT NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS queue (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration INTEGER,
    cover TEXT,
    genre TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reservations (
    path TEXT PRIMARY KEY,  -- nombre de archivo en minúsculas: Windows no distingue mayúsculas
    video_id TEXT NOT NULL
);
-- «Me gusta»: sin clave foránea a tracks para que el favorito sobreviva si borras y vuelves a
-- descargar la canción. Las canciones que no estén en la biblioteca no se muestran.
CREATE TABLE IF NOT EXISTS likes (
    video_id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Historial: una fila por reproducción. Guarda título y artista porque también registra lo que
-- suena por streaming, que no está en `tracks`.
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    played_at TEXT DEFAULT CURRENT_TIMESTAMP  -- UTC, 'AAAA-MM-DD HH:MM:SS'
);
CREATE INDEX IF NOT EXISTS plays_by_video ON plays (video_id);
CREATE INDEX IF NOT EXISTS plays_by_time ON plays (played_at);
CREATE INDEX IF NOT EXISTS tracks_by_artist ON tracks (artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS tracks_by_title ON tracks (title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS tracks_by_genre ON tracks (genre COLLATE NOCASE);
"""

QUEUE_FIELDS = ("video_id", "title", "artist", "album", "duration", "cover", "genre")
SCHEMA_VERSION = 2  # súbelo al añadir columnas o tablas: antes se guarda una copia de seguridad
# Columnas añadidas después de la primera versión: se agregan a bases de datos ya existentes.
_ADDED_COLUMNS = {
    "tracks": {"year": "INTEGER", "has_cover": "INTEGER"},
    "queue": {"priority": "INTEGER NOT NULL DEFAULT 0"},  # más alto se descarga antes
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in present:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError:
                    pass  # otro hilo o proceso la añadió justo antes ('duplicate column')


_ready: set[str] = set()  # bases de datos ya preparadas (esquema, migración, WAL) en este proceso
_ready_lock = threading.Lock()


def _backup(conn: sqlite3.Connection, path: Path, version: int) -> Path | None:
    """Copia de seguridad antes de migrar una base de datos existente.

    Se usa la API de backup de SQLite (no copiar el archivo a mano): con WAL, una copia
    ingenua del .db puede perder las últimas transacciones.
    """
    target = path.with_name(f"{path.stem}.v{version}.bak.db")
    if target.exists():
        return target
    try:
        dst = sqlite3.connect(target)
        try:
            conn.backup(dst)
        finally:
            dst.close()
    except sqlite3.Error:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return target


def _prepare(conn: sqlite3.Connection, path: Path, existed: bool) -> None:
    """Esquema, migraciones y modo WAL: una sola vez por base de datos y proceso.

    WAL deja que la ventana lea mientras los trabajadores escriben (y al revés) sin bloquearse;
    es una propiedad del archivo, así que basta con activarlo una vez. Antes de migrar se guarda
    una copia, porque la biblioteca, el historial y los «me gusta» no se pueden reconstruir.
    """
    key = str(path)
    with _ready_lock:
        if key in _ready and existed:  # si borraron el archivo, hay que prepararlo otra vez
            return
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if existed and version < SCHEMA_VERSION:
            backup = _backup(conn, path, version)
            if backup is not None:
                log.info("copia de seguridad de la base de datos: %s", backup)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()  # también confirma columnas añadidas si la versión ya era la actual
        _ready.add(key)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)  # DB_PATH se lee al llamar, no al importar
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()  # antes de conectar: sqlite3 crea el archivo vacío al abrirlo
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")  # por conexión: las referencias se respetan siempre
    _prepare(conn, path, existed)
    return conn


def all_tracks(db_path: Path | None = None) -> list[dict]:
    """Toda la biblioteca descargada, ordenada por artista y título."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT video_id, title, artist, album, genre, duration, path FROM tracks"
            " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def get_track(video_id: str, db_path: Path | None = None) -> dict | None:
    """Fila completa de una canción descargada, o None si no está en la biblioteca."""
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT video_id, title, artist, album, genre, duration, path, year, has_cover,"
            " added_at FROM tracks WHERE video_id = ?", (video_id,)
        ).fetchone()
    return dict(row) if row else None


def genre_label(genre: str, db_path: Path | None = None) -> str:
    """El texto que ya usa la biblioteca para ese género, para no crear 'salsa' y 'Salsa'."""
    wanted = fold(genre)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT genre FROM tracks WHERE genre IS NOT NULL AND trim(genre) != ''"
        ).fetchall()
    return next((row[0] for row in rows if fold(row[0]) == wanted), genre)


def missing_files(db_path: Path | None = None) -> list[dict]:
    """Canciones registradas cuyo archivo ya no está en disco."""
    return [t for t in all_tracks(db_path) if not Path(t["path"]).exists()]


def remove_missing(db_path: Path | None = None) -> int:
    """Quita de la biblioteca las canciones cuyo archivo ya no existe. Devuelve cuántas."""
    missing = missing_files(db_path)
    for track in missing:
        remove_track(track["video_id"], db_path)
    return len(missing)


def export_m3u(dest: Path, db_path: Path | None = None) -> int:
    """Escribe una lista M3U extendida con las canciones que siguen en disco.

    Las rutas son absolutas: el archivo se puede abrir desde cualquier reproductor. Devuelve
    cuántas canciones se escribieron.
    """
    tracks = [t for t in all_tracks(db_path) if Path(t["path"]).exists()]
    lines = ["#EXTM3U"]
    for track in tracks:
        duration = int(track.get("duration") or -1)
        lines.append(f"#EXTINF:{duration},{track['artist']} - {track['title']}")
        lines.append(str(Path(track["path"]).resolve()))
    Path(dest).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(tracks)


def find_tracks(text: str, db_path: Path | None = None) -> list[dict]:
    """Canciones de la biblioteca por video_id exacto o por texto en título, artista o álbum.

    Sin distinguir mayúsculas ni tildes. Si `text` es un video_id existente, solo devuelve esa.
    El filtro se hace en SQLite (con una función de normalización) para no traer toda la tabla
    a Python cuando la biblioteca es grande.
    """
    columns = "video_id, title, artist, album, genre, duration, path"
    with closing(connect(db_path)) as conn:
        exact = conn.execute(
            f"SELECT {columns} FROM tracks WHERE video_id = ?", (text,)
        ).fetchall()
        if exact:
            return [dict(row) for row in exact]
        conn.create_function(
            "sound_fold", 1, lambda value: fold(str(value or "")), deterministic=True
        )
        wanted = fold(text)
        rows = conn.execute(
            f"SELECT {columns} FROM tracks"
            " WHERE instr(sound_fold(title || ' ' || artist || ' ' || coalesce(album, '')), ?) > 0"
            " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE",
            (wanted,),
        ).fetchall()
    return [dict(row) for row in rows]


def without_cover(db_path: Path | None = None) -> list[dict]:
    """Canciones descargadas cuya carátula no se pudo incrustar (has_cover = 0)."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT video_id, artist, title FROM tracks WHERE has_cover = 0 ORDER BY rowid"
        ).fetchall()
    return [dict(r) for r in rows]


def enqueue(tracks: list[dict], genre: str | None, priority: int = 0,
            db_path: Path | None = None) -> int:
    """Encola canciones nuevas y devuelve cuántas lo eran.

    Las que ya están en la cola se ignoran, salvo que sigan pendientes y ahora lleguen con más
    prioridad (p. ej. la precarga de lo que sigue en la reproducción), que las adelanta. Una
    canción 'done' cuya fila en `tracks` ya no existe (se borró) vuelve a ser descargable.
    """
    ids = [t["video_id"] for t in tracks]
    with closing(connect(db_path)) as conn, conn:
        existing = {row[0] for row in conn.execute("SELECT video_id FROM queue")}
        conn.executemany(
            "INSERT INTO queue (video_id, title, artist, album, duration, cover, genre, priority)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(video_id) DO UPDATE SET priority = MAX(priority, excluded.priority)"
            " WHERE status = 'pending'",
            [
                (t["video_id"], t["title"], t["artist"], t.get("album"), t.get("duration"),
                 t.get("cover"), genre, priority)
                for t in tracks
            ],
        )
        requeued = conn.executemany(
            "UPDATE queue SET status = 'pending', error = NULL, priority = ?"
            " WHERE video_id = ? AND status = 'done'"
            " AND video_id NOT IN (SELECT video_id FROM tracks)",
            [(priority, video_id) for video_id in ids],
        ).rowcount
        return len(set(ids) - existing) + requeued


def pending(db_path: Path | None = None) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY priority DESC, rowid"
        ).fetchall()
    return [dict(r) for r in rows]


def claim_next(db_path: Path | None = None) -> dict | None:
    """Toma la siguiente canción pendiente y la marca 'running', de forma atómica.

    Varios hilos o procesos (la ventana y la CLI) pueden llamarla a la vez: cada canción la
    recibe exactamente uno, porque la selección y el cambio de estado son una sola sentencia.
    """
    with closing(connect(db_path)) as conn, conn:
        row = conn.execute(
            "UPDATE queue SET status = 'running', updated_at = CURRENT_TIMESTAMP"
            " WHERE video_id = (SELECT video_id FROM queue WHERE status = 'pending'"
            "                   ORDER BY priority DESC, rowid LIMIT 1)"
            " RETURNING *"
        ).fetchone()
    return dict(row) if row else None


def unclaim(video_id: str, db_path: Path | None = None) -> None:
    """Devuelve a 'pending' una canción reclamada que no llegó a empezar (p. ej. se pausó justo)."""
    with closing(connect(db_path)) as conn, conn:
        conn.execute("UPDATE queue SET status = 'pending' WHERE video_id = ? AND status = 'running'", (video_id,))


def claim(track: dict, genre: str | None, db_path: Path | None = None) -> bool:
    """Toma una canción concreta para descargarla ya, fuera de turno (p. ej. al reproducirla).

    False si otro la está descargando en este momento. Si estaba pendiente, fallida o 'done' pero
    sin archivo (quien llama ya comprobó que no existe), pasa a 'running' y es del que llama.
    """
    with closing(connect(db_path)) as conn, conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO queue (video_id, title, artist, album, duration, cover, genre, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'running')",
            (track["video_id"], track["title"], track["artist"], track.get("album"),
             track.get("duration"), track.get("cover"), genre),
        ).rowcount
        if inserted:
            return True
        return conn.execute(
            "UPDATE queue SET status = 'running', error = NULL, updated_at = CURRENT_TIMESTAMP"
            " WHERE video_id = ? AND status IN ('pending', 'failed', 'done')",
            (track["video_id"],),
        ).rowcount > 0


def status_of(video_id: str, db_path: Path | None = None) -> tuple[str, str | None] | None:
    """(estado, error) de una canción en la cola, o None si no está."""
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT status, error FROM queue WHERE video_id = ?", (video_id,)).fetchone()
    return (row["status"], row["error"]) if row else None


def recover_running(db_path: Path | None = None) -> int:
    """Devuelve a pendientes lo que quedó 'running' de una sesión que se cayó o se cerró.

    También borra las reservas de nombres de archivo: quien las hizo ya no existe.
    """
    with closing(connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM reservations")
        return conn.execute("UPDATE queue SET status = 'pending' WHERE status = 'running'").rowcount


def reserve(path: Path, video_id: str, db_path: Path | None = None) -> bool:
    """Reserva un nombre de archivo para una canción antes de descargarla.

    Sin esto, dos trabajadores (o dos procesos) con canciones distintas que se llaman igual
    elegirían el mismo nombre a la vez, porque ninguno está aún registrado en `tracks`, y una
    pisaría a la otra. True si el nombre es de esta canción (nuevo o reservado antes por ella).
    """
    key = str(path).lower()
    with closing(connect(db_path)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO reservations (path, video_id) VALUES (?, ?)", (key, video_id))
        row = conn.execute("SELECT video_id FROM reservations WHERE path = ?", (key,)).fetchone()
    return row["video_id"] == video_id


def release(video_id: str, db_path: Path | None = None) -> None:
    """Libera las reservas de una canción (ya quedó registrada en `tracks`, o falló)."""
    with closing(connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM reservations WHERE video_id = ?", (video_id,))


def mark(video_id: str, status: str, attempts: int, error: str | None = None,
         db_path: Path | None = None) -> None:
    with closing(connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE queue SET status = ?, attempts = attempts + ?, error = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE video_id = ?",
            (status, attempts, error, video_id),
        )


def retry_failed(db_path: Path | None = None) -> int:
    """Devuelve las fallidas a pendientes; devuelve cuántas eran."""
    with closing(connect(db_path)) as conn, conn:
        return conn.execute(
            "UPDATE queue SET status = 'pending', error = NULL WHERE status = 'failed'"
        ).rowcount


def requeue(video_ids: list[str], db_path: Path | None = None) -> int:
    """Vuelve a poner en la cola canciones ya terminadas o fallidas.

    Se usa cuando un archivo local resulta incompleto: sigue en `tracks`, así que `enqueue` no
    lo reencolaría, pero el trabajador lo apartará como dañado al volver a descargarlo.
    """
    with closing(connect(db_path)) as conn, conn:
        return conn.executemany(
            "UPDATE queue SET status = 'pending', error = NULL"
            " WHERE video_id = ? AND status IN ('done', 'failed')",
            [(video_id,) for video_id in video_ids],
        ).rowcount


def prioritize(video_ids: list[str], db_path: Path | None = None) -> int:
    """Pone canciones al frente de la cola, conservando el orden en que se dan.

    Pasan por delante de todo lo demás, incluido el respaldo automático. Una fallida además vuelve
    a pendiente: es la forma de reintentar una sola. Las que ya se están bajando (o terminaron) no
    cambian. Devuelve cuántas cambiaron.
    """
    with closing(connect(db_path)) as conn, conn:
        top = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM queue").fetchone()[0]
        changed = 0
        for offset, video_id in enumerate(video_ids):
            changed += conn.execute(
                "UPDATE queue SET status = 'pending', error = NULL, priority = ?"
                " WHERE video_id = ? AND status IN ('pending', 'failed')",
                (top + len(video_ids) - offset, video_id),
            ).rowcount
    return changed


def remove_from_queue(video_ids: list[str], db_path: Path | None = None) -> int:
    """Quita de la cola canciones pendientes o fallidas. Devuelve cuántas quitó.

    Las que ya se están bajando no se tocan: yt-dlp no se puede cortar a mitad sin dejar el archivo
    corrupto. Si otro trabajador la reclama a la vez, gana quien llegue primero a la base de datos.
    Una canción quitada vuelve a poder sugerirse y descargarse.
    """
    with closing(connect(db_path)) as conn, conn:
        return conn.executemany(
            "DELETE FROM queue WHERE video_id = ? AND status IN ('pending', 'failed')",
            [(video_id,) for video_id in video_ids],
        ).rowcount


def queue_counts(db_path: Path | None = None) -> dict[str, int]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM queue GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def failed(db_path: Path | None = None) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM queue WHERE status = 'failed' ORDER BY rowid").fetchall()
    return [dict(r) for r in rows]


def queue_rows(limit: int = 300, db_path: Path | None = None) -> list[dict]:
    """La cola para mostrarla: lo que se baja ahora, lo pendiente por orden de turno y las fallidas.

    Las 'done' no salen: ya están en la biblioteca.
    """
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT video_id, artist, title, status, attempts, error, priority FROM queue"
            " WHERE status != 'done'"
            " ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,"
            "          priority DESC, rowid LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_path(video_id: str, db_path: Path | None = None) -> Path | None:
    """Ruta del archivo si la canción ya está descargada y sigue existiendo en disco."""
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT path FROM tracks WHERE video_id = ?", (video_id,)
        ).fetchone()
    return Path(row[0]) if row and Path(row[0]).exists() else None


def owner(path: Path, db_path: Path | None = None) -> str | None:
    """video_id al que pertenece un archivo ya registrado (sin distinguir mayúsculas, como Windows)."""
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT video_id FROM tracks WHERE lower(path) = lower(?)", (str(path),)
        ).fetchone()
    return row["video_id"] if row else None


def known(include_queue: bool = True, db_path: Path | None = None) -> list[tuple[str, str, str]]:
    """(video_id, artista, título) de todo lo descargado y, si include_queue, también lo aún en cola.

    Un 'done' sin fila en `tracks` no cuenta: esa canción se borró y debe poder volver a sugerirse.
    """
    query = "SELECT video_id, artist, title FROM tracks"
    if include_queue:
        query += " UNION SELECT video_id, artist, title FROM queue WHERE status != 'done'"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(query).fetchall()
    return [(r["video_id"], r["artist"], r["title"]) for r in rows]


def remove_track(video_id: str, db_path: Path | None = None) -> None:
    """Quita la canción del índice (el archivo lo borra quien llama).

    También borra su fila de la cola: si no, quedaría como 'done' y esa canción no volvería a
    sugerirse ni a poder descargarse.
    """
    with closing(connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM tracks WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM queue WHERE video_id = ? AND status = 'done'", (video_id,))


def add_track(track: dict, path: Path, db_path: Path | None = None) -> None:
    with closing(connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO tracks"
            " (video_id, title, artist, album, genre, duration, path, year, has_cover)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                track["video_id"], track["title"], track["artist"], track.get("album"),
                track.get("genre"), track.get("duration"), str(path), track.get("year"),
                None if track.get("has_cover") is None else int(track["has_cover"]),
            ),
        )
