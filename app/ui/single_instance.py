"""Una sola ventana de Sound a la vez.

Dos instancias compartirían preferencias, sesión y reproductor, y la segunda pisaría el estado
de la primera. El bloqueo lo libera el sistema al morir el proceso, así que no queda pegado.
"""
import os
from pathlib import Path

from ..core import config


class SingleInstance:
    def __init__(self, name: str = "Sound.SingleInstance") -> None:
        self.name = name
        self._handle = None
        self._file = None

    def acquire(self) -> bool:
        """True si esta es la única instancia. Si ya hay otra, avisa al usuario y devuelve False."""
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_lockfile()

    def _acquire_windows(self) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            self._handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            if not self._handle:
                return True  # sin mutex no se bloquea la app: mejor abrir que no arrancar
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Sound ya está abierto.\n\nCierra la otra ventana antes de abrir otra.",
                    "Sound", 0x40)
                return False
            return True
        except Exception:  # noqa: BLE001 - si falla la API, permitir el arranque
            return True

    def _acquire_lockfile(self) -> bool:
        path = Path(config.DATA_DIR) / "sound.lock"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a+b")
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
