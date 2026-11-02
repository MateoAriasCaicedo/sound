from contextlib import closing
from pathlib import Path

import pytest

from app.core import library, playlists


def add(video_id, title="T", artist="A"):
    library.add_track({"video_id": video_id, "title": f"{title}{video_id}", "artist": artist},
                      Path(f"{video_id}.m4a"))


@pytest.fixture
def songs():
    for vid in "abcd":
        add(vid)


def ids(ref):
    return [t["video_id"] for t in playlists.tracks(ref)]


def test_create_list_and_case_insensitive_unique_name():
    playlists.create("Rock clásico")
    with pytest.raises(playlists.PlaylistError, match="ya existe"):
        playlists.create("ROCK CLÁSICO".replace("Á", "á"))
    assert [p["name"] for p in playlists.all_lists()] == ["Rock clásico"]


def test_empty_name_is_rejected():
    with pytest.raises(playlists.PlaylistError, match="vacío"):
        playlists.create("   ")


def test_add_keeps_order_and_ignores_repeats_and_unknown_ids(songs):
    playlists.create("Mix")

    added = playlists.add("Mix", ["c", "a", "c", "zzz"])
    again = playlists.add("Mix", ["a", "b"])

    assert (added, again) == (2, 1)  # 'zzz' no está en la biblioteca; 'a' ya estaba
    assert ids("Mix") == ["c", "a", "b"]


def test_remove_and_move(songs):
    playlists.create("Mix")
    playlists.add("Mix", ["a", "b", "c", "d"])

    assert playlists.remove("Mix", ["b", "nope"]) == 1
    playlists.move("Mix", "d", 0)
    assert ids("Mix") == ["d", "a", "c"]
    playlists.move("Mix", "d", 99)  # fuera de rango: al final
    assert ids("Mix") == ["a", "c", "d"]
    with pytest.raises(playlists.PlaylistError):
        playlists.move("Mix", "b", 0)


def test_rename_and_unknown_playlist(songs):
    playlists.create("Uno")
    playlists.create("Dos")
    playlists.rename("Uno", "Primera")
    with pytest.raises(playlists.PlaylistError, match="ya existe"):
        playlists.rename("Primera", "dos")
    with pytest.raises(playlists.PlaylistError, match="no existe"):
        playlists.tracks("Uno")


def test_playlist_can_be_addressed_by_id(songs):
    pid = playlists.create("Mix")
    playlists.add(pid, ["a"])
    assert ids(pid) == ["a"]


def test_delete_playlist_keeps_songs_in_library(songs):
    playlists.create("Mix")
    playlists.add("Mix", ["a", "b"])

    playlists.delete("Mix")

    assert playlists.all_lists() == []
    assert len(library.all_tracks()) == 4
    with closing(library.connect()) as conn:  # la cascada limpió los elementos
        assert conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0] == 0


def test_removing_a_song_from_library_removes_it_from_playlists(songs):
    playlists.create("Mix")
    playlists.add("Mix", ["a", "b"])

    library.remove_track("a")

    assert ids("Mix") == ["b"]
    assert playlists.all_lists()[0]["tracks"] == 1


def test_all_lists_counts_tracks(songs):
    playlists.create("B")
    playlists.create("a")
    playlists.add("B", ["a", "b", "c"])

    assert [(p["name"], p["tracks"]) for p in playlists.all_lists()] == [("a", 0), ("B", 3)]
