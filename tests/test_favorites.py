from pathlib import Path

from app.core import favorites, library


def add(video_id, title=None):
    library.add_track({"video_id": video_id, "title": title or f"T{video_id}", "artist": "A",
                       "album": "Álbum", "genre": "Rock", "duration": 180},
                      Path(f"{video_id}.m4a"))


def test_set_liked_persists_and_is_idempotent():
    add("a")

    assert favorites.set_liked("a", True) is True
    assert favorites.set_liked("a", True) is True
    assert favorites.liked_ids() == {"a"}
    assert favorites.count() == 1

    assert favorites.set_liked("a", False) is False
    assert favorites.liked_ids() == set()
    assert favorites.count() == 0


def test_toggle_inverts_and_reports_new_state():
    add("a")

    assert favorites.toggle("a") is True
    assert favorites.toggle("a") is False
    assert favorites.toggle("a") is True


def test_songs_outside_the_library_are_ignored():
    assert favorites.toggle("nope") is False
    assert favorites.set_liked("nope", True) is False
    assert favorites.liked_ids() == set()


def test_liked_tracks_keeps_the_usual_track_fields_and_newest_first():
    for vid in "abc":
        add(vid)
    for vid in "abc":
        favorites.set_liked(vid, True)

    rows = favorites.liked_tracks()

    assert [row["video_id"] for row in rows] == ["c", "b", "a"]
    assert rows[0]["album"] == "Álbum"
    assert rows[0]["duration"] == 180


def test_a_like_survives_deleting_and_redownloading_the_song():
    add("a")
    favorites.set_liked("a", True)

    library.remove_track("a")
    assert favorites.liked_ids() == set()  # ya no está en la biblioteca: no se muestra
    assert favorites.count() == 0

    add("a")  # vuelve a descargarse
    assert favorites.liked_ids() == {"a"}


def test_count_only_includes_songs_still_in_the_library():
    add("a")
    add("b")
    favorites.set_liked("a", True)
    favorites.set_liked("b", True)

    library.remove_track("b")

    assert favorites.liked_ids() == {"a"}
    assert favorites.count() == 1
