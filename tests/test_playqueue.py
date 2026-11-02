from app.ui.playqueue import PlayQueue


def track(n: int, remote: bool = False) -> dict:
    t = {"video_id": f"v{n}", "title": f"T{n}", "artist": "A"}
    if not remote:
        t["path"] = f"/m/{n}.m4a"
    return t


def make(n: int = 5, online: bool = True, remote: tuple[int, ...] = ()) -> PlayQueue:
    state = {"online": online}
    pq = PlayQueue(lambda t: "path" not in t, lambda: state["online"])
    pq.state = state
    pq.start([track(i, i in remote) for i in range(n)], 0, auto=False)
    return pq


def test_order_is_sequential_without_shuffle():
    pq = make(4)
    assert pq.order == [0, 1, 2, 3] and pq.opos == 0


def test_shuffle_puts_first_track_first_and_keeps_all():
    pq = make(6)
    pq.shuffle = True
    pq.make_order(3)
    assert pq.order[0] == 3 and pq.opos == 0
    assert sorted(pq.order) == list(range(6))


def test_shuffle_ignored_for_automatic_queue():
    pq = make(6)
    pq.shuffle, pq.auto_ctx = True, True
    pq.make_order(2)
    assert pq.order == list(range(6)) and pq.opos == 2


def test_next_tracks_puts_manual_queue_first():
    pq = make(5)
    pq.up_next = [track(9)]
    assert [t["video_id"] for t in pq.next_tracks(3)] == ["v9", "v1", "v2"]


def test_next_tracks_stops_at_end_unless_repeating():
    pq = make(3)
    pq.opos = 2
    assert pq.next_tracks(2) == []
    pq.repeat = 1
    assert [t["video_id"] for t in pq.next_tracks(2)] == ["v0", "v1"]


def test_peek_next_repeat_one_returns_current():
    pq = make(3)
    pq.repeat = 2
    cur = track(0)
    assert pq.peek_next(cur) is cur


def test_peek_next_wraps_with_repeat_all_and_ends_without():
    pq = make(3)
    pq.opos = 2
    assert pq.peek_next(None) is None
    pq.repeat = 1
    assert pq.peek_next(None)["video_id"] == "v0"


def test_offline_skips_remote_tracks():
    pq = make(5, online=False, remote=(1, 2))
    assert pq.peek_next(None)["video_id"] == "v3"
    assert pq.skip_remote(1, 1) == 3
    assert pq.skip_remote(2, -1) == 0


def test_playable_ahead_counts_only_local_when_offline():
    pq = make(5, remote=(1, 2))
    assert pq.playable_ahead() == 4
    pq.state["online"] = False
    assert pq.playable_ahead() == 2


def test_append_extends_order_and_current_finds_track():
    pq = make(2)
    pq.append([track(7), track(8)])
    assert pq.order == [0, 1, 2, 3]
    assert pq.current("v8")["title"] == "T8"
    assert pq.current("nope") is None
