from app.core import downloader


def test_safe_name_strips_forbidden_chars():
    assert downloader.safe_name('AC/DC: "Back" in Black?') == "AC_DC_ _Back_ in Black_"


def test_safe_name_never_empty():
    assert downloader.safe_name("...") == "sin_nombre"
