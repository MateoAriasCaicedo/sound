import random

from app.core.autoqueue import backup_queue, reason_for


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


def test_reason_for_explains_the_backup_selection():
    assert reason_for(song("a", "A", "Rock"), "rock") == "mismo género"
    assert reason_for(song("b", "B", "Pop"), "Rock") == "otro género"
    assert reason_for(song("c", "C", "Rock"), None) == "de tu biblioteca"


def test_energy_filter_avoids_extreme_jumps_when_there_are_options():
    library = [song(f"a{i}", f"A{i}", "Rock") for i in range(8)]
    energy = {f"a{i}": (0.9 if i % 2 else 0.2) for i in range(8)}
    queue = backup_queue(library, "Rock", set(), n=4, rng=random.Random(1),
                         energy=lambda t: energy[t["video_id"]], target_energy=0.85)
    assert len(queue) == 4
    assert all(energy[t["video_id"]] > 0.8 for t in queue)


def test_energy_filter_keeps_unknown_tracks_and_small_pools():
    library = [song(f"a{i}", f"A{i}", "Rock") for i in range(3)]
    assert len(backup_queue(library, "Rock", set(), n=3, energy=lambda _t: 0.9,
                            target_energy=0.1)) == 3
    assert len(backup_queue(library, "Rock", set(), n=3, energy=lambda _t: None,
                            target_energy=0.1)) == 3
