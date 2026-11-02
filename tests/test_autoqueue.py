import random

from app.core.autoqueue import backup_queue


def song(vid, artist, genre):
    return {"video_id": vid, "title": vid, "artist": artist, "genre": genre}


LIBRARY = (
    [song(f"r{i}", f"Rockero{i % 3}", "Rock") for i in range(6)]
    + [song(f"p{i}", f"Pop{i}", "Pop") for i in range(4)]
    + [song(f"j{i}", f"Jazz{i}", "Jazz") for i in range(3)]
)


def ids(tracks):
    return [t["video_id"] for t in tracks]


def test_same_genre_comes_first_and_alone_when_enough():
    queue = backup_queue(LIBRARY, "Rock", set(), n=5, rng=random.Random(1))
    assert len(queue) == 5
    assert all(t["genre"] == "Rock" for t in queue)


def test_other_genres_only_when_same_genre_runs_out():
    queue = backup_queue(LIBRARY, "Rock", set(), n=9, rng=random.Random(1))
    assert all(t["genre"] == "Rock" for t in queue[:6])  # los 6 de Rock van antes
    assert all(t["genre"] != "Rock" for t in queue[6:])
    assert len(queue) == 9


def test_other_genres_are_grouped_one_at_a_time():
    queue = backup_queue(LIBRARY, "Rock", set(), n=13, rng=random.Random(3))
    tail = [t["genre"] for t in queue[6:]]
    changes = sum(1 for a, b in zip(tail, tail[1:]) if a != b)
    assert changes == 1  # Pop y Jazz, cada uno de corrido


def test_excludes_played_and_queued():
    exclude = {"r0", "r1", "r2"}
    queue = backup_queue(LIBRARY, "Rock", exclude, n=20, rng=random.Random(1))
    assert not exclude & set(ids(queue))
    assert len(queue) == len(LIBRARY) - 3


def test_genre_match_ignores_case_and_none_means_untagged():
    mixed = [song("a", "A", "rock"), song("b", "B", None), song("c", "C", "Pop")]
    assert ids(backup_queue(mixed, "ROCK", set(), n=1)) == ["a"]
    assert ids(backup_queue(mixed, None, set(), n=1)) == ["b"]


def test_artists_are_spread_not_chained():
    library = [song(f"a{i}", "Same", "Rock") for i in range(4)] + [song("b0", "Other", "Rock")]
    queue = backup_queue(library, "Rock", set(), n=5, rng=random.Random(2))
    assert queue[1]["artist"] != queue[0]["artist"] or queue[2]["artist"] != queue[1]["artist"]
    assert {t["artist"] for t in queue[:2]} == {"Same", "Other"}


def test_empty_library_gives_empty_queue():
    assert backup_queue([], "Rock", set()) == []
