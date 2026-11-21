"""Radio: directorio de emisoras, favoritas y el modo radio del reproductor (sin red ni sonido)."""
import io
import json
import types

import pytest

from app.core import radio
from app.ui import radio as radio_ui
from app.ui.radio import RadioMixin, live_text
from app.ui.radio_page import list_status, tile_columns


def _raw(uuid, name, url=None, **extra):
    return {"stationuuid": uuid, "name": name, "url_resolved": url or f"http://x/{uuid}",
            "url": "", "favicon": "", "country": "Colombia", "countrycode": "co",
            "state": "Bogotá", "tags": "pop,rock,news,salsa,jazz", "codec": "mp3",
            "bitrate": 128, **extra}


def test_normalize_keeps_what_sound_needs():
    s = radio.normalize(_raw("u1", "  Radio   Uno  "))
    assert s["name"] == "Radio Uno" and s["countrycode"] == "CO" and s["codec"] == "MP3"
    assert s["tags"] == ["pop", "rock", "news", "salsa"]  # como mucho cuatro
    assert radio.normalize(_raw("u2", "Sin dirección", url=" ")) is None
    assert radio.normalize(dict(_raw("u3", "Antigua"), url_resolved="", url="http://y"))["url"] \
        == "http://y"


def test_dedupe_drops_same_url_or_same_name():
    a = radio.normalize(_raw("1", "La X"))
    b = radio.normalize(_raw("2", "la x"))               # mismo nombre
    c = radio.normalize(_raw("3", "Otra", url=a["url"]))  # misma dirección
    d = radio.normalize(_raw("4", "Distinta"))
    assert [s["uuid"] for s in radio.dedupe([a, b, c, d])] == ["1", "4"]


def test_short_name_drops_the_technical_tail():
    long = "Caracol Radio (Bogotá) - 100.9 FM / 810 AM - HJGL / HJCY - PRISA Radio"
    assert radio.short_name({"name": long}) == "Caracol Radio (Bogotá)"
    assert radio.short_name({"name": "La X Medellín 103.9 FM"}) == "La X Medellín 103.9 FM"
    assert radio.short_name({"name": "XY - Radio"}) == "XY - Radio"  # demasiado corto


def test_fetch_logo_skips_svg_and_huge_files():
    class Resp(io.BytesIO):
        def __init__(self, data, kind):
            super().__init__(data)
            self.headers = {"Content-Type": kind}

    png = b"\x89PNG fake"
    assert radio.fetch_logo("https://x/logo.png", opener=lambda r, timeout=None:
                            Resp(png, "image/png")) == png
    assert radio.fetch_logo("https://x/logo.svg", opener=lambda r, timeout=None:
                            Resp(b"<svg/>", "image/svg+xml")) is None
    big = b"x" * (radio.LOGO_MAX_BYTES + 10)
    assert radio.fetch_logo("https://x/big", opener=lambda r, timeout=None:
                            Resp(big, "image/png")) is None
    assert radio.fetch_logo(None) is None and radio.fetch_logo("ftp://x") is None


def test_subtitle():
    assert radio.subtitle(radio.normalize(_raw("1", "A"))) == "Bogotá · Pop, Rock"
    assert radio.subtitle({"country": "Colombia", "tags": []}) == "Colombia"


class _Opener:
    """Hace de urllib: responde por servidor; uno puede fallar."""

    def __init__(self, broken=(), answers=None):
        self.broken, self.answers, self.urls = set(broken), answers or {}, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        assert req.get_header("User-agent").startswith("Sound/")
        host = url.split("/json")[0]
        if host in self.broken:
            raise OSError("caído")
        if url.endswith("/json/servers"):
            return io.BytesIO(json.dumps([{"name": "a.api.test"}, {"name": "b.api.test"}]).encode())
        for key, value in self.answers.items():
            if key in url:
                return io.BytesIO(json.dumps(value).encode())
        return io.BytesIO(b"[]")


@pytest.fixture(autouse=True)
def _no_hosts(monkeypatch):
    monkeypatch.setattr(radio, "_hosts", [])
    monkeypatch.setattr(radio.random, "shuffle", lambda items: items.sort())


def test_a_broken_server_is_skipped():
    opener = _Opener(broken={"https://a.api.test"},
                     answers={"countrycode=CO": [_raw("1", "Uno"), _raw("2", "Dos")]})
    stations = radio.by_country("co", opener=opener)
    assert [s["name"] for s in stations] == ["Uno", "Dos"]
    assert any(u.startswith("https://b.api.test") for u in opener.urls)


