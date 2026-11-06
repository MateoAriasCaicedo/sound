"""Arranque con Windows: comando correcto y clave Run del usuario."""
import os

import pytest

from app.core import startup

pytestmark = pytest.mark.skipif(os.name != "nt", reason="la clave Run es de Windows")


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


def test_a_stale_entry_for_another_installation_does_not_count(monkeypatch):
    registry = FakeWinreg()
    registry.values["Sound"] = r'"D:\Otra\Sound.exe"'
    monkeypatch.setattr(startup, "_winreg", lambda: registry)
    monkeypatch.setattr(startup, "_launch_command", lambda: r'"C:\Sound\Sound.exe"')

    assert not startup.is_enabled()
