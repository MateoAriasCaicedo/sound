from pathlib import Path

import pytest

from app.core import downloader, library


def _track(video_id="v1"):
    return {"video_id": video_id, "title": f"T{video_id}", "artist": "A", "album": None,
            "duration": 100, "cover": None}


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(downloader, "is_online", lambda *a, **k: True)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "t.db")


def test_classification():
    assert downloader.is_permanent(Exception("ERROR: Video unavailable"))
    # mensaje real de yt-dlp con un id inexistente (visto en la prueba de extremo a extremo)
    assert downloader.is_permanent(Exception("ERROR: [youtube] ZZZZZZZZZZZ: This video is unavailable"))
    assert not downloader.is_permanent(Exception("HTTP Error 503: Service Unavailable"))
    assert not downloader.is_permanent(Exception("HTTP Error 403: Forbidden"))
    assert downloader.is_throttle(Exception("unable to download: HTTP Error 429"))
    assert not downloader.is_throttle(Exception("timed out"))


def test_retries_transient_errors_with_growing_delay(monkeypatch, online):
    outcomes = [Exception("HTTP Error 403: Forbidden"), Exception("timed out"), Path("ok.m4a")]

    def fake_download(track, genre):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(downloader, "download_track", fake_download)
    sleeps, retries = [], []
    now = [0.0]  # reloj simulado: el enfriamiento del 403 pasa al instante en vez de 30s reales
    pacer = downloader.Pacer(clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))

    path, used = downloader.download_with_retry(
        _track(), "salsa", pacer,
        sleep=sleeps.append, on_retry=lambda t, n, d, e: retries.append(n),
    )

    assert (path, used) == (Path("ok.m4a"), 3)
    assert retries == [1, 2]
    assert 2 <= sleeps[0] < 3 and 4 <= sleeps[1] < 5  # 2**intento + azar < 1s


def test_permanent_error_is_not_retried(monkeypatch, online):
    calls = []

    def fake_download(track, genre):
        calls.append(1)
        raise Exception("ERROR: Private video")

    monkeypatch.setattr(downloader, "download_track", fake_download)

    with pytest.raises(Exception, match="Private video"):
        downloader.download_with_retry(_track(), sleep=lambda s: None)

    assert len(calls) == 1


def test_gives_up_after_max_attempts_and_reports_count(monkeypatch, online):
    monkeypatch.setattr(downloader, "download_track", lambda t, g: (_ for _ in ()).throw(Exception("timed out")))

    with pytest.raises(Exception, match="timed out") as info:
        downloader.download_with_retry(_track(), attempts=3, sleep=lambda s: None)

    assert info.value.attempts == 3


def test_offline_wait_does_not_consume_attempts(monkeypatch):
    states = iter([False])  # offline justo después del primer fallo, luego online
    monkeypatch.setattr(downloader, "is_online", lambda *a, **k: next(states, True))
    waited = []
    monkeypatch.setattr(downloader, "wait_for_internet", lambda sleep, max_wait: waited.append(max_wait))
    outcomes = [Exception("timed out"), Path("ok.m4a")]

    def fake_download(track, genre):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(downloader, "download_track", fake_download)

    path, used = downloader.download_with_retry(_track(), sleep=lambda s: None)

    assert waited == [downloader.OFFLINE_MAX_WAIT]
    assert used == 1  # el corte de internet no gastó el intento


def test_offline_wait_zero_fails_fast_and_notifies(monkeypatch):
    """Reproducir una canción: sin internet no se espera media hora, se avisa y se falla."""
    monkeypatch.setattr(downloader, "is_online", lambda *a, **k: False)
    monkeypatch.setattr(downloader.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("sin red")))
    monkeypatch.setattr(downloader, "download_track", lambda t, g, **k: (_ for _ in ()).throw(Exception("timed out")))
    notified = []

    with pytest.raises(ConnectionError, match="Sin internet"):
        downloader.download_with_retry(
            _track(), offline_wait=0, on_offline=lambda t: notified.append(t["video_id"]),
            sleep=lambda s: None,
        )

    assert notified == ["v1"]


