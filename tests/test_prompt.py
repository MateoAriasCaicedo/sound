import pytest
import requests

from app.core import genres, prompt


def _t(video_id, artist, title, album=None):
    return {"video_id": video_id, "artist": artist, "title": title, "album": album, "duration": 200,
            "cover": None, "video_type": "MUSIC_VIDEO_TYPE_ATV"}


# ---------- entender la frase ----------
def test_parse_the_users_example():
    parsed = prompt.parse("música de los ochenta en español")

    assert parsed.years == [(1980, 1989)]
    assert parsed.language == "es"
    assert parsed.genres == [] and parsed.keywords == []
    assert parsed.constrained
    assert parsed.label == "80s · español"


@pytest.mark.parametrize(
    "text, years",
    [
        ("rock de los 80", [(1980, 1989)]),
        ("canciones de los 80's", [(1980, 1989)]),
        ("pop 80s", [(1980, 1989)]),
        ("años 90", [(1990, 1999)]),
        ("música de los noventa", [(1990, 1999)]),
        ("the eighties hits", [(1980, 1989)]),
        ("baladas de los 70 y 80", [(1970, 1989)]),  # dos décadas seguidas se fusionan
        ("rock de los 60 y 90", [(1960, 1969), (1990, 1999)]),  # separadas se mantienen
        ("música de 1985", [(1985, 1985)]),
        ("de 1980 a 1989", [(1980, 1989)]),
        ("1990s", [(1990, 1999)]),
        ("música de los 2000", [(2000, 2009)]),
        ("reggaeton de los dos miles", [(2000, 2009)]),
        ("rock en español", []),  # sin década
    ],
)
def test_parse_decades_and_years(text, years):
    assert prompt.parse(text).years == years


def test_parse_ignores_numbers_that_are_not_decades():
    assert prompt.parse("top 40 de todos los tiempos").years == []
    assert prompt.parse("los 45 mejores").years == []


@pytest.mark.parametrize(
    "text, lang",
    [("pop en español", "es"), ("rock in english", "en"), ("música en inglés", "en"),
     ("bossa nova em portugués", "pt"), ("canciones en castellano", "es"), ("salsa", None)],
)
def test_parse_language(text, lang):
    assert prompt.parse(text).language == lang


def test_parse_genres_and_keywords():
    parsed = prompt.parse("rock y baladas románticas para bailar de los 80")

    assert set(parsed.genres) == {"rock", "baladas", "románticas"}
    assert "bailar" in parsed.keywords
    assert parsed.years == [(1980, 1989)]


def test_parse_without_constraints_is_not_constrained():
    parsed = prompt.parse("salsa para bailar")

    assert not parsed.constrained
    assert parsed.genres == ["salsa"] and parsed.keywords == ["bailar"]


def test_label_combines_genre_decade_and_language():
    assert prompt.parse("rock en español de los 80").label == "Rock · 80s · español"
    assert prompt.parse("balada de 1985").label == "Baladas · 1985"
    assert prompt.parse("algo tranquilo").label == "algo tranquilo"


def test_search_queries_start_with_the_raw_text_and_mix_style_decade_and_language():
    queries = prompt.search_queries(prompt.parse("música de los ochenta en español"))

    assert queries[0] == "música de los ochenta en español"
    assert "años 80 en español" in queries and "rock años 80 en español" in queries
    assert "baladas 80s en español" in queries
    assert len(queries) == len({q.lower() for q in queries})  # sin repetidas


def test_search_queries_use_the_requested_genre_only():
    queries = prompt.search_queries(prompt.parse("rock en español de los 80"))

    assert "rock años 80 en español" in queries
    assert not any("baladas" in q for q in queries)


# ---------- seleccionar ----------
class Catalog:
    """Búsqueda y consulta de año simuladas: sin red."""

    def __init__(self, results, years):
        self.results, self.years = results, years  # results: consulta -> lista de canciones
        self.searches, self.lookups = [], []

    def search(self, query, limit):
        self.searches.append(query)
        return list(self.results.get(query, self.results.get("*", [])))

    def details(self, video_id):
        self.lookups.append(video_id)
        return {"year": self.years.get(video_id), "album": None}


def _run(text, n, catalog, **kwargs):
    return prompt.select_by_prompt(text, n, search=catalog.search, details=catalog.details, **kwargs)


def test_keeps_only_songs_of_the_decade_and_language():
    spanish_80s = _t("a", "Hombres G", "Devuélveme a mi chica")
    spanish_90s = _t("b", "Maná", "Oye Mi Amor")
    english_80s = _t("c", "Cyndi Lauper", "Time After Time")
    catalog = Catalog({"*": [spanish_80s, spanish_90s, english_80s]}, {"a": 1985, "b": 1992, "c": 1983})

    result = _run("música de los ochenta en español", 10, catalog)

    assert [t["video_id"] for t in result.tracks] == ["a"]
    assert result.tracks[0]["year"] == 1985  # el año verificado viaja con la canción
    assert result.stats["wrong_language"] == 1 and "c" not in catalog.lookups  # el inglés ni se consulta
    assert result.stats["wrong_year"] == 1
    assert result.label == "80s · español"


