"""Listas de reproducción propias."""
from pathlib import Path

from app.core import library, playlists


def add(video_id, title=None, duration=180):
    library.add_track({"video_id": video_id, "title": title or f"T{video_id}", "artist": "A",
                       "album": "Álbum", "genre": "Rock", "duration": duration},
                      Path(f"{video_id}.m4a"))


def remote(video_id):
    return {"video_id": video_id, "title": f"Remota {video_id}", "artist": "B", "duration": 200}


def test_create_add_keeps_order_and_skips_repeated_songs():
    add("a")
    add("b")
    pid = playlists.create("Para entrenar")
    assert playlists.add(pid, [library.get_track("b"), library.get_track("a")]) == 2
    assert playlists.add(pid, [library.get_track("a"), remote("y")]) == 1  # «a» ya estaba
    assert [t["video_id"] for t in playlists.tracks(pid)] == ["b", "a", "y"]
    info = playlists.get(pid)
    assert info["name"] == "Para entrenar" and info["count"] == 3
    assert info["duration"] == 180 + 180 + 200


def test_songs_not_downloaded_have_no_path_and_library_data_wins():
    add("a", title="Título actual")
    pid = playlists.create("Mix")
    playlists.add(pid, [{"video_id": "a", "title": "Título viejo", "artist": "A"}, remote("y")])
    a, y = playlists.tracks(pid)
    assert a["title"] == "Título actual" and a["path"] == "a.m4a"
    assert "path" not in y and y["title"] == "Remota y"


def test_remove_reorder_and_move_selection():
    for vid in "abcd":
        add(vid)
    pid = playlists.create("L")
    playlists.add(pid, [library.get_track(v) for v in "abcd"])
    assert playlists.remove(pid, ["b"]) == 1
    playlists.reorder(pid, ["d", "a"])
    assert [t["video_id"] for t in playlists.tracks(pid)] == ["d", "a", "c"]
    order = ["a", "b", "c", "d"]
    assert playlists.moved(order, {"b", "c"}, -1) == ["b", "c", "a", "d"]
    assert playlists.moved(order, {"c"}, 1) == ["a", "b", "d", "c"]
    assert playlists.moved(order, {"a", "c"}, -1) == order  # el bloque toca el borde: quieto


def test_rename_delete_unique_names_and_membership():
    add("a")
    first = playlists.create("Nueva lista")
    assert playlists.unique_name() == "Nueva lista 2"
    assert playlists.rename(first, "  Domingo ") and playlists.get(first)["name"] == "Domingo"
    assert not playlists.rename(first, "   ")  # un nombre vacío no se acepta
    other = playlists.create("Otra")
    playlists.add(first, [library.get_track("a")])
    assert playlists.containing(["a"]) == {first}
    playlists.delete(first)
    assert [p["id"] for p in playlists.all_playlists()] == [other]
    assert playlists.tracks(first) == []


def test_export_m3u_of_a_playlist_keeps_its_order(tmp_path):
    for vid in "ab":
        song = tmp_path / f"{vid}.m4a"
        song.write_bytes(b"x")
        library.add_track({"video_id": vid, "title": vid, "artist": "A", "duration": 100}, song)
    pid = playlists.create("L")
    playlists.add(pid, [library.get_track("b"), library.get_track("a"), remote("y")])
    dest = tmp_path / "lista.m3u"
    assert library.export_m3u(dest, tracks=playlists.tracks(pid)) == 2  # la remota no tiene archivo
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[2].endswith("b.m4a") and lines[4].endswith("a.m4a")
