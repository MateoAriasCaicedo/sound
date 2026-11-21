"""Estaciones de Sound: qué se ofrece y cómo suena una estación (sin red ni sonido)."""
from app.core import stations
from app.ui.stations import StationsMixin


def _t(vid, artist="A", genre="Pop", title=None):
    return {"video_id": vid, "title": title or f"Canción {vid}", "artist": artist, "genre": genre,
            "path": f"{vid}.m4a"}


LIBRARY = ([_t(f"s{i}", "Shakira", "Pop") for i in range(6)]
           + [_t(f"j{i}", "Juanes", "Rock") for i in range(5)]
           + [_t(f"k{i}", "Karol G, Otro", "Reggaeton") for i in range(3)]
           + [_t("x0", "Solo", None)])


def test_for_artist_starts_with_their_most_played_song():
    counts = {"s3": 9, "s1": 2}
    item = stations.for_artist("Shakira", LIBRARY, counts, day=1)
    assert item["title"] == "Radio de Shakira" and item["seed"]["video_id"] == "s3"
    assert len(item["extras"]) == stations.EXTRAS
    assert all(t["artist"] == "Shakira" and t["video_id"] != "s3" for t in item["extras"])
    assert stations.for_artist("Nadie", LIBRARY, counts, day=1) is None


def test_suggest_mixes_artists_songs_liked_and_genres_without_repeating_seeds():
    top = [{"video_id": v} for v in ("s3", "j1", "s1", "k0", "gone")]
    counts = {"s3": 9, "j1": 5, "s1": 2, "k0": 1}
    liked = [{"video_id": v} for v in ("j2", "s4", "k1")]
    out = stations.suggest(top, liked, LIBRARY, counts, day=3, sidebar_genres=["Salsa"])
    kinds = [s["kind"] for s in out]
    titles = [s["title"] for s in out]
    assert titles[:3] == ["Radio de Shakira", "Radio de Juanes", "Radio de Karol G"]
    assert "track" in kinds and "liked" in kinds and "genre" in kinds
    assert "Radio Pop" in titles and not any("Sin género" in t for t in titles)
    seeds = [s["seed"]["video_id"] for s in out]
    assert len(seeds) == len(set(seeds))  # cada estación empieza por una canción distinta
    assert out == stations.suggest(top, liked, LIBRARY, counts, day=3, sidebar_genres=[])


def test_without_anything_yours_the_sidebar_genres_are_offered():
    out = stations.suggest([], [], [], {}, day=1, sidebar_genres=["Vallenato", "Salsa"])
    assert [(s["kind"], s["title"], s["seed"]) for s in out] == [
        ("yt_genre", "Radio Vallenato", None), ("yt_genre", "Radio Salsa", None)]
    assert out[0]["genre"] == "Vallenato"


def test_for_track_title():
    assert stations.for_track(_t("a", title="Vivir mi vida"))["title"] == \
        "Radio de «Vivir mi vida»"


# ---------- sonar una estación, con una ventana de mentira ----------
class _PQ:
    def __init__(self):
        self.ctx, self.auto_ctx, self.album, self.order = [], False, False, []

    def make_order(self, first):
        self.order = list(range(len(self.ctx)))


class _Toast:
    def __init__(self):
        self.shown = []

    def show(self, text, *a, **k):
        self.shown.append(text)


class _FakeApp(StationsMixin):
    def __init__(self, autoplay=False, online=True):
        self._init_stations()
        self.pq = _PQ()
        self.autoplay, self.online = autoplay, online
        self.queue_why = {}
        self.toast = _Toast()
        self.status = type("S", (), {"configure": lambda self, text="": None})()
        self.played, self.seeded, self.page_changes = [], [], 0
        self.library = LIBRARY
        self.q = []

    def _seed(self, t):
        self.seeded.append(t["video_id"])
        self.queue_why = {}

    def _play_track(self, t, how="manual"):
        self.played.append(t["video_id"])

    def _radio_page_changed(self, favorites=False):
        self.page_changes += 1

    def _genre_of(self, t):
        return t.get("genre")


def test_a_station_keeps_going_even_with_the_automatic_queue_off():
    app = _FakeApp(autoplay=False)
    item = stations.for_artist("Juanes", LIBRARY, {"j2": 4}, day=1)
    app.start_station(item)
    assert app.played == ["j2"] and app.seeded == ["j2"]
    assert app.pq.auto_ctx and [t["video_id"] for t in app.pq.ctx][0] == "j2"
    assert len(app.pq.ctx) == 1 + stations.EXTRAS
    assert all(app.queue_why[t["video_id"]] for t in item["extras"])
    assert app._play_from == "Radio de Juanes"
    assert app._station_keeps_going()  # aunque la cola automática esté desactivada
    app._end_station()
    assert not app._station_keeps_going() and app.station_now is None


def test_track_station_uses_songs_of_the_same_genre():
    app = _FakeApp()
    app.start_track_station(LIBRARY[0])
    assert app.station_now["title"] == "Radio de «Canción s0»"
    assert all(t["genre"] == "Pop" for t in app.pq.ctx)


def test_a_genre_station_without_library_waits_for_its_seed():
    app = _FakeApp()
    calls = []
    app._fetch_station_seed = lambda item: calls.append(item["id"]) or \
        app._station_pending.setdefault(item["id"], item)
    item = stations.suggest([], [], [], {}, 1, ["Salsa"])[0]
    app.start_station(item)
    assert calls == [item["id"]] and not app.played
    tracks = [_t(f"y{i}", f"Artista {i}", None) for i in range(8)]
    app._station_seed_loaded(item["id"], tracks)
    assert app.played == ["y0"] and app.station_now["title"] == "Radio Salsa"
    assert all(t["_genre"] == "Salsa" for t in tracks)
    app._station_seed_loaded("otra", tracks)  # nada pendiente: no hace nada
    assert app.played == ["y0"]


def test_a_genre_station_needs_internet():
    app = _FakeApp(online=False)
    item = stations.suggest([], [], [], {}, 1, ["Salsa"])[0]
    app.start_station(item)
    assert not app.played and "necesita internet" in app.toast.shown[-1]