def test_stops_searching_once_it_has_enough():
    songs = [_t(f"s{i}", f"Artista{i}", f"Canción de Amor {i}") for i in range(12)]
    catalog = Catalog({"*": songs}, {s["video_id"]: 1985 for s in songs})

    result = _run("música de los 80 en español", 5, catalog)

    assert len(result.tracks) == 5
    assert len(catalog.searches) == 1  # a la primera ya tenía de sobra


def test_songs_with_no_verifiable_year_are_dropped_and_counted():
    catalog = Catalog({"*": [_t("a", "Artista", "Canción de Amor")]}, {"a": None})

    result = _run("música de los 80 en español", 5, catalog)

    assert result.tracks == [] and result.stats["no_year"] == 1


def test_artist_vote_drops_english_songs_without_clues_in_the_title():
    songs = [
        _t("a", "Guns N' Roses", "Welcome To The Jungle", "Appetite For Destruction"),  # inglés claro
        _t("b", "Guns N' Roses", "Sweet Child O' Mine", "Appetite For Destruction"),  # sin pistas
        _t("c", "Hombres G", "Devuélveme a mi chica"),
    ]
    catalog = Catalog({"*": songs}, {"a": 1991, "b": 1987, "c": 1985})

    result = _run("música de los 80 en español", 10, catalog)

    assert [t["video_id"] for t in result.tracks] == ["c"]
    assert "b" not in catalog.lookups  # 'Sweet Child O' Mine' se descartó por el voto del artista


def test_unsure_language_only_fills_in_when_the_request_is_not_met():
    clear = _t("a", "Hombres G", "Devuélveme a mi chica")
    unsure = _t("b", "Elvis Crespo", "Obsesion")  # sin pistas en el título
    years = {"a": 1985, "b": 1986}

    enough = _run("música de los 80 en español", 1, Catalog({"*": [clear, unsure]}, years))
    assert [t["video_id"] for t in enough.tracks] == ["a"] and enough.note == ""

    needs_more = _run("música de los 80 en español", 5, Catalog({"*": [clear, unsure]}, years))
    assert [t["video_id"] for t in needs_more.tracks] == ["a", "b"]
    assert "sin idioma confirmado" in needs_more.note


def test_language_only_prompts_need_no_year_lookups():
    songs = [_t("a", "Hombres G", "Devuélveme a mi chica"), _t("b", "Cyndi Lauper", "Time After Time")]

    def forbidden(video_id):
        raise AssertionError("sin década no hay nada que verificar")

    result = prompt.select_by_prompt("rock en español", 5, search=lambda q, n: songs, details=forbidden)

    assert [t["video_id"] for t in result.tracks] == ["a"]


def test_respects_what_the_library_already_has():
    catalog = Catalog({"*": [_t("a", "Hombres G", "Devuélveme a mi chica"),
                             _t("b", "Mecano", "Hijo de la Luna")]}, {"a": 1985, "b": 1986})

    result = _run("música de los 80 en español", 5, catalog, skip_ids={"a"},
                  skip_songs={("mecano", "hijo de la luna")})

    assert result.tracks == []  # 'a' por id y 'b' por ser la misma canción con otro video
    assert "a" not in catalog.lookups


def test_caps_songs_per_artist_and_removes_duplicates():
    songs = [_t(f"m{i}", "Mecano", f"Canción de Amor {i}") for i in range(5)]
    songs.append(_t("dup", "Mecano", "Canción de Amor 0 - Remasterizado"))
    catalog = Catalog({"*": songs}, {s["video_id"]: 1984 for s in songs})

    result = _run("música de los 80 en español", 10, catalog, per_artist=2)

    assert len(result.tracks) == 2


def test_filters_out_non_original_versions_before_spending_lookups():
    live = _t("a", "Hombres G", "Devuélveme a mi chica (En Vivo)")
    catalog = Catalog({"*": [live]}, {"a": 1985})

    result = _run("música de los 80 en español", 5, catalog)

    assert result.tracks == [] and catalog.lookups == [] and result.stats["not_original"] == 1


def test_lookup_budget_is_respected(monkeypatch):
    monkeypatch.setattr(prompt, "MAX_LOOKUPS", 3)
    songs = [_t(f"s{i}", f"Artista{i}", f"Canción de Amor {i}") for i in range(20)]
    catalog = Catalog({"*": songs}, {s["video_id"]: 1700 for s in songs})  # ninguna cumple

    _run("música de los 80 en español", 10, catalog)

    assert len(catalog.lookups) == 3