def test_everything_down_raises():
    opener = _Opener(broken={"https://a.api.test", "https://b.api.test", radio.DIRECTORY})
    with pytest.raises(radio.RadioError):
        radio.popular(opener=opener)


def test_search_adds_genre_matches_when_names_are_few():
    opener = _Opener(answers={"name=Vallenato": [_raw("1", "Vallenato FM")],
                              "tag=vallenato": [_raw("2", "La Costeña"), _raw("1", "Vallenato FM")]})
    assert [s["name"] for s in radio.search("Vallenato", opener=opener)] == \
        ["Vallenato FM", "La Costeña"]
    assert radio.search("   ", opener=opener) == []


def test_by_tag_prefers_the_country_and_fills_from_the_world():
    opener = _Opener(answers={"countrycode=CO": [_raw("1", "Salsa CO")],
                              "tag=salsa": [_raw("2", "Salsa NY")]})
    names = [s["name"] for s in radio.by_tag("salsa", "co", opener=opener)]
    assert names[0] == "Salsa CO" and "Salsa NY" in names


def test_country_code_falls_back_to_the_language(monkeypatch):
    monkeypatch.setattr(radio, "ctypes", types.SimpleNamespace(
        create_unicode_buffer=lambda n: types.SimpleNamespace(value=""), windll=None))
    monkeypatch.setattr(radio.locale, "getlocale", lambda: ("es_CO", "cp1252"))
    assert radio.country_code() == "CO"
    monkeypatch.setattr(radio.locale, "getlocale", lambda: (None, None))
    assert radio.country_code() is None


def test_favorites_round_trip():
    a, b = radio.normalize(_raw("f1", "Uno")), radio.normalize(_raw("f2", "Dos"))
    radio.add_favorite(a)
    radio.add_favorite(b)
    radio.add_favorite(a)  # otra vez: no se repite
    assert [s["uuid"] for s in radio.favorites() if s["uuid"] in ("f1", "f2")] == ["f1", "f2"]
    assert {"f1", "f2"} <= radio.favorite_ids()
    radio.remove_favorite("f1")
    assert "f1" not in radio.favorite_ids()


def test_page_texts_and_columns():
    assert live_text(65) == "En directo · 1:05" and live_text(3725) == "En directo · 1:02:05"
    assert list_status("fav", 0, "") .startswith("Aún no tienes")
    assert list_status("country", 3, "Colombia") == "3 emisoras · Colombia"
    assert list_status("search", 0, "zzz") == "No se encontraron emisoras para «zzz»"
    cols, side = tile_columns(1000, 1.0)
    assert cols == 6 and side <= 176


# ---------- el modo radio, con una ventana y un reproductor de mentira ----------
class _Player:
    def __init__(self):
        self.loaded = self.paused = False
        self.urls: list[str] = []
        self.pos = 0
        self.fail: str | None = None
        self.ended = False

    def stop(self, fade_ms=0):
        self.loaded = self.paused = False

    def load_url(self, url, key=None, fade_ms=0, overlap=False):
        self.urls.append(url)
        self.loaded, self.paused, self.pos, self.fail, self.ended = True, False, 0, None, False

    def set_next_gain_db(self, db):
        pass

    def set_volume(self, v):
        pass

    def check(self):
        if self.fail:
            raise RuntimeError(self.fail)

    def finished(self):
        return self.ended

    def position(self):
        return self.pos


class _Label:
    def __init__(self):
        self.text = ""

    def configure(self, text=""):
        self.text = text


class _Now:
    def __init__(self):
        self.title = self.artist = ""
        self.liked = None

    def set(self, title, artist):
        self.title, self.artist = title, artist

    def set_liked(self, liked, pop=False):
        self.liked = liked

    def set_artist_link(self, on):
        pass


class _Scale:
    def set_track(self, seed, levels=None):
        self.seed = seed

    def set(self, value):
        pass


class _Toast:
    def __init__(self):
        self.shown = []

    def show(self, text, action="", on_action=None, on_expire=None):
        self.shown.append((text, action, on_action))


