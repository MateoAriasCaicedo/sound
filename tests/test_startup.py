"""Arranque automático: comando correcto, clave Run (Windows) y .desktop (Linux)."""
import os

import pytest

from app.core import startup

windows_only = pytest.mark.skipif(os.name != "nt", reason="la clave Run es de Windows")


class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 1
    REG_SZ = 2

    def __init__(self):
        self.values = {}

    def CreateKeyEx(self, *_args):
        return FakeKey()

    def OpenKey(self, *_args):
        return FakeKey()

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        self.values[name] = value

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_launch_command_quotes_the_installed_executable(monkeypatch):
    monkeypatch.setattr(startup.sys, "executable", r"C:\Program Files\Sound\Sound.exe")

    assert startup._launch_command(frozen=True) == r'"C:\Program Files\Sound\Sound.exe"'


def test_launch_command_uses_pythonw_in_development(monkeypatch, tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(startup.sys, "executable", str(exe))

    assert startup._launch_command(frozen=False) == f'"{tmp_path / "pythonw.exe"}" -m app.ui'


@windows_only
def test_enable_reads_and_removes_the_run_value(monkeypatch):
    registry = FakeWinreg()
    monkeypatch.setattr(startup, "_winreg", lambda: registry)
    monkeypatch.setattr(startup, "_launch_command", lambda: r'"C:\Sound\Sound.exe"')

    assert not startup.is_enabled()
    assert startup.set_enabled(True)
    assert registry.values == {"Sound": r'"C:\Sound\Sound.exe"'}
    assert startup.is_enabled()

    assert startup.set_enabled(False)
    assert registry.values == {}
    assert not startup.is_enabled()


@windows_only
def test_a_stale_entry_for_another_installation_does_not_count(monkeypatch):
    registry = FakeWinreg()
    registry.values["Sound"] = r'"D:\Otra\Sound.exe"'
    monkeypatch.setattr(startup, "_winreg", lambda: registry)
    monkeypatch.setattr(startup, "_launch_command", lambda: r'"C:\Sound\Sound.exe"')

    assert not startup.is_enabled()


def test_linux_autostart_writes_and_removes_a_desktop_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(startup.sys, "platform", "linux")
    monkeypatch.setattr(startup.sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert startup.available()
    assert not startup.is_enabled()
    assert startup.set_enabled(True)

    entry = (tmp_path / "autostart" / "sound.desktop").read_text(encoding="utf-8")
    assert "Exec=/usr/bin/python3 -m app.ui" in entry
    assert startup.is_enabled()

    # Otra instalación distinta no cuenta como la misma entrada.
    monkeypatch.setattr(startup.sys, "executable", "/opt/otra/python3")
    assert not startup.is_enabled()

    assert startup.set_enabled(False)
    assert not startup.is_enabled()
