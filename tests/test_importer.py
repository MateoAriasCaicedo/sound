"""Importación por URL: interpretar enlaces y reunir las canciones."""
import pytest

from app.core import importer


@pytest.mark.parametrize("url, kind, ref", [
    ("https://music.youtube.com/playlist?list=PLabc123", "playlist", "PLabc123"),
    ("https://music.youtube.com/playlist?list=VLPLabc123", "playlist", "PLabc123"),
    ("https://music.youtube.com/playlist?list=OLAK5uy_abc123", "album", "OLAK5uy_abc123"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "song", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "song", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/browse/MPREb_abc123", "album", "MPREb_abc123"),
    ("https://music.youtube.com/channel/UCabc123456789012345678", "artist",
     "UCabc123456789012345678"),
    ("PLabc123", "playlist", "PLabc123"),
    ("MPREb_abc123", "album", "MPREb_abc123"),
    ("OLAK5uy_abc123", "album", "OLAK5uy_abc123"),
    ("dQw4w9WgXcQ", "song", "dQw4w9WgXcQ"),
])
def test_parse_recognizes_urls_and_raw_ids(url, kind, ref):
    parsed = importer.parse(url)
    assert (parsed.kind, parsed.id) == (kind, ref)


@pytest.mark.parametrize("url", ["", "   ", "https://example.com/algo", "esto-no-es-una-url"])
def test_parse_rejects_unknown_input(url):
    with pytest.raises(importer.ImportFailure):
        importer.parse(url)


def test_fetch_playlist_dedupes_and_labels(monkeypatch):
    monkeypatch.setattr(importer.ytsearch, "playlist_details",
                        lambda _id, limit: ("Mi lista", [
                            {"video_id": "a", "title": "A", "artist": "X"},
                            {"video_id": "a", "title": "A repetida", "artist": "X"},
                            {"video_id": "b", "title": "B", "artist": "Y"},
                        ]))

    label, tracks = importer.fetch("PLabc123")

    assert label == "Mi lista"
    assert [t["video_id"] for t in tracks] == ["a", "b"]


def test_fetch_album_converts_audio_playlist_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(importer.ytsearch, "album_browse_id",
                        lambda playlist_id: seen.setdefault("playlist", playlist_id) and "MPREb_x")
    monkeypatch.setattr(importer.ytsearch, "album_details",
                        lambda browse_id, limit: (seen.setdefault("album", browse_id),
                                                  [{"video_id": "a", "title": "A", "artist": "X"}]))

    label, tracks = importer.fetch("OLAK5uy_abc")

    assert seen == {"playlist": "OLAK5uy_abc", "album": "MPREb_x"}
    assert label == "MPREb_x" and [t["video_id"] for t in tracks] == ["a"]


def test_fetch_song_uses_track_metadata(monkeypatch):
    monkeypatch.setattr(importer.ytsearch, "song_track",
                        lambda _id: {"video_id": "a", "title": "Tema", "artist": "Artista"})

    label, tracks = importer.fetch("dQw4w9WgXcQ")

    assert label == "Artista - Tema"
    assert tracks[0]["video_id"] == "a"


def test_fetch_reports_empty_results(monkeypatch):
    monkeypatch.setattr(importer.ytsearch, "playlist_details", lambda _id, limit: ("Vacía", []))

    with pytest.raises(importer.ImportFailure, match="no tiene canciones"):
        importer.fetch("PLabc123")


def test_fetch_wraps_youtube_errors(monkeypatch):
    def boom(_id, limit):
        raise RuntimeError("sin red")

    monkeypatch.setattr(importer.ytsearch, "playlist_details", boom)

    with pytest.raises(importer.ImportFailure, match="No se pudo leer"):
        importer.fetch("PLabc123")
