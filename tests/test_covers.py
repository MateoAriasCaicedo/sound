"""Portadas y geometría de la vista de cuadrícula."""
import os
import time

from app.core import covers
from app.ui.cover_grid import grid_metrics


def test_cached_cover_wins_over_download(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    (tmp_path / "abc.bin").write_bytes(b"local")
    called = []
    monkeypatch.setattr(covers, "_download", lambda url: called.append(url) or b"remote")

    assert covers.load({"video_id": "abc", "cover": "https://x.test/a.jpg"}) == b"local"
    assert called == []


def test_remote_cover_is_downloaded_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    monkeypatch.setattr(covers, "_download", lambda url: b"image" if url else None)

    data = covers.load({"video_id": "xyz", "cover": "https://x.test/a.jpg"})

    assert data == b"image"
    assert covers.cached("xyz") == b"image"
    assert covers.cache_path("xyz").parent == tmp_path


def test_cover_without_sources_is_none_and_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    assert covers.load({"video_id": "nada"}) is None
    assert not (tmp_path / "nada.bin").exists()


def test_missing_video_id_never_touches_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    assert covers.load({"title": "sin id"}) is None


def test_oversized_cover_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    monkeypatch.setattr(covers, "MAX_BYTES", 4)
    monkeypatch.setattr(covers, "_download", lambda url: b"demasiado grande")

    assert covers.load({"video_id": "big", "cover": "https://x.test/a.jpg"}) == b"demasiado grande"
    assert covers.cached("big") is None


def test_newer_local_file_refreshes_the_cached_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"audio")
    covers._store("a", b"vieja")
    later = time.time() + 10
    os.utime(audio, (later, later))
    monkeypatch.setattr(covers, "_embedded", lambda path: b"nueva")

    assert covers.load({"video_id": "a", "path": str(audio)}) == b"nueva"
    assert covers.cached("a") == b"nueva"


def test_newer_local_file_without_cover_falls_back_to_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(covers.config, "COVER_DIR", tmp_path)
    audio = tmp_path / "b.m4a"
    audio.write_bytes(b"audio")
    covers._store("b", b"cache")
    later = time.time() + 10
    os.utime(audio, (later, later))
    monkeypatch.setattr(covers, "_embedded", lambda path: None)

    assert covers.load({"video_id": "b", "path": str(audio)}) == b"cache"


def test_grid_metrics_are_coherent_at_several_widths():
    for width in (320, 640, 880, 1120, 1920):
        for k in (1.0, 1.25, 1.5):
            cols, cell, cover, card_h, row_h, pad, gap, x0 = grid_metrics(width, k)
            assert cols >= 1
            assert cell > 0 and cover > 0
            assert card_h > cover
            assert row_h > card_h
            assert x0 >= pad
            # la cuadrícula nunca es más ancha que el espacio disponible
            assert x0 + cols * cell + (cols - 1) * gap <= width


def test_grid_grows_in_columns_with_the_window():
    narrow = grid_metrics(700, 1.0)[0]
    wide = grid_metrics(1600, 1.0)[0]
    assert wide > narrow >= 1
