import pytest
from mutagen.mp4 import MP4

from app.core import library, metadata


@pytest.fixture
def song(tmp_path, make_m4a):
    path = tmp_path / "a.m4a"
    path.write_bytes(make_m4a(60))
    library.add_track({"video_id": "a", "title": "Viejo", "artist": "Antes, Invitado", "album": "Disco",
                       "genre": "Rock", "duration": 60, "year": 1990}, path)
    return path


def tags(path):
    return MP4(path).tags


def test_edit_updates_file_tags_and_library(song):
    result = metadata.edit("a", {"title": "Nuevo", "artist": "Ahora, Otro", "year": "2001"})

    assert result["title"] == "Nuevo" and result["year"] == 2001
    t = tags(song)
    assert t["\xa9nam"] == ["Nuevo"] and t["\xa9ART"] == ["Ahora, Otro"]
    assert t["aART"] == ["Ahora"] and t["\xa9day"] == ["2001"]
    row = library.all_tracks()[0]
    assert (row["title"], row["artist"]) == ("Nuevo", "Ahora, Otro")


def test_empty_album_or_genre_removes_them(song):
    metadata.edit("a", {"album": "Disco"})
    result = metadata.edit("a", {"album": "", "genre": "  "})

    assert result["album"] is None and result["genre"] is None
    assert "\xa9alb" not in (tags(song) or {})
    assert library.all_tracks()[0]["album"] is None


def test_untouched_fields_stay_as_they_were(song):
    result = metadata.edit("a", {"genre": "Pop"})

    assert result["title"] == "Viejo" and result["year"] == 1990
    assert library.all_tracks()[0]["genre"] == "Pop"


@pytest.mark.parametrize("changes, message", [
    ({"title": "  "}, "título"),
    ({"artist": ""}, "artista"),
    ({"year": "abc"}, "inválido"),
    ({"year": 99}, "fuera de rango"),
    ({"color": "rojo"}, "no editables"),
])
def test_invalid_changes_are_rejected_without_touching_anything(song, changes, message):
    with pytest.raises(metadata.MetadataError, match=message):
        metadata.edit("a", changes)

    assert library.all_tracks()[0]["title"] == "Viejo"


def test_unknown_song_and_missing_file(song):
    with pytest.raises(metadata.MetadataError, match="no está en la biblioteca"):
        metadata.edit("zzz", {"title": "X"})
    song.unlink()
    with pytest.raises(metadata.MetadataError, match="ya no existe"):
        metadata.edit("a", {"title": "X"})


def test_unreadable_file_leaves_the_library_untouched(song):
    song.write_bytes(b"no soy un m4a")

    with pytest.raises(metadata.MetadataError, match="etiquetas"):
        metadata.edit("a", {"title": "Nuevo"})

    assert library.all_tracks()[0]["title"] == "Viejo"  # el archivo falló primero: la base no cambia


def test_no_changes_returns_the_current_row(song):
    assert metadata.edit("a", {})["title"] == "Viejo"
