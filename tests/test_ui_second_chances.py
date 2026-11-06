"""Acciones de la ventana que no deben perder el género ni silenciar un reintento manual."""
from app.ui import main
from app.ui.downloads import DownloadsController


class FakeStatus:
    def __init__(self):
        self.texts = []

    def configure(self, **kwargs):
        self.texts.append(kwargs)


class FakeDownloads:
    def __init__(self):
        self.calls = []

    def start_download(self, track, quiet=False):
        self.calls.append((track, quiet))


class FakeApp:
    damaged = {"v"}

    def __init__(self):
        self.dl = FakeDownloads()
        self.status = FakeStatus()
        self.state = None

    def _selected(self):
        return [{"video_id": "v", "title": "T", "artist": "A", "genre": "salsa",
                 "path": "C:/musica/T.m4a"}]

    def _genre_of(self, track):
        return track.get("genre") or track.get("_genre")

    def _set_state(self, video_id, text):
        self.state = (video_id, text)


def test_redownload_selected_keeps_genre_and_reports_failures(monkeypatch):
    app = FakeApp()
    requeued = []
    monkeypatch.setattr(main.library, "requeue", lambda ids: requeued.append(list(ids)) or 1)

    main.App.redownload_selected(app)

    track, quiet = app.dl.calls[0]
    assert track["_genre"] == "salsa"
    assert "path" not in track
    assert quiet is False  # un reintento pedido por el usuario sí debe avisar si falla
    assert requeued == [["v"]]
    assert "v" in app.damaged  # sigue marcada hasta que la descarga termine bien


def test_configure_cookies_reaches_the_streaming_resolver(monkeypatch):
    from app.core import cookies

    monkeypatch.setattr(cookies, "_browser", "")
    monkeypatch.setattr(cookies, "_generation", 0)

    controller = DownloadsController(FakeApp())
    controller.configure_cookies("  Firefox ")

    assert cookies.browser() == "firefox"
    assert cookies.ydl_options() == {"cookiesfrombrowser": ("firefox",)}


def test_start_download_uses_the_genre_that_comes_from_the_library():
    calls = []
    controller = DownloadsController(FakeApp())
    controller.service = object()  # ya arrancado: no debe reprogramar el intento
    controller.submit = lambda tracks, genre, priority=0, quiet=False: calls.append(
        (tracks, genre, priority, quiet))

    controller.start_download({"video_id": "v", "title": "T", "artist": "A", "genre": "rock"})

    assert calls == [([{"video_id": "v", "title": "T", "artist": "A", "genre": "rock"}],
                      "rock", 0, False)]
