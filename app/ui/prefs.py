"""Preferencias de la ventana (ui_prefs.json, junto a la base de datos)."""
import json
from pathlib import Path

from ..core import log as _log
from ..core.config import DB_PATH

log = _log.get("ui.prefs")
PATH = Path(DB_PATH).with_name("ui_prefs.json")


def load(path: Path = PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("preferencias ilegibles en %s: se usan las de fábrica", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict, path: Path = PATH) -> None:
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        log.warning("no se pudieron guardar las preferencias en %s", path, exc_info=True)
