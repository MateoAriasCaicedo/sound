"""Arranque automático con el sistema.

En Windows se escribe la clave Run del usuario (HKCU, sin pedir administrador). En Linux se deja
un archivo `.desktop` en la carpeta de autostart de XDG (`~/.config/autostart`). En los dos casos
apunta al ejecutable de la app instalada o, en desarrollo, a `python -m app.ui`.
"""
import os
import shlex
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE = "Sound"
DESKTOP_FILE = "sound.desktop"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _winreg():
    """Importa winreg solo cuando se usa: el resto del proyecto no depende de Windows."""
    import winreg

    return winreg


def available() -> bool:
    """¿Se puede activar el arranque automático en este sistema?"""
    return _is_windows() or _is_linux()


def _launch_argv(frozen: bool | None = None) -> list[str]:
    """Comando que arranca Sound, como lista de argumentos (para Linux)."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return [sys.executable]
    return [sys.executable, "-m", "app.ui"]


def _launch_command(frozen: bool | None = None) -> str:
    """Comando de Windows que arranca Sound, entre comillas para rutas con espacios."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # sin consola en el arranque
    return f'"{pythonw if pythonw.exists() else exe}" -m app.ui'


def _autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "autostart" / DESKTOP_FILE


def _desktop_entry() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Sound\n"
        "Comment=Descarga, organiza y reproduce música de YouTube Music\n"
        f"Exec={shlex.join(_launch_argv())}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _read_value(value_name: str = APP_VALUE) -> str | None:
    if not _is_windows():
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
    if _is_linux():
        try:
            text = _autostart_file().read_text(encoding="utf-8")
        except OSError:
            return False
        expected = shlex.join(_launch_argv())
        for line in text.splitlines():
            if line.startswith("Exec="):
                return line[len("Exec="):].strip() == expected
        return False
    value = _read_value(value_name)
    if not value:
        return False
    # Windows no distingue mayúsculas en las rutas.
    return os.path.normcase(value.strip()) == os.path.normcase(_launch_command().strip())


def _write_value(command: str, value_name: str = APP_VALUE) -> bool:
    if not _is_windows():
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
    if not _is_windows():
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


def _set_linux(enabled: bool) -> bool:
    path = _autostart_file()
    try:
        if not enabled:
            path.unlink(missing_ok=True)
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_entry(), encoding="utf-8")
        return True
    except OSError:
        return False


def set_enabled(enabled: bool, value_name: str = APP_VALUE) -> bool:
    """Activa o desactiva el arranque automático. Devuelve si se pudo aplicar."""
    if _is_linux():
        return _set_linux(enabled)
    return _write_value(_launch_command(), value_name) if enabled else _delete_value(value_name)
