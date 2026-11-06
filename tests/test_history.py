from contextlib import closing
from pathlib import Path

from app.core import history, library


def play(video_id, title=None, artist="A", n=1):
    for _ in range(n):
        history.record({"video_id": video_id, "title": title or f"T{video_id}", "artist": artist})


def test_recent_lists_each_song_once_most_recent_first():
    play("a")
    play("b")
    play("a")

    rows = history.recent()

    assert [r["video_id"] for r in rows] == ["a", "b"]
    assert rows[0]["plays"] == 2 and rows[1]["plays"] == 1


def test_recent_respects_limit():
    for vid in "abcde":
        play(vid)
    assert [r["video_id"] for r in history.recent(2)] == ["e", "d"]


def test_most_played_orders_by_count_then_recency():
    play("a", n=1)
    play("b", n=3)
    play("c", n=3)

    assert [r["video_id"] for r in history.most_played()] == ["c", "b", "a"]


def test_most_played_can_be_limited_to_recent_days():
    play("old", n=5)
    play("new", n=1)
    with closing(library.connect()) as conn, conn:
        conn.execute("UPDATE plays SET played_at = datetime('now', '-40 days') WHERE video_id = 'old'")

    assert [r["video_id"] for r in history.most_played(days=7)] == ["new"]
    assert [r["video_id"] for r in history.most_played()] == ["old", "new"]


def test_streamed_song_is_recorded_without_path_and_downloaded_one_has_it():
    play("streamed")
    library.add_track({"video_id": "own", "title": "Tg", "artist": "A"}, Path("own.m4a"))
    play("own")

    by_id = {r["video_id"]: r["path"] for r in history.recent()}

    assert by_id["streamed"] is None
    assert by_id["own"] == "own.m4a"


def test_history_survives_deleting_the_song_from_the_library():
    library.add_track({"video_id": "x", "title": "Tx", "artist": "A"}, Path("x.m4a"))
    play("x")

    library.remove_track("x")

    assert history.recent()[0]["path"] is None


def test_clear_returns_how_many_plays_were_removed():
    play("a", n=2)
    assert history.clear() == 2
    assert history.recent() == []


def test_recent_uses_the_timestamp_of_the_latest_play():
    play("a")
    play("b")
    play("a")
    with closing(library.connect()) as conn, conn:
        conn.execute("UPDATE plays SET played_at = '2020-01-01 00:00:00' WHERE video_id = 'a'")

    row = next(r for r in history.recent() if r["video_id"] == "a")

    assert row["last_played"] == "2020-01-01 00:00:00"


def test_prune_removes_only_old_plays():
    play("vieja", n=2)
    play("nueva")
    with closing(library.connect()) as conn, conn:
        conn.execute("UPDATE plays SET played_at = datetime('now', '-800 days') WHERE video_id = 'vieja'")

    assert history.prune(days=730) == 2
    assert [r["video_id"] for r in history.recent()] == ["nueva"]


def test_counts_as_play_needs_real_listening():
    assert not history.counts_as_play(5, 200)
    assert history.counts_as_play(30, 200)
    assert history.counts_as_play(10, 15)  # canción corta: basta la mitad
    assert not history.counts_as_play(5, 15)
