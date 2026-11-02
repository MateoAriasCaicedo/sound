"""Motor mínimo de animación: un solo bucle `after` que duerme cuando no hay nada que mover."""
import time
import tkinter as tk

from ..core import log as _log

log = _log.get("ui.anim")
FRAME_MS = 25


def ease_out(t: float) -> float:
    """Arranca rápido y se posa suave: la curva para casi todo lo que responde al usuario."""
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def reduced_motion() -> bool:
    """True si Windows tiene desactivadas las animaciones (accesibilidad)."""
    try:
        import ctypes
        enabled = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return not enabled.value
    except Exception:  # noqa: BLE001 - no es Windows
        return False


class Animator:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.reduced = reduced_motion()
        self._tweens: dict = {}
        self._loops: list = []
        self._job = None

    def tween(self, key, ms: int, step, ease=ease_out, done=None) -> None:
        """Llama a step(p) con p de 0 a 1. Otra animación con la misma clave la sustituye."""
        if self.reduced or ms <= 0:
            self._tweens.pop(key, None)
            step(1.0)
            if done:
                done()
            return
        self._tweens[key] = (time.monotonic(), ms / 1000, step, ease, done)
        self.wake()

    def add_loop(self, fn) -> None:
        """fn(ahora) se llama en cada fotograma; devuelve True mientras quiera seguir moviéndose."""
        self._loops.append(fn)
        self.wake()

    def wake(self) -> None:
        if self._job is None:
            self._job = self.root.after(FRAME_MS, self._frame)

    def _frame(self) -> None:
        self._job = None
        now = time.monotonic()
        for key, (t0, dur, step, ease, done) in list(self._tweens.items()):
            p = min(1.0, (now - t0) / dur)
            if p >= 1.0:
                del self._tweens[key]
            try:
                step(ease(p))
                if p >= 1.0 and done:
                    done()
            except tk.TclError:  # el control ya no existe (ventana cerrada)
                self._tweens.pop(key, None)
            except Exception as exc:  # noqa: BLE001 - una animación rota no debe parar las demás
                self._tweens.pop(key, None)
                _log.failure(log, f"animación {key}", exc)
        alive = False
        for fn in self._loops:
            try:
                alive = bool(fn(now)) or alive
            except tk.TclError:
                pass
            except Exception as exc:  # noqa: BLE001
                _log.failure(log, "bucle de animación", exc)
        if self._tweens or alive:
            self.wake()
