import json
import time

import pytest

from app.core import genres, ytsearch

_REAL_SEARCH_SONGS = ytsearch.search_songs  # la fixture de abajo la sustituye en cada test


@pytest.fixture(autouse=True)
def no_song_search_top_up(monkeypatch):
    """Por defecto el respaldo por búsqueda de canciones no encuentra nada, para no tocar la red;
    los tests que prueban ese respaldo lo sustituyen por el suyo."""
    monkeypatch.setattr(ytsearch, "search_songs", lambda query, limit: [])


def test_norm_ignores_accents_and_case():
    assert genres.norm("Rock en Español") == "rock en espanol"


def test_resolve_uses_cached_categories_without_network(tmp_path, monkeypatch):
    cache = tmp_path / "genres.json"
    cache.write_text(json.dumps({
        "saved_at": time.time(),
        "playlists": [],
        "categories": {"Rock": "p1"},
    }), encoding="utf-8")
    monkeypatch.setattr(genres, "GENRE_CACHE", cache)

    def offline():
        raise ConnectionError("sin internet")

    monkeypatch.setattr(ytsearch, "genre_categories", offline)
    monkeypatch.setattr(ytsearch, "mood_playlists", lambda params: [{"playlist_id": params, "title": "x"}])

    label, playlists = genres.resolve("Rock")

    assert label == "Rock"
    assert playlists[0]["playlist_id"] == "p1"


def test_resolve_prefers_official_genre(monkeypatch):
    monkeypatch.setattr(ytsearch, "genre_categories", lambda: {"Rock": "p1", "Tropical": "p2"})
    monkeypatch.setattr(ytsearch, "mood_playlists", lambda params: [{"playlist_id": params, "title": "x"}])

    label, playlists = genres.resolve("rock")

    assert label == "Rock"
    assert playlists[0]["playlist_id"] == "p1"


def test_resolve_finds_fine_term_by_playlist_title(monkeypatch):
    monkeypatch.setattr(ytsearch, "genre_categories", lambda: {"Tropical": "p2"})
    monkeypatch.setattr(
        genres, "_all_playlists",
        lambda: [
            {"playlist_id": "a", "title": "Classic Salsa Hits", "genre": "Tropical"},
            {"playlist_id": "b", "title": "Fiesta Latina", "genre": "Tropical"},
        ],
    )

    label, playlists = genres.resolve("Salsa")

    assert label == "Salsa"
    assert [p["playlist_id"] for p in playlists] == ["a"]


def test_resolve_returns_none_without_match(monkeypatch):
    monkeypatch.setattr(ytsearch, "genre_categories", lambda: {"Rock": "p1"})
    monkeypatch.setattr(genres, "_all_playlists", lambda: [])

    assert genres.resolve("polka") is None


def _track(video_id, artist, title):
    return {"video_id": video_id, "artist": artist, "title": title}


def test_select_tracks_caps_artist_and_dedupes(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}, {"playlist_id": "b"}]))
    catalog = {
        "a": [
            _track("1", "Queen", "Bohemian Rhapsody"),
            _track("2", "Queen", "Don't Stop Me Now"),
            _track("3", "Queen", "Somebody to Love"),
            _track("4", "Queen", "Killer Queen"),
            _track("5", "AC/DC", "Back in Black"),
        ],
        "b": [
            _track("1", "Queen", "Bohemian Rhapsody"),
            _track("6", "AC/DC", "Back in Black - Remastered"),
            _track("7", "Nirvana", "Lithium"),
        ],
    }
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: catalog[pid])

    _, picked = genres.select_tracks("rock", n=10, per_artist=3)

    ids = [t["video_id"] for t in picked]
    assert ids == ["1", "2", "3", "5", "7"]  # Queen tope 3, remaster y repetido fuera


def test_select_tracks_falls_back_to_song_search_for_unofficial_genre(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: None)
    asked = []
    monkeypatch.setattr(
        ytsearch, "search_songs",
        lambda query, limit: asked.append((query, limit)) or [_track("1", "Grupo Mazz", "Polkitas")],
    )

    label, picked = genres.select_tracks("polka", n=20)

    assert label == "polka"
    assert asked == [("polka", 80)]  # se pide n*4: el filtro de originales descarta parte
    assert [t["video_id"] for t in picked] == ["1"]


def test_select_tracks_mixes_playlists_before_filling(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}, {"playlist_id": "b"}]))
    catalog = {
        "a": [_track(f"a{i}", f"ArtA{i}", f"TemaA{i}") for i in range(20)],
        "b": [_track(f"b{i}", f"ArtB{i}", f"TemaB{i}") for i in range(20)],
    }
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: catalog[pid])

    _, picked = genres.select_tracks("rock", n=12)  # tope por playlist = 5 -> 5 + 5, resto al rellenar

    firsts = [t["video_id"] for t in picked[:10]]
    assert firsts == [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)]
    assert len(picked) == 12


