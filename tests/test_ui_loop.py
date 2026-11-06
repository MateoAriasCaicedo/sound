"""Bucle de eventos de la ventana: un fallo no debe dejar de reprogramarse."""
from app.ui.main import App


class FakeLoop:
    _pump = App._pump
    _tick = App._tick

    def __init__(self, error=None):
        self.error = error
        self.scheduled = []

    def _drain_events(self):
        if self.error:
            raise self.error

    def _tick_once(self):
        if self.error:
            raise self.error

    def after(self, delay, callback):
        self.scheduled.append(delay)


def test_pump_reschedules_even_when_an_event_handler_crashes():
    app = FakeLoop(RuntimeError("evento roto"))

    App._pump(app)

    assert app.scheduled == [100]


def test_tick_reschedules_even_when_a_frame_crashes():
    app = FakeLoop(ValueError("fotograma roto"))

    App._tick(app)

    assert app.scheduled == [400]


class FakeLibrary:
    def __init__(self):
        self.library = []
        self.lib_by_id = {}
        self._search_cache = {}


def test_upsert_and_search_text_keep_the_cache_in_sync():
    app = FakeLibrary()
    track = {"video_id": "a", "title": "Café", "artist": "José", "album": None}

    App._upsert_track(app, track)

    assert app.lib_by_id["a"] is track
    assert App._search_text(app, track) == "cafe jose"
    assert App._search_text(app, {"video_id": "b", "title": "Otra", "artist": "X"}) == "otra x"
