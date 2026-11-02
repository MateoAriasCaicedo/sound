import pytest

from app.core import filters


def _t(title, artist="Artista", album=None, video_type=None):
    return {"video_id": "x", "title": title, "artist": artist, "album": album, "video_type": video_type}


# Títulos reales vistos en las playlists oficiales de YouTube Music que NO son la grabación original.
@pytest.mark.parametrize(
    "title",
    [
        "La Plata (En Vivo)",
        "Me Gusta El Son (Live) (feat. Tito Puente)",
        "Get Back (2021 Mix)",
        "Everlong (Acoustic Version)",
        "Vuela Muy Alto (Salsa Version)",
        "Abril Oneil (Versión Demo)",
        "Despacito (Remix)",
        "Hotel California (Cover)",
        "Salsa Mix 2020 - Las Mejores",
        "Medley cumbia colombiana (Medley)",
        "Imagine - Instrumental",
        "Some Song (Karaoke Version)",
        "Fiesta (Sped Up)",
        "Noche de Farra y Son (Version Salsa)",  # orden en español, visto en salsa real
        "Cherry Oh Baby (Version)",  # reggae: dub/instrumental alterno
        "Volví a Nacer (Extended Version)",
    ],
)
def test_rejects_non_original_versions(title):
    assert filters.reject_reason(_t(title)) is not None


# Canciones normales que contienen palabras sospechosas o son la misma grabación original.
@pytest.mark.parametrize(
    "title",
    [
        "Juana Peña",
        "Live Forever",
        "Cover Me",
        "Under Cover of the Night",
        "Mixed Emotions",
        "Livin' on a Prayer",
        "Bohemian Rhapsody (Remastered 2011)",
        "Lunes Por La Madrugada (1994 Remastered Version)",
        "Born To Be Wild (Single Version)",
        "She Loves You (Mono Version / Remastered 2009)",
        "Strobe (Original Mix)",
        "Treinta Kilos (Audio Oficial)",
        "Fuego en el 23 (Remastered 2026)",
        "Sombra Loca (Video Version)",
        "Jump (45 Version)",
        "Tu Eres Ajena (Bachata)",  # el género entre paréntesis no es una versión
        "Dance The Night (From Barbie The Album)",
        "Amor Eterno (Versión Original)",
        "Amor Eterno (Versión Remasterizada)",
    ],
)
def test_keeps_normal_songs(title):
    assert filters.reject_reason(_t(title)) is None


def test_reason_is_reported():
    assert filters.reject_reason(_t("La Plata (En Vivo)")) == "en vivo"
    assert filters.reject_reason(_t("Vuela Muy Alto (Salsa Version)")) == "salsa version"


def test_rejects_by_album():
    assert filters.reject_reason(_t("Son Cuero", album="Live At The Red Garter, Vol. 2")).startswith("álbum")
    assert filters.reject_reason(_t("Aserejé", album="Tributo a la Cumbia Colombiana")).startswith("álbum")
    assert filters.reject_reason(_t("Gotas de Lluvia", album="Greatest Hits")) is None


def test_rejects_by_artist():
    assert filters.reject_reason(_t("Song", artist="Karaoke Hits Band")).startswith("artista")
    assert filters.reject_reason(_t("Song", artist="Various Artists")).startswith("artista")
    assert filters.reject_reason(_t("Song", artist="Willie Colón")) is None


def test_rejects_user_uploads_but_keeps_official_types():
    assert filters.reject_reason(_t("Song", video_type="MUSIC_VIDEO_TYPE_UGC")) == "subida de usuario"
    assert filters.reject_reason(_t("Song", video_type="MUSIC_VIDEO_TYPE_OMV")) is None
    assert filters.reject_reason(_t("Song", video_type="MUSIC_VIDEO_TYPE_ATV")) is None
    assert filters.reject_reason(_t("Song", video_type=None)) is None