def _mixed_catalog(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Salsa", [{"playlist_id": "a"}]))
    monkeypatch.setattr(
        ytsearch, "playlist_tracks",
        lambda pid, limit: [
            _track("1", "Willie Colón", "Juana Peña"),
            _track("2", "Fania All Stars", "Me Gusta El Son (Live)"),
            _track("3", "DJ Fulano", "Salsa Mix 2020"),
            _track("4", "Grupo Niche", "Gotas De Lluvia"),
            _track("5", "Alberto Barros", "La Piragua (Cover)"),
        ],
    )


def test_select_tracks_keeps_only_original_recordings(monkeypatch):
    _mixed_catalog(monkeypatch)
    rejected = []

    _, picked = genres.select_tracks("salsa", n=10, rejected=rejected)

    assert [t["video_id"] for t in picked] == ["1", "4"]
    assert sorted((t["video_id"], reason) for t, reason in rejected) == [
        ("2", "live"), ("3", "mix"), ("5", "cover"),
    ]


def test_select_tracks_can_include_alternate_versions(monkeypatch):
    _mixed_catalog(monkeypatch)

    _, picked = genres.select_tracks("salsa", n=10, strict=False)

    assert len(picked) == 5


def test_select_tracks_fills_n_after_discarding(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}]))
    catalog = [_track(f"l{i}", f"Banda{i}", f"Tema{i} (En Vivo)") for i in range(6)]
    catalog += [_track(f"s{i}", f"Grupo{i}", f"Cancion{i}") for i in range(6)]
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: catalog)

    _, picked = genres.select_tracks("rock", n=5)

    assert [t["video_id"] for t in picked] == [f"s{i}" for i in range(5)]


def test_select_tracks_skips_what_the_library_already_has(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}]))
    monkeypatch.setattr(
        ytsearch, "playlist_tracks",
        lambda pid, limit: [
            _track("1", "Queen", "Bohemian Rhapsody"),
            _track("2", "AC/DC", "Back in Black"),
            _track("3", "Nirvana", "Lithium"),
            _track("4", "Queen", "Killer Queen"),
        ],
    )

    _, picked = genres.select_tracks(
        "rock", n=3,
        skip_ids={"1"},                                   # mismo video ya descargado
        skip_songs={("ac/dc", "back in black")},          # mismo tema con otro video_id
    )

    assert [t["video_id"] for t in picked] == ["3", "4"]


def test_second_batch_brings_new_songs_not_the_same_ones(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}]))
    catalog = [_track(str(i), f"Artista{i}", f"Tema{i}") for i in range(10)]
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: catalog)

    _, first = genres.select_tracks("rock", n=4)
    ids = {t["video_id"] for t in first}
    songs = {(genres.norm(t["artist"]), genres._base_title(t["title"])) for t in first}
    _, second = genres.select_tracks("rock", n=4, skip_ids=ids, skip_songs=songs)

    assert {t["video_id"] for t in second}.isdisjoint(ids)
    assert len(second) == 4


def test_known_from_library_merges_downloads_and_queue():
    from pathlib import Path

    from app.core import library

    library.add_track({"video_id": "a", "title": "Gotas De Lluvia (Remastered)", "artist": "Grupo Niche, Invitado"},
                      Path("a.m4a"))
    library.enqueue([{"video_id": "b", "title": "Cali Pachanguero", "artist": "Grupo Niche"}], "salsa")

    ids, songs = genres.known_from_library()

    assert ids == {"a", "b"}
    assert ("grupo niche", "gotas de lluvia") in songs  # artista principal y título sin la decoración
    assert ("grupo niche", "cali pachanguero") in songs


def test_search_songs_respects_the_limit_even_if_the_api_returns_a_full_page(monkeypatch):
    class FakeClient:
        def search(self, query, filter=None, limit=None):
            return [{"videoId": f"v{i}", "title": f"T{i}", "artists": [{"name": "A"}]} for i in range(20)]

    monkeypatch.setattr(ytsearch, "_client", lambda: FakeClient())
    monkeypatch.setattr(ytsearch, "search_songs", _REAL_SEARCH_SONGS)  # este test prueba la real

    assert len(ytsearch.search_songs("x", 5)) == 5
    assert len(ytsearch.search_songs("x", 40)) == 20  # si hay menos de las pedidas, devuelve las que haya