class _FakeApp(RadioMixin):
    def __init__(self):
        self._init_radio()
        self.player = _Player()
        self.play_token = 0
        self.playing_vid = "song1"
        self.cur_track = {"video_id": "song1"}
        self.retagged = []
        self.sleep_mode = None
        self.now, self.scale, self.toast = _Now(), _Scale(), _Toast()
        self.time, self.btn_play, self.status = _Label(), _Label(), _Label()
        self.vol = types.SimpleNamespace(get=lambda: 0)
        self.pages = {}
        self.media = []

    def _retag(self, vid):
        self.retagged.append(vid)

    def _sleep_wake(self):
        return False

    def _refresh_sleep_button(self):
        pass

    def _refresh_now_heart(self, pop=False):
        self.now.set_liked(self.radio_station["uuid"] in self.radio_favs)

    def _system_media_track(self, t, cover=None):
        self.media.append(t)

    def _load_cover(self, t, path, token):
        self.cover_asked = (t, token)


STATIONS = [radio.normalize(_raw(f"s{i}", f"Emisora {i}")) for i in range(3)]


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 100.0}
    monkeypatch.setattr(radio_ui.time, "monotonic", lambda: now["t"])
    return now


def test_playing_a_station_leaves_the_song_and_connects(clock):
    app = _FakeApp()
    app.play_station(STATIONS[1], STATIONS)
    assert app.radio_on and app.cur_track is None and app.playing_vid is None
    assert app.retagged == ["song1"] and app.player.urls == [STATIONS[1]["url"]]
    assert app.now.title == "Emisora 1" and app.now.artist.startswith("En directo")
    assert app.time.text == "Conectando…" and app.btn_play.text == "⏸"
    assert app.cover_asked[0]["video_id"] == "radio-s1"
    assert app.media[-1]["artist"] == "Radio en directo"
    app.player.pos = 1200  # empezó a sonar
    clock["t"] += 65
    assert app._radio_tick()
    clock["t"] += 65
    app._radio_tick()
    assert app.time.text == "En directo · 1:05"


def test_a_station_that_does_not_answer_is_retried_and_then_given_up(clock):
    app = _FakeApp()
    app.play_station(STATIONS[0], STATIONS)
    for _ in range(radio_ui.RETRIES):
        clock["t"] += radio_ui.OPEN_TIMEOUT_S + 1
        app._radio_tick()
    assert len(app.player.urls) == 1 + radio_ui.RETRIES
    clock["t"] += radio_ui.OPEN_TIMEOUT_S + 1
    app._radio_tick()
    assert not app.player.loaded and app.time.text == "Sin señal"
    text, action, retry = app.toast.shown[-1]
    assert action == "Reintentar"
    retry()
    assert app.player.loaded and app.player.urls[-1] == STATIONS[0]["url"]


def test_a_dropped_stream_reconnects(clock):
    app = _FakeApp()
    app.play_station(STATIONS[0], STATIONS)
    app.player.pos = 500
    app._radio_tick()
    app.player.fail = "error de medios"
    app._radio_tick()
    assert len(app.player.urls) == 2 and "Reconectando" in app.time.text


def test_pause_disconnects_and_play_goes_back_to_live():
    app = _FakeApp()
    app.play_station(STATIONS[0], STATIONS)
    app._radio_toggle()
    assert not app.player.loaded and app.btn_play.text == "▶" and app.time.text == "En pausa"
    app._radio_toggle()
    assert app.player.loaded and len(app.player.urls) == 2


def test_prev_and_next_walk_the_list_it_was_picked_from():
    app = _FakeApp()
    app.play_station(STATIONS[2], STATIONS)
    app._radio_step(1)
    assert app.radio_station["uuid"] == "s0"  # da la vuelta
    app._radio_step(-1)
    assert app.radio_station["uuid"] == "s2"


def test_the_heart_marks_the_station_as_favorite():
    app = _FakeApp()
    app.play_station(STATIONS[0], STATIONS)
    app.toggle_station_favorite(STATIONS[0])
    assert "s0" in app.radio_favs and app.now.liked is True
    app.toggle_station_favorite(STATIONS[0])
    text, action, undo = app.toast.shown[-1]
    assert "s0" not in app.radio_favs and action == "Deshacer"
    undo()
    assert "s0" in app.radio_favs


def test_a_song_leaves_radio_mode():
    app = _FakeApp()
    app.play_station(STATIONS[0], STATIONS)
    app._leave_radio()
    assert not app.radio_on
    assert not app._radio_tick()  # el tick vuelve a ser de canciones
