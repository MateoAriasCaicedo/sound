import pytest
import requests

from pathlib import Path

from app import cli
from app.core import downloader, genres, health, library


def _tracks(n):
    return [{"video_id": f"v{i}", "title": f"T{i}", "artist": f"A{i}", "album": None,
             "duration": 100, "cover": None} for i in range(n)]


@pytest.fixture
def quiet(monkeypatch):
    """Sin red, sin descargas y con disco de sobra: cada test cambia solo lo que prueba."""
    monkeypatch.setattr(health, "problems", lambda: [])
    monkeypatch.setattr(downloader, "run_queue", lambda **kwargs: library.queue_counts())
    monkeypatch.setattr(downloader, "free_bytes", lambda *args: 10 * 1024**4)
    monkeypatch.setattr(genres, "resolve", lambda term: ("Rock", []))


def test_genero_reports_when_fewer_songs_than_requested(quiet, monkeypatch, capsys):
    monkeypatch.setattr(genres, "select_tracks", lambda term, n, per_artist=3, **kw: ("Rock", _tracks(3)))

    cli.main(["genero", "rock", "-n", "10"])

    assert "Pediste 10 y hay 3" in capsys.readouterr().out


def test_genero_asks_only_for_what_is_not_already_owned(quiet, monkeypatch):
    library.enqueue([{"video_id": "v9", "title": "T9", "artist": "A9"}], "rock")
    captured = {}

    def fake_select(term, n, per_artist=3, **kwargs):
        captured.update(kwargs)
        return "Rock", []

    monkeypatch.setattr(genres, "select_tracks", fake_select)

    cli.main(["genero", "rock"])

    assert "v9" in captured["skip_ids"]
    assert ("a9", "t9") in captured["skip_songs"]


def test_ver_previews_without_queueing(quiet, monkeypatch, capsys):
    monkeypatch.setattr(genres, "select_tracks", lambda term, n, per_artist=3, **kw: ("Rock", _tracks(2)))

    cli.main(["genero", "rock", "--ver"])

    assert "A0 - T0" in capsys.readouterr().out
    assert library.queue_counts() == {}


def test_network_error_gets_a_friendly_message(monkeypatch, capsys):
    def offline(argv=None):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(cli, "main", offline)

    with pytest.raises(SystemExit) as stop:
        cli.entry()

    assert stop.value.code == 1
    assert "No pude conectar con YouTube Music" in capsys.readouterr().err


def test_estado_lists_songs_without_cover(capsys):
    library.add_track({"video_id": "a", "title": "Tema", "artist": "Grupo", "has_cover": False}, "a.m4a")

    cli.main(["estado"])

    out = capsys.readouterr().out
    assert "1 canciones descargadas sin carátula" in out and "Grupo - Tema" in out


def test_run_warns_when_disk_may_not_be_enough(quiet, monkeypatch, capsys):
    library.enqueue(_tracks(3), "rock")
    monkeypatch.setattr(downloader, "free_bytes", lambda *args: 505 * 1024**2)  # 15 MB + 500 MB de reserva

    cli.run()

    assert "Aviso: hacen falta" in capsys.readouterr().out


def test_run_does_not_warn_with_plenty_of_space(quiet, capsys):
    library.enqueue(_tracks(3), "rock")

    cli.run()

    assert "Aviso" not in capsys.readouterr().out


def test_run_explains_when_it_stopped_for_lack_of_space(quiet, monkeypatch, capsys):
    library.enqueue(_tracks(2), "rock")
    monkeypatch.setattr(downloader, "free_bytes", lambda *args: 100 * 1024**2)

    cli.run()

    assert "Se detuvo por poco espacio" in capsys.readouterr().out


def test_run_suggests_updating_when_errors_look_outdated(quiet, capsys):
    library.enqueue(_tracks(1), "rock")
    library.mark("v0", "failed", 4, "ERROR: [youtube] v0: Unable to extract player")

    cli.run()

    assert "actualizar" in capsys.readouterr().out


def test_run_shows_environment_problems(quiet, monkeypatch, capsys):
    monkeypatch.setattr(health, "problems", lambda: ["Node.js no está instalado"])

    cli.run()

    assert "Aviso: Node.js no está instalado" in capsys.readouterr().out


def test_actualizar_exits_with_pip_status(monkeypatch):
    monkeypatch.setattr(health, "update_components", lambda: 0)

    with pytest.raises(SystemExit) as stop:
        cli.main(["actualizar"])

    assert stop.value.code == 0


def test_importar_fetches_and_queues(quiet, monkeypatch, capsys):
    monkeypatch.setattr(cli.importer, "fetch", lambda url, n: ("Mi álbum", _tracks(2)))

    cli.main(["importar", "https://music.youtube.com/playlist?list=PLx", "--genero", "rock"])

    out = capsys.readouterr().out
    assert "Mi álbum: 2 canciones" in out
    assert library.queue_counts().get("pending") == 2


def test_importar_preview_does_not_queue(quiet, monkeypatch, capsys):
    monkeypatch.setattr(cli.importer, "fetch", lambda url, n: ("Mi lista", _tracks(2)))

    cli.main(["importar", "PLx", "--ver"])

    out = capsys.readouterr().out
    assert "A0 - T0" in out
    assert library.queue_counts() == {}


def test_importar_reports_unrecognized_url(quiet, monkeypatch):
    monkeypatch.setattr(cli.importer, "fetch",
                        lambda url, n: (_ for _ in ()).throw(cli.importer.ImportFailure("mala URL")))

    with pytest.raises(SystemExit) as stop:
        cli.main(["importar", "https://example.com"])

    assert "Error: mala URL" in str(stop.value)


def test_exportar_writes_an_m3u(tmp_path, capsys):
    song = tmp_path / "a.m4a"
    song.write_bytes(b"x")
    library.add_track({"video_id": "a", "title": "Tema", "artist": "Grupo", "duration": 90}, song)
    dest = tmp_path / "lista.m3u"

    cli.main(["exportar", str(dest)])

    assert "1 canciones exportadas" in capsys.readouterr().out
    assert "#EXTINF:90,Grupo - Tema" in dest.read_text(encoding="utf-8")


def test_limpiar_removes_missing_files(capsys):
    library.add_track({"video_id": "a", "title": "Tema", "artist": "Grupo"}, Path("no-existe.m4a"))

    cli.main(["limpiar"])

    assert "1 canciones quitadas" in capsys.readouterr().out
    assert library.all_tracks() == []


def test_normalizar_requires_ffmpeg(monkeypatch, capsys):
    monkeypatch.setattr(cli.loudness, "available", lambda: False)

    with pytest.raises(SystemExit) as stop:
        cli.main(["normalizar"])

    assert "ffmpeg" in str(stop.value)


def test_normalizar_measures_only_pending_tracks(tmp_path, monkeypatch, capsys):
    song = tmp_path / "a.m4a"
    song.write_bytes(b"x")
    library.add_track({"video_id": "a", "title": "Tema", "artist": "Grupo"}, song)
    monkeypatch.setattr(cli.loudness, "available", lambda: True)
    monkeypatch.setattr(cli.loudness, "cached_gain", lambda *args: None)
    monkeypatch.setattr(cli.loudness, "scan", lambda tracks, report: len(tracks))

    cli.main(["normalizar"])

    out = capsys.readouterr().out
    assert "Midiendo 1 canciones" in out and "1 canciones medidas" in out

