from pathlib import Path

from app.ui.uri import file_uri


def test_accents_and_enyes_are_left_as_they_are():
    # as_uri() daba '%C3%A1' y el reproductor de Windows lo rechazaba (error de medios 4)
    uri = file_uri(Path("C:/Música/Felipe Peláez - Tan Natural ñ.m4a"))

    assert "Peláez" in uri and "ñ" in uri and "Música" in uri
    assert "%C3" not in uri and "%C2" not in uri


def test_ascii_special_characters_are_encoded():
    uri = file_uri(Path("C:/Mi música/100% #1 'Hit', [x].m4a"))

    assert uri.startswith("file:///") and "\\" not in uri
    assert "Mi%20m" in uri and "100%25%20%231" in uri and "%27Hit%27%2C" in uri and "%5Bx%5D" in uri


def test_drive_letter_and_separators_stay_readable():
    assert file_uri(Path("C:/a/b.m4a")).endswith("/a/b.m4a")
    assert file_uri(Path("C:/a/b.m4a")).startswith("file:///")
