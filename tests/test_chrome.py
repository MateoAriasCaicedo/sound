"""Tema (Como Windows, claro u oscuro) y contador de Descargas, sin ventanas."""
from app.ui.chrome import badge_text, initial_theme


def test_new_installs_follow_windows():
    assert initial_theme({}, system_dark=True) == "system"
    assert initial_theme({}, system_dark=False) == "system"


def test_saved_theme_mode_wins():
    assert initial_theme({"theme": "light", "dark": True}, system_dark=True) == "light"
    assert initial_theme({"theme": "system", "dark": False}, system_dark=True) == "system"
    assert initial_theme({"theme": "rosa", "dark": True}, system_dark=True) == "system"


def test_old_settings_keep_a_deliberate_choice():
    # antes solo se guardaba «dark»: si coincide con Windows, nadie lo eligió a propósito
    assert initial_theme({"dark": True}, system_dark=True) == "system"
    assert initial_theme({"dark": False}, system_dark=False) == "system"
    # si no coincide, alguien lo cambió: se respeta
    assert initial_theme({"dark": False}, system_dark=True) == "light"
    assert initial_theme({"dark": True}, system_dark=False) == "dark"


def test_download_badge():
    assert badge_text(0) is None and badge_text(-1) is None
    assert badge_text(7) == "7" and badge_text(99) == "99" and badge_text(250) == "99+"
