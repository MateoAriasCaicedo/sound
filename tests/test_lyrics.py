"""Letras: análisis de LRC, línea actual, caché y consulta a LRCLIB sin red."""
import pytest

from app.core import lyrics


@pytest.fixture(autouse=True)
def lyrics_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics.config, "LYRICS_DIR", tmp_path / "lyrics")


def track(vid: str = "v1") -> dict:
    return {"video_id": vid, "title": "Tema", "artist": "Artista",
            "album": "Álbum", "duration": 180}


def test_parse_lrc_handles_metadata_multiple_stamps_and_fractions():
    text = ("[ar:Artista]\n"
            "[00:12.34]Hola\n"
            "[00:15.5]Mundo\n"
            "[00:20.00][00:25.00]Coro\n"
            "[sin marca]")
    assert lyrics.parse_lrc(text) == [
        (12340, "Hola"),
        (15500, "Mundo"),
        (20000, "Coro"),
        (25000, "Coro"),
    ]


def test_parse_lrc_applies_offset_and_ignores_empty_text():
    assert lyrics.parse_lrc("[offset:500]\n[00:01.00]Hola") == [(500, "Hola")]
    assert lyrics.parse_lrc("") == []
    assert lyrics.parse_lrc("solo texto sin tiempos") == []


def test_parse_lrc_removes_word_level_timestamps():
    assert lyrics.parse_lrc("[00:01.00]<00:01.00>Ho<00:01.20>la") == [(1000, "Hola")]


def test_line_at_finds_the_current_line():
    lines = [(1000, "a"), (2000, "b")]
    assert lyrics.line_at(lines, 0) == -1
    assert lyrics.line_at(lines, 999) == -1
    assert lyrics.line_at(lines, 1000) == 0
    assert lyrics.line_at(lines, 1500) == 0
    assert lyrics.line_at(lines, 2000) == 1
    assert lyrics.line_at(lines, 9999) == 1
    assert lyrics.line_at([], 1000) == -1


def test_cache_round_trip_and_safe_ids():
    payload = {"synced": [[1000, "Hola"]], "plain": "Hola", "source": "lrclib",
               "at": 1.0, "negative": False}
    lyrics.store("abc-123_X", payload)
    assert lyrics.cached("abc-123_X") == payload
    assert lyrics.cached("../../secreto") is None
    with pytest.raises(ValueError):
        lyrics.store("../../secreto", payload)


def test_fetch_uses_the_exact_match_and_caches_it(monkeypatch):
    calls = []

    def fake(url, timeout):
        calls.append(url)
        assert "duration=180" in url and "artist_name=Artista" in url
        return {"syncedLyrics": "[00:01.00]Hola", "plainLyrics": "Hola", "duration": 180}

    monkeypatch.setattr(lyrics, "_request", fake)
    payload = lyrics.fetch(track())
    assert payload["synced"] == [(1000, "Hola")]
    assert not payload.get("negative")
    assert lyrics.cached("v1")["synced"] == [[1000, "Hola"]]
    assert len(calls) == 1


def test_fetch_falls_back_to_search_when_get_has_no_match(monkeypatch):
    def fake(url, timeout):
        if "/get?" in url:
            return None
        return [
            {"syncedLyrics": "[00:02.00]Otra", "duration": 181},
            {"plainLyrics": "Lejana", "duration": 500},
        ]

    monkeypatch.setattr(lyrics, "_request", fake)
    assert lyrics.fetch(track())["synced"] == [(2000, "Otra")]


def test_fetch_keeps_plain_lyrics_when_there_are_no_timestamps(monkeypatch):
    monkeypatch.setattr(lyrics, "_request", lambda *_a: {"plainLyrics": "Solo texto"})
    payload = lyrics.fetch(track())
    assert payload["synced"] == [] and payload["plain"] == "Solo texto"


def test_negative_result_is_cached_for_a_while(monkeypatch):
    calls = []

    def fake(url, timeout):
        calls.append(url)
        return None

    monkeypatch.setattr(lyrics, "_request", fake)
    assert lyrics.fetch(track())["negative"] is True
    assert lyrics.fetch(track())["negative"] is True
    assert len(calls) == 2  # get + search, solo la primera vez


def test_network_failure_is_not_cached(monkeypatch):
    def boom(_url, _timeout):
        raise OSError("sin red")

    monkeypatch.setattr(lyrics, "_request", boom)
    assert lyrics.fetch(track()) is None
    assert lyrics.cached("v1") is None