def test_throttle_sets_shared_cooldown(monkeypatch, online):
    now = [100.0]
    pacer = downloader.Pacer(clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    outcomes = [Exception("HTTP Error 429"), Path("ok.m4a")]

    def fake_download(track, genre):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(downloader, "download_track", fake_download)

    downloader.download_with_retry(_track(), pacer=pacer, sleep=lambda s: None)

    assert pacer._until == 100.0 + downloader.THROTTLE_COOLDOWN
    # el siguiente trabajador que llame a wait() espera hasta el final del enfriamiento
    pacer.wait()
    assert now[0] == pacer._until


def test_enqueue_ignores_duplicates(temp_db):
    first = library.enqueue([_track("a"), _track("b"), _track("a")], "salsa")
    again = library.enqueue([_track("a"), _track("c")], "salsa")

    assert first == 2
    assert again == 1
    assert [r["video_id"] for r in library.pending()] == ["a", "b", "c"]


def test_run_queue_marks_done_and_failed_and_keeps_going(monkeypatch, temp_db):
    library.enqueue([_track("ok1"), _track("bad"), _track("ok2")], "salsa")

    def fake_retry(track, genre, pacer, **kwargs):
        if track["video_id"] == "bad":
            err = Exception("ERROR: Video unavailable")
            err.attempts = 1
            raise err
        return Path("x.m4a"), 2

    monkeypatch.setattr(downloader, "download_with_retry", fake_retry)

    counts = downloader.run_queue()

    assert counts == {"done": 2, "failed": 1}
    failed = library.failed()
    assert [r["video_id"] for r in failed] == ["bad"]
    assert failed[0]["attempts"] == 1 and "unavailable" in failed[0]["error"]


def test_run_queue_stops_cleanly_when_disk_is_nearly_full(monkeypatch, temp_db):
    library.enqueue([_track("a"), _track("b"), _track("c")], "salsa")
    calls = []
    monkeypatch.setattr(downloader, "free_bytes", lambda *a: downloader.MIN_FREE_BYTES - 1)
    monkeypatch.setattr(downloader, "download_with_retry", lambda *a, **k: calls.append(1))

    counts = downloader.run_queue()

    assert calls == []  # no se intentó descargar nada
    assert counts == {"pending": 3}  # todo sigue pendiente para reanudar cuando haya espacio


def test_run_queue_stops_midway_when_space_runs_out(monkeypatch, temp_db):
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    monkeypatch.setattr(downloader, "MAX_PARALLEL", 1)
    free = iter([downloader.MIN_FREE_BYTES + 1, downloader.MIN_FREE_BYTES + 1])  # alcanza para 2

    def low_after_two(*a):
        return next(free, downloader.MIN_FREE_BYTES - 1)

    monkeypatch.setattr(downloader, "free_bytes", low_after_two)
    monkeypatch.setattr(downloader, "download_with_retry", lambda *a, **k: (Path("x.m4a"), 1))

    counts = downloader.run_queue()

    assert counts == {"done": 2, "pending": 2}


def test_run_queue_forwards_progress_per_track(monkeypatch, temp_db):
    library.enqueue([_track("a")], "salsa")
    seen = []

    def fake_retry(track, genre, pacer, **kwargs):
        kwargs["on_progress"](0.25)
        kwargs["on_progress"](1.0)
        return Path("x.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", fake_retry)

    downloader.run_queue(on_progress=lambda track, fraction: seen.append((track["video_id"], fraction)))

    assert seen == [("a", 0.25), ("a", 1.0)]


def test_retry_failed_requeues_only_failed(temp_db):
    library.enqueue([_track("a"), _track("b")], None)
    library.mark("a", "done", 1)
    library.mark("b", "failed", 4, "boom")

    assert library.retry_failed() == 1
    assert library.queue_counts() == {"done": 1, "pending": 1}
