import threading
import time
from pathlib import Path

import pytest

from app.core import downloader, library, service


def _track(video_id="a"):
    return {"video_id": video_id, "title": f"T{video_id}", "artist": "Artista", "album": None,
            "duration": 100, "cover": None}


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def events():
    return []


@pytest.fixture
def collect(events):
    def on_event(kind, track, **info):
        events.append((kind, track["video_id"] if track else None, info))
    return on_event


@pytest.fixture
def fake_download(monkeypatch):
    """download_with_retry simulado: anota las llamadas y entrega avance y ruta."""
    calls = []

    def fake(track, genre, pacer, **kwargs):
        calls.append({"video_id": track["video_id"], **kwargs})
        if kwargs.get("on_progress"):
            kwargs["on_progress"](0.5)
        return Path(f"{track['video_id']}.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", fake)
    return calls


def test_worker_survives_a_disk_error_instead_of_dying(monkeypatch, fake_download):
    calls = []

    def flaky_free_bytes(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disco no disponible")
        return 10 * 1024**4

    monkeypatch.setattr(downloader, "free_bytes", flaky_free_bytes)
    library.enqueue([_track("a")], None)
    svc = service.DownloadService(persistent=False, workers=1)
    svc._sleep_or_wake = lambda _seconds: None

    svc.start()
    svc.wait_idle()
    svc.close()

    assert library.queue_counts() == {"done": 1}


def test_close_keeps_the_lock_while_a_worker_is_still_running(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(track, genre, pacer, **kwargs):
        started.set()
        assert release.wait(5)
        return Path(f"{track['video_id']}.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", slow)
    library.enqueue([_track("a")], None)
    svc = service.DownloadService(persistent=False, workers=1)
    svc.start()
    assert started.wait(5)

    svc.close(timeout=0.05)
    assert service._try_lock(service._lock_path()) is None  # sigue protegido

    release.set()
    svc.close(timeout=5)
    again = service._try_lock(service._lock_path())
    assert again is not None  # ya terminó: se puede soltar
    service._unlock(again)


# ---------- aviso de yt-dlp desactualizado ----------
def _failing(monkeypatch, message):
    def fake(track, genre, pacer, **kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(downloader, "download_with_retry", fake)


def test_warns_once_after_consecutive_outdated_failures(monkeypatch, events, collect):
    _failing(monkeypatch, "ERROR: Unable to extract player")
    svc = service.DownloadService(collect, persistent=False)
    for i in range(service.OUTDATED_STREAK + 2):
        library.enqueue([_track(f"o{i}")], None)
        row = library.claim_next()
        with pytest.raises(RuntimeError):
            svc._run({k: row[k] for k in library.QUEUE_FIELDS}, None)

    assert [kind for kind, _, _ in events].count("outdated") == 1  # una sola vez por racha


def test_success_or_other_errors_reset_the_outdated_streak(monkeypatch, events, collect):
    svc = service.DownloadService(collect, persistent=False)

    def attempt(vid, message):
        _failing(monkeypatch, message)
        library.enqueue([_track(vid)], None)
        row = library.claim_next()
        with pytest.raises(RuntimeError):
            svc._run({k: row[k] for k in library.QUEUE_FIELDS}, None)

    for i in range(service.OUTDATED_STREAK - 1):
        attempt(f"a{i}", "Unable to extract player")
    attempt("corte", "This video is unavailable")  # otro tipo de fallo: reinicia la cuenta
    for i in range(service.OUTDATED_STREAK - 1):
        attempt(f"b{i}", "Unable to extract player")

    assert "outdated" not in [kind for kind, _, _ in events]


# ---------- cola ----------
def test_claim_next_gives_each_song_to_exactly_one_thread():
    library.enqueue([_track(f"s{i}") for i in range(40)], "salsa")
    got, lock = [], threading.Lock()

    def drain():
        while (row := library.claim_next()) is not None:
            with lock:
                got.append(row["video_id"])

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(got) == sorted(f"s{i}" for i in range(40))  # ninguna repetida ni perdida
    assert library.queue_counts() == {"running": 40}


def test_claim_next_takes_higher_priority_first():
    library.enqueue([_track("a"), _track("b")], "salsa")
    library.enqueue([_track("c")], "salsa", priority=10)

    assert [library.claim_next()["video_id"] for _ in range(3)] == ["c", "a", "b"]


def test_enqueue_bumps_priority_only_while_pending():
    library.enqueue([_track("a"), _track("b")], "salsa")

    assert library.enqueue([_track("a")], "salsa", priority=10) == 0  # no es nueva
    first = library.claim_next()
    assert first["video_id"] == "a" and first["priority"] == 10  # pasó delante de b

    library.enqueue([_track("a")], "salsa", priority=99)  # ya 'running': no se toca
    with library.connect() as conn:
        assert conn.execute("SELECT priority FROM queue WHERE video_id = 'a'").fetchone()[0] == 10


def test_deleted_song_can_be_queued_and_suggested_again():
    library.add_track(_track("a"), Path("a.m4a"))
    library.enqueue([_track("a")], "salsa")
    library.mark("a", "done", 1)
    assert "a" in {vid for vid, _, _ in library.known()}

    library.remove_track("a")  # lo que hace la ventana al borrar una canción

    assert "a" not in {vid for vid, _, _ in library.known()}
    assert library.enqueue([_track("a")], "salsa") == 1


def test_done_song_still_in_library_is_not_requeued():
    library.add_track(_track("a"), Path("a.m4a"))
    library.enqueue([_track("a")], "salsa")
    library.mark("a", "done", 1)

    assert library.enqueue([_track("a")], "salsa") == 0
    assert library.queue_counts() == {"done": 1}


def test_stale_done_row_without_track_is_requeued():
    library.enqueue([_track("a")], "salsa")
    library.mark("a", "done", 1)  # base de datos antigua: 'done' sin fila en tracks

    assert library.enqueue([_track("a")], "salsa") == 1
    assert library.queue_counts() == {"pending": 1}


def test_claim_specific_song():
    track = _track("a")

    assert library.claim(track, "salsa") is True  # no estaba en la cola: queda 'running'
    assert library.claim(track, "salsa") is False  # otro ya la tiene
    library.mark("a", "failed", 1, "boom")
    assert library.claim(track, "salsa") is True  # una fallida puede volver a intentarse
    library.mark("a", "done", 1)
    assert library.claim(track, "salsa") is True  # 'done' sin archivo (el llamador ya lo comprobó)


def test_recover_running_returns_them_to_pending():
    library.enqueue([_track("a"), _track("b")], "salsa")
    library.claim_next()

    assert library.recover_running() == 1
    assert library.queue_counts() == {"pending": 2}


# ---------- bloqueo entre procesos ----------
def test_runner_lock_is_exclusive_and_released():
    path = service._lock_path()
    first = service._try_lock(path)
    assert first is not None
    assert service._try_lock(path) is None  # otro proceso (u otro manejador) lo tiene

    service._unlock(first)

    again = service._try_lock(path)
    assert again is not None
    service._unlock(again)


def test_start_recovers_crashed_downloads_only_when_nobody_else_is_downloading(fake_download):
    library.enqueue([_track("a")], "salsa")
    library.claim_next()  # una sesión anterior se cayó con 'a' a medias
    other_process = service._try_lock(service._lock_path())

    busy = service.DownloadService(persistent=False, workers=1)
    busy.start()
    busy.wait_idle()
    busy.close()
    assert library.queue_counts() == {"running": 1}  # hay otro descargando: no se le quita nada

    service._unlock(other_process)
    alone = service.DownloadService(persistent=False, workers=1)
    alone.start()
    alone.wait_idle()
    alone.close()
    assert library.queue_counts() == {"done": 1}  # nadie más: se recupera y se completa


# ---------- servicio ----------
def test_service_works_the_queue_and_reports_events(fake_download, collect, events):
    library.enqueue([_track("a"), _track("b"), _track("c")], "salsa")
    svc = service.DownloadService(collect, persistent=False, workers=2)

    svc.start()
    svc.wait_idle()
    svc.close()

    assert library.queue_counts() == {"done": 3}
    assert sorted(vid for kind, vid, _ in events if kind == "done") == ["a", "b", "c"]
    assert [i["fraction"] for k, v, i in events if k == "progress" and v == "a"] == [0.5]


def test_progress_events_are_throttled(monkeypatch, collect, events):
    def fake(track, genre, pacer, **kwargs):
        for pct in range(101):
            kwargs["on_progress"](pct / 100)
        return Path("a.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", fake)
    library.enqueue([_track("a")], "salsa")
    svc = service.DownloadService(collect, persistent=False, workers=1)

    svc.start()
    svc.wait_idle()
    svc.close()

    fractions = [i["fraction"] for k, _, i in events if k == "progress"]
    assert len(fractions) <= 52 and fractions[-1] == 1.0  # cada 2 %, no 101 avisos


def test_persistent_service_picks_up_songs_submitted_later(monkeypatch, fake_download, collect, events):
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    svc = service.DownloadService(collect, persistent=True, workers=2)
    svc.start()
    time.sleep(0.1)  # arranca sin nada que hacer

    added = svc.submit([_track("a"), _track("b")], "salsa")

    assert added == 2
    assert wait_until(lambda: library.queue_counts() == {"done": 2})
    svc.close()


def test_failure_is_recorded_and_the_rest_continue(monkeypatch, collect, events):
    def fake(track, genre, pacer, **kwargs):
        if track["video_id"] == "bad":
            err = Exception("ERROR: Video unavailable")
            err.attempts = 1
            raise err
        return Path("x.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", fake)
    library.enqueue([_track("bad"), _track("ok")], "salsa")
    svc = service.DownloadService(collect, persistent=False, workers=1)

    svc.start()
    svc.wait_idle()
    svc.close()

    assert library.queue_counts() == {"done": 1, "failed": 1}
    failed = [(v, i["error"]) for k, v, i in events if k == "failed"]
    assert failed[0][0] == "bad" and "unavailable" in str(failed[0][1])


def test_broken_event_handler_does_not_kill_workers(fake_download):
    def explode(kind, track, **info):
        raise ValueError("el manejador falló")

    library.enqueue([_track("a"), _track("b")], "salsa")
    svc = service.DownloadService(explode, persistent=False, workers=1)

    svc.start()
    svc.wait_idle()
    svc.close()

    assert library.queue_counts() == {"done": 2}


def test_low_disk_pauses_once_and_resumes_when_there_is_space(monkeypatch, fake_download, collect, events):
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    monkeypatch.setattr(service, "LOW_DISK_POLL", 0.05)
    free = [downloader.MIN_FREE_BYTES - 1]
    monkeypatch.setattr(downloader, "free_bytes", lambda *a: free[0])
    library.enqueue([_track("a")], "salsa")
    svc = service.DownloadService(collect, persistent=True, workers=2)

    svc.start()
    assert wait_until(lambda: any(k == "low_disk" for k, _, _ in events))
    time.sleep(0.2)
    assert [k for k, _, _ in events].count("low_disk") == 1  # avisa una vez, no en cada vuelta
    assert library.queue_counts() == {"pending": 1}  # no tocó nada

    free[0] = downloader.MIN_FREE_BYTES * 10  # el usuario liberó espacio
    svc.submit([], None)  # despierta a los trabajadores
    assert wait_until(lambda: library.queue_counts() == {"done": 1})
    assert any(k == "disk_ok" for k, _, _ in events)
    svc.close()


def test_low_space_emits_disk_low_once(monkeypatch, fake_download, collect, events):
    monkeypatch.setattr(service, "IDLE_POLL", 0.01)
    monkeypatch.setattr(service, "LOW_DISK_POLL", 0.01)
    monkeypatch.setattr(downloader, "free_bytes",
                        lambda *a: downloader.LOW_FREE_BYTES - 1)
    library.enqueue([_track("a")], "salsa")
    svc = service.DownloadService(collect, persistent=True, workers=2)

    svc.start()
    assert wait_until(lambda: any(k == "disk_low" for k, _, _ in events))
    assert wait_until(lambda: library.queue_counts() == {"done": 1})
    time.sleep(0.1)
    assert [k for k, _, _ in events].count("disk_low") == 1
    svc.close()


def test_low_space_limits_downloads_to_one(monkeypatch):
    monkeypatch.setattr(service, "IDLE_POLL", 0.01)
    monkeypatch.setattr(downloader, "free_bytes",
                        lambda *a: downloader.MIN_FREE_BYTES + 1)  # zona de aviso, no crítica
    active = peak = 0
    lock = threading.Lock()

    def fake(track, genre, pacer, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return Path(f"{track['video_id']}.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", fake)
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    svc = service.DownloadService(persistent=False, workers=3)

    svc.start()
    svc.wait_idle()
    svc.close()

    assert peak == 1
    assert library.queue_counts() == {"done": 4}


def test_fetch_now_refuses_to_fill_the_last_megabytes(monkeypatch):
    monkeypatch.setattr(downloader, "free_bytes", lambda *a: downloader.MIN_FREE_BYTES - 1)
    svc = service.DownloadService()

    with pytest.raises(OSError, match="espacio suficiente"):
        svc.fetch_now(_track("a"), "salsa")


# ---------- fetch_now (reproducir una canción ☁) ----------
def test_fetch_now_downloads_immediately_without_waiting_for_internet(fake_download, collect, events):
    svc = service.DownloadService(collect)

    path = svc.fetch_now(_track("a"), "salsa")

    assert path == Path("a.m4a")
    assert fake_download[0]["offline_wait"] == 0  # el usuario espera: sin internet falla enseguida
    assert library.queue_counts() == {"done": 1}
    assert [k for k, _, _ in events if k in ("progress", "done")] == ["progress", "done"]


def test_fetch_now_returns_an_existing_song_without_downloading(monkeypatch, fake_download):
    monkeypatch.setattr(library, "get_path", lambda video_id: Path("ya.m4a"))

    assert service.DownloadService().fetch_now(_track("a"), "salsa") == Path("ya.m4a")
    assert fake_download == []


def test_fetch_now_takes_a_pending_song_ahead_of_the_queue(fake_download):
    library.enqueue([_track("x"), _track("y"), _track("a")], "salsa")  # 'a' es la última

    service.DownloadService().fetch_now(_track("a"), "salsa")

    assert [c["video_id"] for c in fake_download] == ["a"]
    assert library.queue_counts() == {"done": 1, "pending": 2}


def test_fetch_now_retakes_a_failed_song(fake_download):
    library.claim(_track("a"), "salsa")
    library.mark("a", "failed", 4, "boom")  # ya falló antes; el usuario la quiere oír ahora

    assert service.DownloadService().fetch_now(_track("a"), "salsa") == Path("a.m4a")
    assert library.queue_counts() == {"done": 1}


def test_fetch_now_waits_for_the_worker_that_already_has_the_song(monkeypatch, fake_download):
    library.claim(_track("a"), "salsa")  # otro trabajador la está bajando
    appears = iter([None, None, Path("lista.m4a")])
    monkeypatch.setattr(library, "get_path", lambda video_id: next(appears))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    path = service.DownloadService().fetch_now(_track("a"), "salsa")

    assert path == Path("lista.m4a")
    assert fake_download == []  # no se bajó dos veces


def test_fetch_now_reports_when_the_other_worker_fails_meanwhile(monkeypatch, fake_download):
    library.claim(_track("a"), "salsa")  # otro trabajador la tiene
    monkeypatch.setattr(library, "get_path", lambda video_id: None)
    # mientras se espera, el otro trabajador falla
    monkeypatch.setattr(service.time, "sleep", lambda seconds: library.mark("a", "failed", 4, "boom"))

    with pytest.raises(RuntimeError, match="boom"):
        service.DownloadService().fetch_now(_track("a"), "salsa")

    assert fake_download == []


def test_fetch_now_gives_up_waiting_with_a_clear_error(monkeypatch, fake_download):
    library.claim(_track("a"), "salsa")  # sigue 'running' para siempre
    monkeypatch.setattr(library, "get_path", lambda video_id: None)
    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    with pytest.raises(TimeoutError, match="no terminó"):
        service.DownloadService().fetch_now(_track("a"), "salsa")


def test_fetch_now_failure_is_recorded_and_reported(monkeypatch, collect, events):
    def fake(track, genre, pacer, **kwargs):
        err = Exception("sin internet")
        err.attempts = 1
        raise err

    monkeypatch.setattr(downloader, "download_with_retry", fake)
    svc = service.DownloadService(collect)

    with pytest.raises(Exception, match="sin internet"):
        svc.fetch_now(_track("a"), "salsa")

    assert library.queue_counts() == {"failed": 1}
    assert [k for k, _, _ in events] == ["failed"]


# ---------- pausar y reintentar ----------
def test_queue_rows_show_running_then_pending_then_failed_and_hide_done():
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    library.claim_next()  # 'a' se está bajando
    library.mark("c", "done", 1)
    library.mark("d", "failed", 4, "HTTP Error 403")

    rows = library.queue_rows()

    assert [(r["video_id"], r["status"]) for r in rows] == [("a", "running"), ("b", "pending"), ("d", "failed")]
    assert rows[2]["error"] == "HTTP Error 403" and rows[2]["attempts"] == 4
    assert len(library.queue_rows(limit=2)) == 2


def test_pause_stops_new_songs_but_lets_the_ones_in_progress_finish(monkeypatch, collect, events):
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    started, release = [], threading.Event()

    def slow(track, genre, pacer, **kwargs):
        started.append(track["video_id"])
        release.wait(5)
        return Path(f"{track['video_id']}.m4a"), 1

    monkeypatch.setattr(downloader, "download_with_retry", slow)
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    svc = service.DownloadService(collect, persistent=True, workers=2)
    svc.start()
    assert wait_until(lambda: len(started) == 2)  # los dos trabajadores están ocupados

    svc.pause()
    svc.pause()  # repetir no vuelve a avisar
    release.set()  # lo que ya empezó termina

    assert wait_until(lambda: library.queue_counts().get("done") == 2)
    time.sleep(0.3)  # varias vueltas de los trabajadores: no deben empezar nada nuevo
    assert library.queue_counts() == {"done": 2, "pending": 2}
    assert len(started) == 2
    assert svc.paused and [k for k, _, _ in events].count("paused") == 1

    svc.resume()
    assert wait_until(lambda: library.queue_counts() == {"done": 4})
    assert not svc.paused and [k for k, _, _ in events].count("resumed") == 1
    svc.close()


def test_pause_landing_while_a_worker_is_claiming_does_not_start_the_song(monkeypatch, fake_download):
    """Carrera vista en la ventana: el trabajador ya había mirado 'no hay pausa' cuando se pulsó."""
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    library.enqueue([_track("a")], "salsa")
    svc = service.DownloadService(persistent=True, workers=1)
    real_claim = library.claim_next

    def claim_then_pause():
        row = real_claim()
        svc.pause()  # el usuario pulsa Pausar justo después de que se la asignan
        return row

    monkeypatch.setattr(library, "claim_next", claim_then_pause)
    svc.start()

    assert wait_until(lambda: svc.paused)
    time.sleep(0.3)
    assert fake_download == []  # no se descargó
    assert library.queue_counts() == {"pending": 1}  # volvió a la cola en vez de quedar 'running'
    svc.close()


def test_prioritize_moves_songs_to_the_front_keeping_the_order_given():
    library.enqueue([_track(c) for c in "abcde"], "salsa")
    library.enqueue([_track("z")], "salsa", priority=10)  # un respaldo automático ya va por delante

    changed = library.prioritize(["d", "b"])  # d primero, luego b

    assert changed == 2
    order = [library.claim_next()["video_id"] for _ in range(6)]
    assert order == ["d", "b", "z", "a", "c", "e"]  # incluso por delante del respaldo (prioridad 10)


def test_prioritize_retakes_failed_songs_but_leaves_running_and_done_alone():
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    library.claim_next()  # 'a' running
    library.mark("b", "failed", 4, "HTTP Error 403")
    library.mark("c", "done", 1)

    changed = library.prioritize(["a", "b", "c", "d", "no-existe"])

    assert changed == 2  # solo b (fallida) y d (pendiente)
    statuses = {r["video_id"]: r["status"] for r in library.queue_rows()}
    assert statuses == {"a": "running", "b": "pending", "d": "pending"}
    assert library.queue_rows()[1]["error"] is None  # el error viejo se limpia al reintentar


def test_remove_from_queue_takes_pending_and_failed_but_never_running():
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    library.claim_next()  # 'a' running
    library.mark("b", "failed", 4, "boom")

    removed = library.remove_from_queue(["a", "b", "c", "no-existe"])

    assert removed == 2  # b (fallida) y c (pendiente); a ya se está bajando
    assert {r["video_id"] for r in library.queue_rows()} == {"a", "d"}


def test_removed_song_can_be_suggested_and_queued_again():
    library.enqueue([_track("a")], "salsa")
    assert "a" in {vid for vid, _, _ in library.known()}

    library.remove_from_queue(["a"])

    assert "a" not in {vid for vid, _, _ in library.known()}
    assert library.enqueue([_track("a")], "salsa") == 1


def test_a_worker_claiming_wins_over_a_removal_that_arrives_late():
    library.enqueue([_track("a")], "salsa")
    library.claim_next()  # el trabajador llegó primero

    assert library.remove_from_queue(["a"]) == 0
    assert library.queue_counts() == {"running": 1}


def test_service_prioritize_changes_download_order_and_remove_skips_songs(fake_download):
    library.enqueue([_track(c) for c in "abcd"], "salsa")
    svc = service.DownloadService(persistent=False, workers=1)

    assert svc.prioritize(["c"]) == 1
    assert svc.remove(["a"]) == 1
    svc.start()
    svc.wait_idle()
    svc.close()

    assert [c["video_id"] for c in fake_download] == ["c", "b", "d"]  # 'a' nunca se bajó


def test_unclaim_only_touches_running_songs():
    library.enqueue([_track("a"), _track("b")], "salsa")
    library.claim_next()  # 'a' running
    library.mark("b", "failed", 4, "boom")

    library.unclaim("a")
    library.unclaim("b")  # una fallida no vuelve a pendiente por esta vía

    assert library.queue_counts() == {"pending": 1, "failed": 1}


def test_paused_before_anything_starts_stays_idle_until_resumed(monkeypatch, fake_download):
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    svc = service.DownloadService(persistent=True, workers=2)
    svc.pause()
    svc.start()

    svc.submit([_track("a"), _track("b")], "salsa")
    time.sleep(0.3)
    assert library.queue_counts() == {"pending": 2}  # en pausa: nada empieza

    svc.resume()
    assert wait_until(lambda: library.queue_counts() == {"done": 2})
    svc.close()


def test_retry_failed_puts_them_back_in_the_queue_and_downloads_them(monkeypatch, fake_download):
    monkeypatch.setattr(service, "IDLE_POLL", 0.05)
    library.enqueue([_track("a"), _track("b")], "salsa")
    library.mark("a", "failed", 4, "timed out")
    library.mark("b", "failed", 4, "HTTP Error 403")
    svc = service.DownloadService(persistent=True, workers=2)
    svc.start()
    time.sleep(0.2)
    assert library.queue_counts() == {"failed": 2}  # las fallidas no se reintentan solas

    assert svc.retry_failed() == 2

    assert wait_until(lambda: library.queue_counts() == {"done": 2})
    svc.close()


def test_fetch_now_still_works_while_paused(fake_download):
    svc = service.DownloadService()
    svc.pause()

    assert svc.fetch_now(_track("a"), "salsa") == Path("a.m4a")  # el usuario la quiere oír ya
