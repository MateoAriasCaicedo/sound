"""Engancha los fallos no controlados al registro y a un diálogo visible.

`pythonw` (el lanzador de la ventana) no tiene consola: sin esto, una excepción en un hilo o en
el arranque desaparece sin dejar rastro para el usuario.
"""
import sys
import threading
import traceback

from ..core import log as _log

log = _log.get("diagnostics")


def install(app) -> None:
    """Instala los ganchos. `app` debe tener `q` (cola de la UI) y `_show_crash`."""
    sys.excepthook = lambda kind, value, tb: _handle(app, kind, value, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        _handle(app, args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_hook


def _handle(app, kind, value, tb) -> None:
    details = "".join(traceback.format_exception(kind, value, tb)) if tb else str(value)
    try:
        _log.failure(log, "fallo no controlado", value or Exception(details))
    except Exception:  # noqa: BLE001 - el registro no puede tumbar el manejador
        pass
    try:
        app.q.put(("crash", "Sound encontró un error inesperado.", details))
    except Exception:  # noqa: BLE001 - la ventana ya no existe
        pass
