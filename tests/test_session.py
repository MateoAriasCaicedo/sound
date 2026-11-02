"""Sesión de reproducción: guardar, leer y sanear el estado de la ventana."""
import json

from app.ui import session


def track(vid, **extra):
    return {"video_id": vid, "title": f"T{vid}", "artist": "A", **extra}


def test_roundtrip_keeps_queue_position_and_extra_fields(tmp_path):
    path = tmp_path / "session.json"
    state = {
        "current": track("b", path="b.m4a", album="Álbum", _genre="rock"),
        "position_ms": 4321,
        "ctx": [track("a"), track("b"), track("c")],
        "order": [2, 0, 1],
        "opos": 1,
        "up_next": [track("d")],
        "auto_ctx": True,
        "played": ["a", "b"],
    }

    session.save(state, path)
    loaded = session.load(path)

    assert loaded["current"] == track("b", path="b.m4a", album="Álbum", _genre="rock")
    assert loaded["position_ms"] == 4321
    assert [t["video_id"] for t in loaded["ctx"]] == ["a", "b", "c"]
    assert loaded["order"] == [2, 0, 1] and loaded["opos"] == 1
    assert [t["video_id"] for t in loaded["up_next"]] == ["d"]
    assert loaded["auto_ctx"] is True
    assert loaded["played"] == ["a", "b"]


def test_missing_or_broken_file_is_ignored(tmp_path):
    assert session.load(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert session.load(bad) is None
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert session.load(bad) is None


def test_current_song_is_added_to_the_queue_if_it_was_not_there(tmp_path):
    path = tmp_path / "session.json"
    session.save({"current": track("z"), "ctx": [], "order": [], "opos": 0}, path)

    loaded = session.load(path)

    assert [t["video_id"] for t in loaded["ctx"]] == ["z"]
    assert loaded["order"] == [0] and loaded["opos"] == 0


def test_invalid_order_and_position_are_rebuilt_and_clamped(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "ctx": [track("a"), track("b")],
        "order": [0, 0],
        "opos": 99,
    }), encoding="utf-8")

    loaded = session.load(path)

    assert loaded["order"] == [0, 1]
    assert loaded["opos"] == 1


def test_non_serializable_or_incomplete_tracks_are_dropped(tmp_path):
    path = tmp_path / "session.json"
    session.save({
        "ctx": [{"video_id": "a", "title": "A", "duration": object()}, {"title": "sin id"}],
        "up_next": "no es una lista",
    }, path)

    loaded = session.load(path)

    assert [t["video_id"] for t in loaded["ctx"]] == ["a"]
    assert "duration" not in loaded["ctx"][0]
    assert loaded["up_next"] == []


def test_queue_without_current_is_still_restored(tmp_path):
    path = tmp_path / "session.json"
    session.save({"ctx": [track("a")], "order": [0], "opos": 0}, path)

    loaded = session.load(path)

    assert loaded["current"] is None
    assert [t["video_id"] for t in loaded["ctx"]] == ["a"]


def test_clear_removes_the_file(tmp_path):
    path = tmp_path / "session.json"
    session.save({"current": track("a")}, path)
    session.clear(path)
    assert not path.exists()
