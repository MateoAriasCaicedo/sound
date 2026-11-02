"""Rutas y configuración de Sound.

Regla de oro: los datos del usuario nunca viven dentro del directorio de instalación. En una
instalación normal (o en un .exe) van a la carpeta de datos del usuario; solo en modo portable
(`SOUND_PORTABLE=1` o un archivo `portable.txt` junto al ejecutable) se quedan junto a la app.

Todas las rutas se pueden cambiar con variables de entorno, pensadas para tests y para quien
quiera su biblioteca en otro disco.
"""
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

APP_NAME = "Sound"
APP_SLUG = "Sound"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


ROOT = Path(__file__).resolve().parents[2]  # proyecto en desarrollo, o _internal con PyInstaller
# En la app empaquetada el ejecutable está un nivel por encima de _internal; el modo portable
# debe dejar los datos junto a Sound.exe, no dentro de la carpeta interna.
APP_HOME = Path(sys.executable).resolve().parent if _is_frozen() else ROOT
PORTABLE_MARKERS = (APP_HOME / "portable.txt", ROOT / "portable.txt")
PORTABLE = os.environ.get("SOUND_PORTABLE", "").strip().lower() in {"1", "true", "yes"} or any(
    marker.exists() for marker in PORTABLE_MARKERS
)

if os.name == "nt":
    _LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    _MUSIC_HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / "Music"
    DEFAULT_DATA_DIR = APP_HOME if PORTABLE else _LOCAL / APP_SLUG
    DEFAULT_MUSIC_DIR = (APP_HOME / "Music") if PORTABLE else _MUSIC_HOME / APP_SLUG
else:
    _XDG = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    DEFAULT_DATA_DIR = APP_HOME if PORTABLE else _XDG / APP_SLUG.lower()
    DEFAULT_MUSIC_DIR = (APP_HOME / "Music") if PORTABLE else Path.home() / "Music" / APP_SLUG

DATA_DIR = _env_path("SOUND_DATA_DIR") or DEFAULT_DATA_DIR
MUSIC_DIR = _env_path("SOUND_MUSIC_DIR") or (DATA_DIR / "Music" if PORTABLE else DEFAULT_MUSIC_DIR)
DB_PATH = _env_path("SOUND_DB") or DATA_DIR / "library.db"
GENRE_CACHE = _env_path("SOUND_GENRE_CACHE") or DATA_DIR / "genres_cache.json"
LOG_PATH = _env_path("SOUND_LOG") or DATA_DIR / "sound.log"
WAVE_DIR = _env_path("SOUND_WAVE_DIR") or DATA_DIR / "cache" / "waves"
LOUDNESS_CACHE = _env_path("SOUND_LOUDNESS_CACHE") or DATA_DIR / "cache" / "loudness.json"
UI_PREFS = _env_path("SOUND_UI_PREFS") or DATA_DIR / "ui_prefs.json"
UI_SESSION = _env_path("SOUND_UI_SESSION") or DATA_DIR / "ui_session.json"
VENDOR_DIR = _env_path("SOUND_VENDOR_DIR") or DATA_DIR / "vendor"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


MAX_PARALLEL = min(8, _int_env("SOUND_PARALLEL", 3))  # descargas simultáneas
COOKIES_FROM_BROWSER = os.environ.get("SOUND_COOKIES_FROM_BROWSER", "").strip()
NEEDS_NODE = True  # YouTube exige un runtime JS; se avisa si falta


def prepare() -> list[str]:
    """Crea las carpetas que la app necesita antes de abrir el registro o la base de datos."""
    created = []
    for path in (DATA_DIR, MUSIC_DIR, WAVE_DIR, LOUDNESS_CACHE.parent, LOG_PATH.parent):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))
    return created


def legacy_paths() -> dict[str, Path]:
    """Rutas de la disposición anterior (0.x), cuando todo vivía junto al proyecto."""
    return {
        "db": ROOT / "library.db",
        "music": ROOT / "Music",
        "genres": ROOT / "genres_cache.json",
        "waves": ROOT / "cache" / "waves",
        "loudness": ROOT / "cache" / "loudness.json",
        "prefs": ROOT / "ui_prefs.json",
        "session": ROOT / "ui_session.json",
        "log": ROOT / "sound.log",
    }


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def _copy_if_missing(source: Path, target: Path, moved: list[str], failed: list[str]) -> None:
    if not source.exists() or target.exists() or _same_path(source, target):
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        moved.append(str(target))
    except OSError as exc:  # se sigue: un archivo que no se pudo copiar no bloquea el arranque
        failed.append(f"{source}: {exc}")


def _copy_database(source: Path, target: Path, moved: list[str], failed: list[str]) -> None:
    """Copia una base SQLite con su WAL mediante la API de backup (copiar el .db no basta)."""
    if not source.exists() or target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(source)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        moved.append(str(target))
    except sqlite3.Error as exc:
        failed.append(f"{source}: {exc}")
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


def _merge_move(source: Path, target: Path) -> None:
    """Mueve una carpeta; si el destino ya existe, fusiona recursivamente lo que falte.

    No pisa archivos con el mismo nombre: lo que ya está en el destino manda. Así una migración
    interrumpida se puede repetir sin perder canciones.
    """
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = target / item.name
        if dest.exists():
            if item.is_dir() and dest.is_dir():
                _merge_move(item, dest)
            continue
        shutil.move(str(item), str(dest))
    try:
        source.rmdir()
    except OSError:
        pass  # quedaron elementos que ya existían: no pasa nada