def test_recommended_names_put_colombia_first_without_repeats():
    official = ["African", "Bachata", "Cumbia", "Tropical", "Urbano", "Vallenato", "Zydeco"]

    names = genres.recommended_names(official)

    assert names[0] == "Música colombiana"
    assert names.index("Vallenato") < names.index("Urbano") < names.index("Hip-hop en Español")
    assert names[-2:] == ["African", "Zydeco"]  # el resto, en su orden, al final
    assert len(names) == len({genres.norm(n) for n in names})  # 'Vallenato' o 'Cumbia' no salen dos veces
    assert "Champeta" in names and "Carranga" in names and "Porro" in names


def test_colombian_music_uses_official_playlists_with_colombia_in_the_title(monkeypatch):
    monkeypatch.setattr(genres, "_all_playlists", lambda: [
        {"playlist_id": "a", "title": "Colombian Music Classics", "genre": "Cumbia"},
        {"playlist_id": "b", "title": "Música Tropical Colombiana", "genre": "Tropical"},
        {"playlist_id": "c", "title": "Rock Classics", "genre": "Rock"},
    ])

    label, playlists = genres.resolve("Música colombiana")

    assert label == "Música colombiana"
    assert [p["playlist_id"] for p in playlists] == ["a", "b"]


def test_porro_does_not_match_an_unrelated_playlist_title(monkeypatch):
    monkeypatch.setattr(genres, "_all_playlists", lambda: [
        {"playlist_id": "x", "title": "Club de Porros", "genre": "Hip-hop en Español"}])

    label, playlists = genres.resolve("Porro")

    assert playlists == []  # sin falso positivo: se buscará 'porro colombiano' como canciones


def test_a_curated_genre_without_playlists_is_filled_by_its_own_song_search(monkeypatch):
    searched = []

    def search(query, limit):
        searched.append(query)
        return [_track("1", "Grupo Niche", "Cali Pachanguero"), _track("2", "Joe Arroyo", "La Rebelión")]

    monkeypatch.setattr(ytsearch, "search_songs", search)

    label, tracks = genres.select_tracks("Salsa colombiana", n=2)

    assert label == "Salsa colombiana" and searched == ["salsa colombiana"]
    assert [t["video_id"] for t in tracks] == ["1", "2"]


def test_an_official_genre_that_runs_short_is_topped_up_with_a_song_search(monkeypatch):
    monkeypatch.setattr(ytsearch, "genre_categories", lambda: {"Vallenato": "p"})
    monkeypatch.setattr(ytsearch, "mood_playlists", lambda params: [{"playlist_id": "a", "title": "Puro Vallenato"}])
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: [_track("1", "Binomio de Oro", "La Creciente")])
    searched = []

    def search(query, limit):
        searched.append(query)
        return [_track("2", "Diomedes Díaz", "Sin Medir Distancias"), _track("3", "Jorge Oñate", "No Comprendí Tu Amor")]

    monkeypatch.setattr(ytsearch, "search_songs", search)

    _, tracks = genres.select_tracks("Vallenato", n=3)

    assert [t["video_id"] for t in tracks] == ["1", "2", "3"] and searched == ["Vallenato"]


def test_no_song_search_when_the_playlists_already_fill_the_request(monkeypatch):
    monkeypatch.setattr(ytsearch, "genre_categories", lambda: {"Vallenato": "p"})
    monkeypatch.setattr(ytsearch, "mood_playlists", lambda params: [{"playlist_id": "a", "title": "Puro Vallenato"}])
    monkeypatch.setattr(ytsearch, "playlist_tracks", lambda pid, limit: [_track("1", "Binomio de Oro", "La Creciente")])

    def forbidden(query, limit):
        raise AssertionError("no hacía falta buscar")

    monkeypatch.setattr(ytsearch, "search_songs", forbidden)

    _, tracks = genres.select_tracks("Vallenato", n=1)

    assert [t["video_id"] for t in tracks] == ["1"]


def test_select_tracks_stops_at_n(monkeypatch):
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", [{"playlist_id": "a"}]))
    monkeypatch.setattr(
        ytsearch, "playlist_tracks",
        lambda pid, limit: [_track(str(i), f"Artista{i}", f"Tema{i}") for i in range(30)],
    )

    _, picked = genres.select_tracks("rock", n=7)

    assert len(picked) == 7


def test_sidebar_genres_are_the_curated_list():
    assert genres.sidebar_genres() == [
        "Música popular",
        "Vallenato",
        "Reggaeton",
        "Salsa",
        "Merengue",
        "Música mexicana",
        "Bachata",
        "Parrandera",
        "Cumbia",
        "Champeta",
    ]
