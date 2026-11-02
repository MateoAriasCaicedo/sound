import threading
from contextlib import closing
from pathlib import Path

import pytest
import requests
from mutagen.mp4 import MP4, MP4Cover

from app.core import downloader, library


def _track(video_id="id1", title="Titulo", artist="Artista", **extra):
    return {"video_id": video_id, "title": title, "artist": artist, "album": None,
            "duration": 200, "cover": "https://example.test/cover.jpg", **extra}


class FakeYDL:
    """Sustituye a yt_dlp.YoutubeDL: escribe un .m4a donde yt-dlp lo habría dejado."""

    instances: list = []
    seconds = 200
    payloads: list = []  # contenidos a entregar antes del m4a válido (p. ej. un archivo corrupto)
    fail_next: Exception | None = None
    build = None

    def __init__(self, opts):
        self.opts = opts
        self.params = dict(opts)
        self.downloaded = []
        FakeYDL.instances.append(self)

    def download(self, urls):
        if FakeYDL.fail_next:
            error, FakeYDL.fail_next = FakeYDL.fail_next, None
            raise error
        video_id = urls[0].split("v=")[1]
        self.downloaded.append(video_id)
        path = Path(self.params["outtmpl"]["default"].replace("%(ext)s", "m4a"))
        path.write_bytes(FakeYDL.payloads.pop(0) if FakeYDL.payloads else FakeYDL.build(FakeYDL.seconds))
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.data


@pytest.fixture
def ytdl(monkeypatch, make_m4a):
    FakeYDL.instances, FakeYDL.seconds, FakeYDL.payloads, FakeYDL.fail_next = [], 200, [], None
    FakeYDL.build = staticmethod(make_m4a)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        downloader.ytsearch, "song_details", lambda video_id: {"album": "Cosa Nuestra", "year": 1968}
    )
    monkeypatch.setattr(
        downloader.urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(b"\xff\xd8jpeg")
    )
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    return FakeYDL