def _rewrite_db_paths(db: Path, old: Path, new: Path) -> None:
    """Actualiza rutas absolutas guardadas en SQLite cuando cambia la carpeta de música."""
    old_s, new_s = str(old), str(new)
    try:
        conn = sqlite3.connect(db, timeout=30)
    except sqlite3.Error:
        return
    try:
        for table, column in (("tracks", "path"), ("reservations", "path")):
            rows = conn.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
            for rowid, value in rows:
                if not isinstance(value, str):
                    continue
                if value.lower().startswith(old_s.lower()):
                    updated = new_s + value[len(old_s):]
                    conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                                 (updated, rowid))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()


def migrate_legacy(
    data_dir: Path | None = None,
    music_dir: Path | None = None,
    legacy: dict[str, Path] | None = None,
) -> dict[str, list[str]]:
    """Migra una instalación 0.x a las carpetas de usuario. Es idempotente y nunca borra datos.

    Devuelve `{"moved": [...], "failed": [...]}` para que quien arranque pueda dejarlo en el log.
    No hace nada si la app está en modo portable, si se cambiaron las rutas por entorno o si ya
    existe una base de datos en el destino.
    """
    report: dict[str, list[str]] = {"moved": [], "failed": []}
    if _is_frozen() or PORTABLE or os.environ.get("SOUND_NO_MIGRATE"):
        return report
    if legacy is None and (_env_path("SOUND_DATA_DIR") or _env_path("SOUND_MUSIC_DIR")):
        return report  # el usuario (o un test) eligió las rutas: no se toca nada
    data_dir = Path(data_dir or DATA_DIR)
    music_dir = Path(music_dir or MUSIC_DIR)
    old = legacy or legacy_paths()
    if _same_path(data_dir, ROOT) or _same_path(data_dir, ROOT / "data"):
        return report
    if (data_dir / "library.db").exists() and (data_dir / "migrated.json").exists():
        return report  # ya migrado

    _copy_database(old["db"], data_dir / "library.db", report["moved"], report["failed"])
    if old["db"].exists() and not (data_dir / "library.db").exists():
        return report  # no se pudo copiar la base: no se toca la música
    for key, target in (
        ("genres", data_dir / "genres_cache.json"),
        ("loudness", data_dir / "cache" / "loudness.json"),
        ("prefs", data_dir / "ui_prefs.json"),
        ("session", data_dir / "ui_session.json"),
    ):
        _copy_if_missing(old[key], target, report["moved"], report["failed"])

    old_waves, new_waves = old["waves"], data_dir / "cache" / "waves"
    if old_waves.is_dir() and not new_waves.exists():
        try:
            _merge_move(old_waves, new_waves)
            report["moved"].append(str(new_waves))
        except OSError as exc:
            report["failed"].append(f"{old_waves}: {exc}")

    if old["music"].is_dir() and not _same_path(old["music"], music_dir):
        try:
            _merge_move(old["music"], music_dir)
            report["moved"].append(str(music_dir))
            _rewrite_db_paths(data_dir / "library.db", old["music"], music_dir)
        except OSError as exc:
            report["failed"].append(f"{old['music']}: {exc}")

    if not report["failed"]:
        try:
            (data_dir / "migrated.json").write_text(
                json.dumps({"at": time.time(), "from": str(ROOT)}), encoding="utf-8")
        except OSError:
            pass
    return report


def ensure_node_path() -> str | None:
    """Añade el Node.js incluido en el instalador al PATH, si existe.

    yt-dlp lo usa como runtime JavaScript para resolver los enlaces de YouTube. En desarrollo se
    usa el Node del sistema; en la app empaquetada va dentro de la carpeta de la aplicación.
    """
    candidates = [ROOT / "node", ROOT / "packaging" / "node"]
    for folder in candidates:
        exe = folder / ("node.exe" if os.name == "nt" else "node")
        if exe.exists():
            current = os.environ.get("PATH", "")
            if str(folder) not in current.split(os.pathsep):
                os.environ["PATH"] = str(folder) + os.pathsep + current
            return str(exe)
    return None


def activate_vendor() -> str | None:
    """Carga una actualización de yt-dlp descargada por la app empaquetada, si existe.

    Se ejecuta al importar `app` (antes de que cualquier módulo importe `yt_dlp`). En desarrollo
    no hace nada: allí las actualizaciones se instalan con pip.
    """
    if not _is_frozen():
        return None
    marker = VENDOR_DIR / "current.json"
    try:
        import json
        info = json.loads(marker.read_text(encoding="utf-8"))
        path = Path(info["path"])
        if (path / "yt_dlp" / "__init__.py").exists():
            sys.path.insert(0, str(path))
            return str(info.get("version") or path)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def data_summary() -> dict[str, str]:
    """Rutas principales, para mostrarlas en diagnóstico y en el README."""
    return {
        "datos": str(DATA_DIR),
        "música": str(MUSIC_DIR),
        "base de datos": str(DB_PATH),
        "registro": str(LOG_PATH),
    }
