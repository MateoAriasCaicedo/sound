from pathlib import Path

from app.core import duplicates, library


def add(tmp_path, video_id, title="Hotel California", artist="Eagles", duration=390, **extra):
    path = tmp_path / f"{video_id}.m4a"
    path.write_bytes(b"x")
    library.add_track({"video_id": video_id, "title": title, "artist": artist, "duration": duration,
                       **extra}, path)
    return path


def test_same_song_with_different_id_is_a_group(tmp_path):
    add(tmp_path, "album", album="Hotel California", year=1976, has_cover=True)
    add(tmp_path, "clip", title="Hotel California - Remastered 2013", has_cover=False)

    (group,) = duplicates.find()

    assert group.keep["video_id"] == "album"  # con carátula, álbum y año
    assert [t["video_id"] for t in group.extras] == ["clip"]


def test_ignores_accents_case_and_secondary_artists(tmp_path):
    add(tmp_path, "a", title="Canción", artist="Café Tacvba, Invitado")
    add(tmp_path, "b", title="CANCION", artist="cafe tacvba")

    assert len(duplicates.find()) == 1


def test_different_songs_are_not_grouped(tmp_path):
    add(tmp_path, "a", title="One")
    add(tmp_path, "b", title="Two")
    add(tmp_path, "c", title="One", artist="Otro artista")

    assert duplicates.find() == []


def test_same_base_title_but_very_different_length_is_not_a_duplicate(tmp_path):
    add(tmp_path, "p1", title="Intro (Parte 1)", duration=60)
    add(tmp_path, "p2", title="Intro (Parte 2)", duration=240)

    assert duplicates.find() == []


def test_missing_duration_does_not_rule_a_match_out(tmp_path):
    add(tmp_path, "a", duration=None)
    add(tmp_path, "b")

    assert len(duplicates.find()) == 1


def test_three_copies_form_one_group_with_two_extras(tmp_path):
    for vid in ("a", "b", "c"):
        add(tmp_path, vid)

    (group,) = duplicates.find()

    assert len(group.extras) == 2


def test_remove_extras_deletes_files_and_library_rows_but_keeps_the_best(tmp_path):
    keep = add(tmp_path, "album", album="X", has_cover=True)
    extra = add(tmp_path, "clip")

    removed, failed = duplicates.remove_extras(duplicates.find())

    assert (removed, failed) == (1, [])
    assert keep.exists() and not extra.exists()
    assert [t["video_id"] for t in library.all_tracks()] == ["album"]


def test_remove_extras_keeps_the_row_when_the_file_cannot_be_deleted(tmp_path, monkeypatch):
    add(tmp_path, "album", album="X", has_cover=True)
    add(tmp_path, "clip")

    def locked(self, missing_ok=False):
        raise PermissionError("en uso")

    monkeypatch.setattr(Path, "unlink", locked)
    removed, failed = duplicates.remove_extras(duplicates.find())

    assert removed == 0 and failed[0][0]["video_id"] == "clip"
    assert {t["video_id"] for t in library.all_tracks()} == {"album", "clip"}
