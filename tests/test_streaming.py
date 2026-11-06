"""Caché de URLs de streaming: escritura atómica y poda de entradas caducadas."""
import json
import threading
import time

from app.core import streaming


def test_store_prunes_expired_entries_and_uses_atomic_write(tmp_path, monkeypatch):
    cache_file = tmp_path / "stream_cache.json"
    monkeypatch.setattr(streaming, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(streaming, "_cache", {})

    soon = int(time.time()) + 3600
    streaming._store("viva", f"https://x.test/a?expire={soon}")
    streaming._store("muerta", "https://x.test/b?expire=1")

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert set(data) == {"viva"}
    assert streaming.cached("viva")
    assert streaming.cached("muerta") is None
    assert not list(tmp_path.glob("*.tmp"))


def test_streaming_recreates_ytdlp_when_the_cookie_browser_changes(monkeypatch):
    from app.core import cookies

    monkeypatch.setattr(cookies, "_browser", "")
    monkeypatch.setattr(cookies, "_generation", 0)
    monkeypatch.setattr(streaming, "_local", threading.local())
    created = []

    class FakeYDL:
        def __init__(self, options):
            created.append(options)

    monkeypatch.setattr(streaming, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYDL}))

    streaming._get_ydl()
    cookies.configure("firefox")
    streaming._get_ydl()

    assert len(created) == 2
    assert "cookiesfrombrowser" not in created[0]
    assert created[1]["cookiesfrombrowser"] == ("firefox",)


def test_warm_up_prepares_ytdlp_without_resolving_a_video(monkeypatch):
    """Calentar los hilos no debe hacer peticiones de red ni depender de un vídeo de muestra."""
    submitted = []
    built = []

    class FakePool:
        def submit(self, fn, *args):
            submitted.append(fn)
            fn(*args)

    class FakeYDL:
        def __init__(self, options):
            built.append(options)

    def fail_extract(_video_id):
        raise AssertionError("warm_up no debe extraer vídeos")

    monkeypatch.setattr(streaming, "_now", FakePool())
    monkeypatch.setattr(streaming, "_ahead", FakePool())
    monkeypatch.setattr(streaming, "_local", threading.local())
    monkeypatch.setattr(streaming, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYDL}))
    monkeypatch.setattr(streaming, "_extract", fail_extract)

    streaming.warm_up()

    assert len(submitted) == 5
    assert len(built) == 1  # un hilo, una instancia
    assert streaming._local.ydl is not None
