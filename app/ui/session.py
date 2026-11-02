"""Sesión de reproducción: qué sonaba, en qué punto y qué venía después.

Se guarda junto a las preferencias (`ui_session.json`). No reproduce nada al abrir: la ventana
recupera el estado y espera a que pulses play. Así cerrar la aplicación no arranca música sola.
"""
import json
from pathlib import Path

from ..core import log as _log
from ..core.atomic import write_text_atomic
from ..core.config import UI_SESSION

log = _log.get("ui.session")
PATH = Path(UI_SESSION)
MAX_TRACKS = 500  # tope defensivo: una radio o una playlist enorme no debe hinchar el archivo
TRACK_KEYS = ("video_id", "title", "artist", "album", "genre", "duration", "path", "cover",
              "year", "_genre")
_SCALARS = (str, int, float, bool, type(None))


def _track(value) -> dict | None:
    """Copia serializable de una canción; None si no tiene lo mínimo para volver a sonar."""
    if not isinstance(value, dict) or not value.get("video_id") or not value.get("title"):
        return None
    clean = {key: value[key] for key in TRACK_KEYS if key in value
             and isinstance(value[key], _SCALARS)}
    return clean or None


def _tracks(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    return [track for track in map(_track, values) if track][:MAX_TRACKS]


def _valid_order(order, size: int) -> list[int]:
    if size <= 0:
        return []
    try:
        valid = isinstance(order, list) and sorted(order) == list(range(size))
    except TypeError:
        valid = False
    return order if valid else list(range(size))


def save(state: dict, path: Path | None = None) -> None:
    """Guarda el estado de reproducción. Un fallo de disco no debe impedir cerrar la ventana."""
    path = Path(path or PATH)
    payload = {
        "current": _track(state.get("current")),
        "position_ms": max(0, int(state.get("position_ms") or 0)),
        "ctx": _tracks(state.get("ctx")),
        "order": state.get("order") if isinstance(state.get("order"), list) else [],
        "opos": int(state.get("opos") or 0),
        "up_next": _tracks(state.get("up_next")),
        "auto_ctx": bool(state.get("auto_ctx")),
        "played": [str(v) for v in (state.get("played") or []) if isinstance(v, str)][:MAX_TRACKS],
    }
    payload["order"] = _valid_order(payload["order"], len(payload["ctx"]))
    payload["opos"] = min(max(0, payload["opos"]),
                          max(0, len(payload["order"]) - 1))
    try:
        write_text_atomic(path, json.dumps(payload, ensure_ascii=False, default=str))
    except OSError:
        log.warning("no se pudo guardar la sesión en %s", path, exc_info=True)


def load(path: Path | None = None) -> dict | None:
    """Estado guardado y saneado, o None si no hay nada útil."""
    path = Path(path or PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("sesión ilegible en %s: se ignora", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None

    current = _track(data.get("current"))
    ctx = _tracks(data.get("ctx"))
    appended = False
    if current and current["video_id"] not in {t["video_id"] for t in ctx}:
        ctx.append(current)
        appended = True
    if not current and not ctx:
        return None

    if appended:
        order = list(range(len(ctx)))
        opos = max(0, len(ctx) - 1)  # la canción recuperada va al final de la cola
    else:
        order = _valid_order(data.get("order"), len(ctx))
        opos = int(data.get("opos") or 0)
        opos = min(max(0, opos), max(0, len(order) - 1))
    up_next = _tracks(data.get("up_next"))
    played = [v for v in (data.get("played") or []) if isinstance(v, str)]
    return {
        "current": current,
        "position_ms": max(0, int(data.get("position_ms") or 0)),
        "ctx": ctx,
        "order": order,
        "opos": opos,
        "up_next": up_next,
        "auto_ctx": bool(data.get("auto_ctx")),
        "played": played[:MAX_TRACKS],
    }


def clear(path: Path | None = None) -> None:
    try:
        Path(path or PATH).unlink(missing_ok=True)
    except OSError:
        log.warning("no se pudo borrar la sesión", exc_info=True)
