"""El navegador de cookies es un único estado compartido por descargas y streaming."""
from app.core import cookies


def test_without_a_browser_no_cookie_options_are_requested(monkeypatch):
    monkeypatch.setattr(cookies, "_browser", "")
    monkeypatch.setattr(cookies, "_generation", 0)

    assert cookies.browser() == ""
    assert cookies.ydl_options() == {}


def test_configure_normalizes_the_name_and_bumps_the_generation(monkeypatch):
    monkeypatch.setattr(cookies, "_browser", "")
    monkeypatch.setattr(cookies, "_generation", 0)

    cookies.configure("  Firefox ")

    assert cookies.browser() == "firefox"
    assert cookies.ydl_options() == {"cookiesfrombrowser": ("firefox",)}
    assert cookies.generation() == 1

    cookies.configure(None)

    assert cookies.browser() == ""
    assert cookies.ydl_options() == {}
    assert cookies.generation() == 2
