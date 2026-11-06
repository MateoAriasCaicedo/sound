"""Preferencias de la ventana (ui_prefs.json, en la carpeta de datos)."""
import json
from pathlib import Path

from ..core import log as _log
from ..core.atomic import write_text_atomic
from ..core.config import UI_PREFS

log = _log.get("ui.prefs")
PATH = Path(UI_PREFS)


def load(path: Path | None = None) -> dict:
    path = Path(path or PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("preferencias ilegibles en %s: se usan las de fábrica", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict, path: Path | None = None) -> None:
    path = Path(path or PATH)
    try:
        write_text_atomic(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        log.warning("no se pudieron guardar las preferencias en %s", path, exc_info=True)