def _rows():
    with closing(library.connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM tracks ORDER BY rowid")]


def test_downloads_tags_and_registers(ytdl, tmp_path):
    progress = []

    path = downloader.download_track(_track(), "salsa", tmp_path, on_progress=progress.append)

    assert path == tmp_path / "por-genero" / "salsa" / "Artista - Titulo.m4a"
    tags = MP4(path).tags
    assert tags["\xa9nam"] == ["Titulo"] and tags["\xa9ART"] == ["Artista"] and tags["aART"] == ["Artista"]
    assert tags["\xa9alb"] == ["Cosa Nuestra"] and tags["\xa9day"] == ["1968"]
    assert tags["\xa9gen"] == ["salsa"]
    assert bytes(tags["covr"][0]) == b"\xff\xd8jpeg"
    assert progress == [0.5]
    row = _rows()[0]
    assert (row["video_id"], row["album"], row["year"], row["has_cover"]) == ("id1", "Cosa Nuestra", 1968, 1)


def test_skips_album_lookup_when_already_known(ytdl, tmp_path, monkeypatch):
    def boom(video_id):
        raise AssertionError("no debía consultar")

    monkeypatch.setattr(downloader.ytsearch, "song_details", boom)

    downloader.download_track(_track(album="Disco", year=1999), "salsa", tmp_path)

    assert _rows()[0]["year"] == 1999


def test_album_lookup_failure_does_not_fail_the_download(ytdl, tmp_path, monkeypatch):
    def offline(video_id):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(downloader.ytsearch, "song_details", offline)

    path = downloader.download_track(_track(), "salsa", tmp_path)

    assert path.exists()
    assert _rows()[0]["album"] is None


def test_cover_failure_is_recorded_not_fatal(ytdl, tmp_path, monkeypatch):
    def offline(request, timeout=None):
        raise OSError("sin red")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", offline)

    path = downloader.download_track(_track(), "salsa", tmp_path)

    assert "covr" not in MP4(path).tags
    assert [t["video_id"] for t in library.without_cover()] == ["id1"]


def test_missing_cover_url_is_recorded(ytdl, tmp_path):
    downloader.download_track(_track(cover=None), "salsa", tmp_path)

    assert _rows()[0]["has_cover"] == 0


def test_fetch_cover_accepts_png_and_rejects_webp_without_retrying(monkeypatch):
    calls = []

    def serve(data):
        def urlopen(request, timeout=None):
            calls.append(1)
            return FakeResponse(data)
        return urlopen

    monkeypatch.setattr(downloader.urllib.request, "urlopen", serve(b"\x89PNG\r\n\x1a\nxx"))
    assert downloader._fetch_cover("https://x.test/a.png").imageformat == MP4Cover.FORMAT_PNG

    calls.clear()
    monkeypatch.setattr(downloader.urllib.request, "urlopen", serve(b"RIFF....WEBP"))
    assert downloader._fetch_cover("https://x.test/a.webp") is None
    assert len(calls) == 1  # un formato no admitido no mejora reintentando


def test_fetch_cover_retries_three_times_then_gives_up(monkeypatch):
    waits = []

    def offline(request, timeout=None):
        raise OSError("caído")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", offline)

    assert downloader._fetch_cover("https://x.test/a.jpg", sleep=waits.append) is None
    assert waits == [1, 2, 3]


def test_wrong_duration_is_rejected_and_file_removed(ytdl, tmp_path):
    ytdl.seconds = 100

    with pytest.raises(downloader.WrongDuration, match="100s"):
        downloader.download_track(_track(), "salsa", tmp_path)

    assert list(tmp_path.rglob("*.m4a")) == []
    assert _rows() == []
    assert downloader.is_permanent(downloader.WrongDuration("x"))  # reintentar no arregla un video equivocado


def test_duration_within_tolerance_passes(ytdl, tmp_path):
    ytdl.seconds = 205  # +5 s sobre lo anunciado

    assert downloader.download_track(_track(), "salsa", tmp_path).exists()


def test_unknown_expected_duration_skips_the_check(ytdl, tmp_path):
    ytdl.seconds = 5

    assert downloader.download_track(_track(duration=None), "salsa", tmp_path).exists()


def test_corrupt_download_is_removed_and_retried(ytdl, tmp_path, monkeypatch):
    ytdl.payloads = [b"esto no es un mp4"]
    monkeypatch.setattr(downloader, "is_online", lambda *a, **k: True)

    with pytest.raises(downloader.IntegrityError):
        downloader.download_track(_track(), "salsa", tmp_path)
    assert list(tmp_path.rglob("*.m4a")) == []
    assert not downloader.is_permanent(downloader.IntegrityError("x"))

    ytdl.payloads = [b"otra vez corrupto"]
    path, attempts = downloader.download_with_retry(_track(), "salsa", sleep=lambda s: None)
    assert attempts == 2 and path.exists()  # el segundo intento ya recibe un m4a válido


def test_same_artist_and_title_get_distinct_files(ytdl, tmp_path):
    first = downloader.download_track(_track("id1"), "salsa", tmp_path)
    second = downloader.download_track(_track("id2"), "salsa", tmp_path)

    assert first.name == "Artista - Titulo.m4a"
    assert second.name == "Artista - Titulo [id2].m4a"
    assert first.exists() and second.exists()
    assert library.owner(first) == "id1" and library.owner(second) == "id2"


def test_workers_picking_the_same_name_at_once_get_distinct_files(tmp_path):
    """La misma canción en varios álbumes = mismo nombre. Con trabajadores en paralelo ninguno
    está aún en `tracks`, así que solo la reserva evita que se pisen (fallo visto con 2 procesos)."""
    tracks = [_track(f"id{i}", title="La Vida Es Un Carnaval") for i in range(6)]
    barrier = threading.Barrier(len(tracks))
    paths = [None] * len(tracks)

    def pick(i):
        barrier.wait()  # todos eligen en el mismo instante
        paths[i] = downloader._target_path(tmp_path, tracks[i])

    threads = [threading.Thread(target=pick, args=(i,)) for i in range(len(tracks))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({str(p).lower() for p in paths}) == 6  # ninguna repetida
    assert sum(1 for p in paths if "[" not in p.name) == 1  # solo una se queda con el nombre limpio


def test_same_video_keeps_its_name_when_retried(tmp_path):
    first = downloader._target_path(tmp_path, _track("id1"))

    assert downloader._target_path(tmp_path, _track("id1")) == first  # un reintento no se sufija solo


def test_name_reservation_is_released_after_success_and_after_failure(ytdl, tmp_path):
    downloader.download_track(_track("id1"), "salsa", tmp_path)
    assert _reservations() == []

    ytdl.fail_next = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        downloader.download_track(_track("id2", title="Otra"), "salsa", tmp_path)
    assert _reservations() == []  # no queda el nombre bloqueado por una descarga caída


def test_recover_running_clears_stale_reservations():
    library.reserve(Path("C:/Music/Tema.m4a"), "id1")

    library.recover_running()  # nadie más está descargando: la reserva es de una sesión caída

    assert _reservations() == []
    assert library.reserve(Path("C:/Music/Tema.m4a"), "id2") is True


def test_reservation_is_exclusive_and_case_insensitive():
    assert library.reserve(Path("C:/Music/Tema.m4a"), "id1") is True
    assert library.reserve(Path("c:/music/TEMA.m4a"), "id2") is False  # Windows: es el mismo archivo
    assert library.reserve(Path("C:/Music/Tema.m4a"), "id1") is True  # la misma canción puede repetirla


def _reservations():
    with closing(library.connect()) as conn:
        return conn.execute("SELECT path FROM reservations").fetchall()


def test_orphan_file_from_an_interrupted_run_is_reused(ytdl, tmp_path):
    dest = tmp_path / "por-genero" / "salsa"
    dest.mkdir(parents=True)
    (dest / "Artista - Titulo.m4a").write_bytes(ytdl.build(200))  # quedó de una ejecución cortada

    path = downloader.download_track(_track(), "salsa", tmp_path)

    assert path.name == "Artista - Titulo.m4a"  # sin sufijo: nadie más es dueño de ese archivo


def test_long_names_stay_under_max_path_even_with_collision(ytdl, tmp_path):
    long_a = _track("id1", title="T" * 300, artist="A" * 300)
    long_b = _track("id2", title="T" * 300, artist="A" * 300)

    first = downloader.download_track(long_a, "salsa", tmp_path)
    second = downloader.download_track(long_b, "salsa", tmp_path)

    assert first != second
    assert first.exists() and second.exists()
    assert len(str(first)) <= downloader.MAX_PATH
    assert len(str(second)) <= downloader.MAX_PATH


def test_already_downloaded_track_is_not_downloaded_again(ytdl, tmp_path):
    first = downloader.download_track(_track(), "salsa", tmp_path)
    second = downloader.download_track(_track(), "salsa", tmp_path)

    assert first == second
    assert sum(len(y.downloaded) for y in ytdl.instances) == 1


def test_reuses_one_ytdlp_instance_per_thread(ytdl, tmp_path):
    downloader.download_track(_track("id1", title="Uno"), "salsa", tmp_path)
    downloader.download_track(_track("id2", title="Dos"), "salsa", tmp_path)

    assert len(ytdl.instances) == 1
    assert ytdl.instances[0].downloaded == ["id1", "id2"]


def test_each_thread_gets_its_own_instance(ytdl, tmp_path):
    downloader.download_track(_track("id1", title="Uno"), "salsa", tmp_path)
    worker = threading.Thread(
        target=downloader.download_track, args=(_track("id2", title="Dos"), "salsa", tmp_path)
    )
    worker.start()
    worker.join()

    assert len(ytdl.instances) == 2


def test_failed_download_discards_the_instance(ytdl, tmp_path):
    ytdl.fail_next = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        downloader.download_track(_track(), "salsa", tmp_path)

    downloader.download_track(_track(), "salsa", tmp_path)

    assert len(ytdl.instances) == 2  # la instancia dudosa no se reutiliza
