import pytest
import requests

from app.core import autogenre


class FakeResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": self._results}


def _track(**extra):
    return {"video_id": "id1", "title": "Gotas de Lluvia", "artist": "Grupo Niche",
            "album": "Huellas del Pasado", **extra}


def test_lookup_finds_the_song_and_shortens_the_genre(monkeypatch):
    asked = {}

    def fake_get(url, params, timeout):
        asked.update(url=url, params=params)
        return FakeResponse([{
            "artistName": "Grupo Niche", "trackName": "Gotas de Lluvia",
            "collectionName": "Huellas del Pasado", "primaryGenreName": "Salsa y tropical",
        }])

    monkeypatch.setattr(autogenre.requests, "get", fake_get)

    assert autogenre.lookup(_track()) == "Salsa"
    assert asked["url"] == autogenre.SEARCH_URL
    assert asked["params"]["term"] == "Grupo Niche Gotas de Lluvia"
    assert asked["params"]["country"] == "CO"


def test_lookup_prefers_the_result_from_the_same_album(monkeypatch):
    monkeypatch.setattr(autogenre.requests, "get", lambda *a, **k: FakeResponse([
        {"artistName": "ELENA ROSE", "trackName": "COSITA LINDA",
         "collectionName": "COSITA LINDA - Single", "primaryGenreName": "Electrónica"},
        {"artistName": "ELENA ROSE & Justin Quiles", "trackName": "COSITA LINDA",
         "collectionName": "Bendito Verano", "primaryGenreName": "Música latina"},
    ]))

    assert autogenre.lookup(_track(artist="ELENA ROSE, Justin Quiles", title="COSITA LINDA",
                                   album="Bendito Verano")) == "Latino"


def test_lookup_ignores_a_different_song_or_artist(monkeypatch):
    monkeypatch.setattr(autogenre.requests, "get", lambda *a, **k: FakeResponse([
        {"artistName": "Otro Artista", "trackName": "Gotas de Lluvia",
         "collectionName": "X", "primaryGenreName": "Rock"},
        {"artistName": "Grupo Niche", "trackName": "Otra Canción",
         "collectionName": "X", "primaryGenreName": "Rock"},
    ]))

    assert autogenre.lookup(_track()) is None


def test_lookup_survives_a_network_error(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(autogenre.requests, "get", offline)

    assert autogenre.lookup(_track()) is None


def test_lookup_does_not_ask_for_an_unknown_artist(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no debía preguntar")

    monkeypatch.setattr(autogenre.requests, "get", boom)

    assert autogenre.lookup(_track(artist="Desconocido")) is None


def test_lookup_waits_between_requests(monkeypatch):
    times = iter([100.0, 101.0, 102.0, 104.0])
    sleeps = []
    monkeypatch.setattr(autogenre, "_last_request", 0.0)
    monkeypatch.setattr(autogenre.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(autogenre.time, "sleep", sleeps.append)

    autogenre._wait_turn()
    autogenre._wait_turn()

    assert sleeps == [pytest.approx(2.0)]  # 3 s desde la petición anterior