def test_all_year_lookups_failing_raises_instead_of_returning_nothing():
    def offline(video_id):
        raise requests.ConnectionError("sin red")

    songs = [_t("a", "Hombres G", "Devuélveme a mi chica")]

    with pytest.raises(ConnectionError, match="No pude verificar"):
        prompt.select_by_prompt("música de los 80 en español", 5, search=lambda q, n: songs, details=offline)


def test_a_transient_search_error_is_retried(monkeypatch):
    monkeypatch.setattr(prompt.time, "sleep", lambda s: None)
    songs = [_t("a", "Hombres G", "Devuélveme a mi chica")]
    calls = []

    def flaky(query, limit):
        calls.append(query)
        if len(calls) < 3:
            raise requests.ConnectionError("cortó la conexión")
        return songs

    result = prompt.select_by_prompt("rock en español", 1, search=flaky, details=lambda v: {})

    assert [t["video_id"] for t in result.tracks] == ["a"] and len(calls) == 3
    assert result.stats["search_failed"] == 0


def test_a_search_that_keeps_failing_is_skipped_and_the_rest_continue(monkeypatch):
    """Fallo real visto: tras 3 búsquedas buenas, un error de red tiraba toda la petición."""
    monkeypatch.setattr(prompt.time, "sleep", lambda s: None)
    good = [_t(f"s{i}", f"Artista{i}", f"Canción de Amor {i}") for i in range(3)]
    queries = prompt.search_queries(prompt.parse("música en español"))
    assert len(queries) > 2
    broken = queries[1]

    def search(query, limit):
        if query == broken:
            raise requests.ConnectionError("cortó la conexión")
        return good if query == queries[0] else []

    result = prompt.select_by_prompt("música en español", 10, search=search, details=lambda v: {})

    assert len(result.tracks) == 3  # lo ya encontrado no se pierde
    assert result.stats["search_failed"] == 1
    assert "1 búsquedas fallaron" in result.summary(10)


def test_when_every_search_fails_it_says_so_instead_of_returning_nothing(monkeypatch):
    monkeypatch.setattr(prompt.time, "sleep", lambda s: None)

    def offline(query, limit):
        raise requests.ConnectionError("sin red")

    with pytest.raises(ConnectionError, match="No pude buscar"):
        prompt.select_by_prompt("rock en español", 5, search=offline, details=lambda v: {})


def test_cancelling_returns_what_was_found_so_far():
    songs = [_t(f"s{i}", f"Artista{i}", f"Canción de Amor {i}") for i in range(3)]
    catalog = Catalog({"*": songs}, {s["video_id"]: 1700 for s in songs})
    calls = []

    def stop_after_first():
        calls.append(1)
        return len(calls) > 1  # deja pasar la primera búsqueda y cancela antes de la segunda

    result = prompt.select_by_prompt("música en español", 10, search=catalog.search,
                                     details=catalog.details, should_stop=stop_after_first)

    assert len(catalog.searches) == 1  # no siguió buscando
    assert len(result.tracks) == 3  # y lo hallado se conserva
    assert "cancelada" in result.note and "cancelada" in result.summary(10)


def test_reports_progress_per_search():
    seen = []
    songs = [_t(f"s{i}", f"Artista{i}", f"Canción de Amor {i}") for i in range(3)]
    catalog = Catalog({"*": songs}, {s["video_id"]: 1700 for s in songs})

    _run("música de los 80 en español", 50, catalog, on_progress=lambda *a: seen.append(a))

    assert seen and seen[0][1] == 50 and seen[-1][2] == len(seen)  # (halladas, n, búsqueda i, total)


def test_without_decade_or_language_it_uses_the_genre_selector(monkeypatch):
    calls = []
    monkeypatch.setattr(genres, "select_tracks", lambda term, n, *a, **k: calls.append((term, n)) or ("Salsa", []))

    result = prompt.select_by_prompt("salsa para bailar", 30, search=lambda q, n: [], details=lambda v: {})

    assert calls == [("salsa", 30)]
    assert result.label == "Salsa" and "selector de géneros" in result.note


def test_summary_uses_singular_for_one_search():
    catalog = Catalog({"*": [_t("a", "Hombres G", "Devuélveme a mi chica")]}, {"a": 1985})

    text = _run("música de los 80 en español", 1, catalog).summary(1)

    assert "1 búsqueda," in text and "1 canción revisada" in text


def test_summary_explains_what_happened():
    catalog = Catalog({"*": [_t("a", "Hombres G", "Devuélveme a mi chica"),
                             _t("b", "Maná", "Oye Mi Amor")]}, {"a": 1985, "b": 1992})

    result = _run("música de los 80 en español", 5, catalog)
    text = result.summary(5)

    assert text.startswith("1 de 5 pedidas") and "1 de otra época" in text
