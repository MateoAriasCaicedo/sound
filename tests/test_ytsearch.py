"""Caché de búsquedas: resultados instantáneos y pre-resultados mientras se escribe."""
from app.core import ytsearch


class FakeClient:
    def __init__(self, total: int = 40):
        self.total = total
        self.calls: list[tuple[str, int]] = []

    def search(self, query, filter=None, limit=20):  # noqa: A002 - firma de ytmusicapi
        self.calls.append((query, limit))
        return [
            {
                "videoId": f"{query}-{i}",
                "title": f"Canción {i}",
                "artists": [{"name": "Artista"}],
                "duration_seconds": 180,
                "thumbnails": [{"url": "https://img/x=w60-h60"}],
            }
            for i in range(min(limit, self.total))
        ]


def test_la_misma_busqueda_no_vuelve_a_pedir_la_red(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ytsearch, "_client", lambda: client)

    first = ytsearch.search_songs("Bad Bunny", 20)
    second = ytsearch.search_songs("  bad   bunny ", 20)

    assert len(client.calls) == 1
    assert [t["video_id"] for t in first] == [t["video_id"] for t in second]
    assert len(second) == 20


def test_pedir_mas_resultados_si_amplia_la_cache(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ytsearch, "_client", lambda: client)

    ytsearch.search_songs("bad bunny", 20)
    full = ytsearch.search_songs("bad bunny", 40)

    assert len(client.calls) == 2
    assert len(full) == 40


def test_la_cache_no_se_contamina_con_quien_muta_los_resultados(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ytsearch, "_client", lambda: client)

    first = ytsearch.search_songs("bad bunny", 20)
    first[0]["_genre"] = "Reggaetón"
    first[0]["title"] = "cambiada"

    second = ytsearch.search_songs("bad bunny", 20)

    assert second[0]["title"] == "Canción 0"
    assert "_genre" not in second[0]


def test_los_pre_resultados_salen_del_prefijo_mas_largo(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ytsearch, "_client", lambda: client)
    ytsearch.search_songs("bad", 20)
    ytsearch.search_songs("bad bunny", 20)

    hit = ytsearch.cached_songs("bad bunny tour")

    assert hit is not None
    query, tracks = hit
    assert query == "bad bunny"
    assert len(tracks) == 20


def test_no_hay_pre_resultados_si_la_consulta_no_continua_la_anterior(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ytsearch, "_client", lambda: client)
    ytsearch.search_songs("bad bunny", 20)

    assert ytsearch.cached_songs("shakira") is None


def test_warm_up_abre_la_conexion_una_sola_vez(monkeypatch):
    calls = []

    class WarmClient:
        def get_search_suggestions(self, query):
            calls.append(query)

    monkeypatch.setattr(ytsearch, "_warmed", False)
    monkeypatch.setattr(ytsearch, "_client", lambda: WarmClient())

    ytsearch.warm_up()
    ytsearch.warm_up()

    assert calls == ["a"]


def test_warm_up_no_revienta_sin_red(monkeypatch):
    monkeypatch.setattr(ytsearch, "_warmed", False)

    def offline():
        raise OSError("sin red")

    monkeypatch.setattr(ytsearch, "_client", offline)

    ytsearch.warm_up()  # no debe lanzar


def test_match_track_numbers_by_id_then_by_base_title():
    album = [{"video_id": "audio1", "title": "Dreams", "track_no": 2},
             {"video_id": "audio2", "title": "Go Your Own Way", "track_no": 5},
             {"video_id": "audio3", "title": "The Chain - 2004 Remaster", "track_no": 7}]
    tracks = [{"video_id": "audio1", "title": "Dreams (2004 Remaster)"},
              {"video_id": "clip2", "title": "Go Your Own Way"},
              {"video_id": "x", "title": "The Chain"},
              {"video_id": "y", "title": "Otra canción"}]
    assert ytsearch.match_track_numbers(tracks, album) == {"audio1": 2, "clip2": 5, "x": 7}
