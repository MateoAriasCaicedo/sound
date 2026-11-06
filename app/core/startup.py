"""Arranque automático con Windows mediante la clave Run del usuario.

Se escribe en HKCU (sin pedir administrador). En la app instalada apunta a Sound.exe; en
desarrollo, a `pythonw -m app.ui` para que también se pueda probar desde el código.
"""
import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE = "Sound"


def _winreg():
    """Importa winreg solo cuando se usa: el resto del proyecto no depende de Windows."""
    import winreg

    return winreg


def available() -> bool:
    return os.name == "nt"


def _launch_command(frozen: bool | None = None) -> str:
    """Comando que arranca Sound, entre comillas para rutas con espacios."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # sin consola en el arranque
    return f'"{pythonw if pythonw.exists() else exe}" -m app.ui'


def _read_value(value_name: str = APP_VALUE) -> str | None:
    if not available():
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
        return str(value)
    except OSError:
        return None


def is_enabled(value_name: str = APP_VALUE) -> bool:
    """¿El arranque automático apunta a esta misma instalación?"""
    value = _read_value(value_name)
    if not value:
        return False
    # Windows no distingue mayúsculas en las rutas.
    return os.path.normcase(value.strip()) == os.path.normcase(_launch_command().strip())


def _write_value(command: str, value_name: str = APP_VALUE) -> bool:
    if not available():
        return False
    winreg = _winreg()
    try:
        with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def _delete_value(value_name: str = APP_VALUE) -> bool:
    if not available():
        return False
    winreg = _winreg()
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
        return True
    except FileNotFoundError:
        return True  # ya no estaba
    except OSError:
        return False


def set_enabled(enabled: bool, value_name: str = APP_VALUE) -> bool:
    """Activa o desactiva el arranque automático. Devuelve si se pudo aplicar."""
    return _write_value(_launch_command(), value_name) if enabled else _delete_value(value_name)
