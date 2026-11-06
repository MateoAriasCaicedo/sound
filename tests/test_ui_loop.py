"""Bucle de eventos de la ventana: un fallo no debe dejar de reprogramarse."""
from app.ui.events import EventLoopMixin
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


def test_related_queries_keep_pre_results_while_typing():
    assert App._related_query("bad bunny", "bad")
    assert App._related_query("bad", "bad bunny")
    assert App._related_query("  BAD BUNNY ", "bad bunny")
    assert not App._related_query("bad bunny", "shakira")
    assert not App._related_query("", "bad")


class FakeSearch:
    _related_query = staticmethod(App._related_query)

    def __init__(self, remote=None, src="songs", remote_q="", searching=True):
        self.remote = list(remote or [])
        self.remote_src = src
        self.remote_q = remote_q
        self.searching = searching


def test_pre_results_show_cached_tracks_without_waiting(monkeypatch):
    from app.core import ytsearch

    tracks = [{"video_id": "a", "title": "Bad Bunny"}]
    monkeypatch.setattr(ytsearch, "cached_songs", lambda q: ("bad", tracks))
    app = FakeSearch()

    App._show_pre_results(app, "bad bunny")

    assert app.remote is tracks
    assert app.remote_src == "songs"
    assert app.remote_q == "bad bunny"
    assert app.searching is False


def test_a_more_specific_query_keeps_the_previous_results(monkeypatch):
    from app.core import ytsearch

    monkeypatch.setattr(ytsearch, "cached_songs", lambda q: None)
    app = FakeSearch(remote=[{"video_id": "a"}], remote_q="bad")

    App._show_pre_results(app, "bad bunny")

    assert app.remote == [{"video_id": "a"}]
    assert app.remote_q == "bad bunny"


def test_an_unrelated_query_discards_old_results(monkeypatch):
    from app.core import ytsearch

    monkeypatch.setattr(ytsearch, "cached_songs", lambda q: None)
    app = FakeSearch(remote=[{"video_id": "a"}], remote_q="bad bunny")

    App._show_pre_results(app, "shakira")

    assert app.remote == []
    assert app.remote_src is None
    assert app.remote_q == ""


class FakeEvents(EventLoopMixin):
    _related_query = staticmethod(App._related_query)

    def __init__(self, *events):
        import queue

        self.q = queue.Queue()
        for event in events:
            self.q.put(event)
        self.search_id = 7
        self.searching = True
        self.remote = []
        self.remote_src = None
        self.remote_q = ""
        self.rows = []
        self.yt_genres = ["Rock"]
        self.refreshes = 0
        self.fades = 0
        self.pre_results = 0

    def refresh_view(self):
        self.refreshes += 1

    def _fade_rows(self):
        self.fades += 1

    def _resolve_ahead(self, vids):
        pass

    def _is_remote(self, track):
        return True

    def _show_pre_results(self, q):
        self.pre_results += 1

    @property
    def query(self):
        class Var:
            def get(inner):
                return "bad bunny"

        return Var()


def test_provisional_results_paint_without_waiting_for_the_full_page():
    app = FakeEvents(("results", 7, [{"video_id": "a"}], "songs", "bad", False))

    app._drain_events()

    assert app.remote == [{"video_id": "a"}]
    assert app.searching is False
    assert app.fades == 0  # los pre-resultados no hacen el fundido de una lista nueva


def test_a_stale_prefix_result_becomes_pre_results():
    app = FakeEvents(("results", 2, [{"video_id": "a"}], "songs", "bad", True))

    app._drain_events()

    assert app.pre_results == 1
    assert app.refreshes == 1
