from pathlib import Path

import pytest

from app import cli
from app.core import history, library


def add(tmp_path, video_id, title, artist="Artista", **extra):
    path = tmp_path / f"{video_id}.m4a"
    path.write_bytes(b"x")
    library.add_track({"video_id": video_id, "title": title, "artist": artist, "duration": 100, **extra}, path)
    return path


def run(argv, capsys):
    cli.main(argv)
    return capsys.readouterr().out


def test_playlist_flow_by_text_and_id(tmp_path, capsys):
    add(tmp_path, "id1", "Imagine", "John Lennon")
    add(tmp_path, "id2", "Yesterday", "Beatles")

    assert "creada" in run(["playlist", "crear", "Favoritas"], capsys)
    assert "2 añadidas" in run(["playlist", "agregar", "Favoritas", "imagine", "id2"], capsys)
    shown = run(["playlist", "ver", "Favoritas"], capsys)
    assert "1. John Lennon - Imagine" in shown and "2. Beatles - Yesterday" in shown
    assert "Favoritas  (2 canciones)" in run(["playlist", "ver"], capsys)
    assert "1 quitadas" in run(["playlist", "quitar", "Favoritas", "id1"], capsys)
    assert "borrada" in run(["playlist", "borrar", "Favoritas"], capsys)


def test_ambiguous_or_unknown_song_is_reported_not_added(tmp_path, capsys):
    add(tmp_path, "a1", "Amor 1")
    add(tmp_path, "a2", "Amor 2")
    run(["playlist", "crear", "L"], capsys)

    out = run(["playlist", "agregar", "L", "amor", "nada"], capsys)

    assert "coincide con 2 canciones" in out and "a1" in out
    assert "no encontré 'nada'" in out and "0 añadidas" in out


def test_playlist_errors_exit_with_a_message(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["playlist", "ver", "inexistente"])
    assert "no existe la playlist" in str(exc.value)


def test_historial_and_top(capsys):
    assert "Todavía no hay" in run(["historial"], capsys)
    for _ in range(3):
        history.record({"video_id": "a", "title": "Tema A", "artist": "X"})
    history.record({"video_id": "b", "title": "Tema B", "artist": "Y"})

    assert "X - Tema A  (x3)" in run(["historial"], capsys)
    top = run(["top", "-n", "1"], capsys)
    assert "3x  X - Tema A" in top and "Tema B" not in top


def test_duplicados_lists_without_deleting_by_default(tmp_path, capsys):
    keep = add(tmp_path, "a", "Hotel California", "Eagles", album="HC", has_cover=True)
    extra = add(tmp_path, "b", "Hotel California - Remastered", "Eagles")

    out = run(["duplicados"], capsys)

    assert "conservar  a" in out and "repetida   b" in out and "--borrar" in out
    assert keep.exists() and extra.exists()


def test_duplicados_borrar_asks_for_confirmation(tmp_path, capsys, monkeypatch):
    add(tmp_path, "a", "Tema", "X", album="A", has_cover=True)
    extra = add(tmp_path, "b", "Tema", "X")

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert "Cancelado" in run(["duplicados", "--borrar"], capsys)
    assert extra.exists()

    monkeypatch.setattr("builtins.input", lambda prompt: "s")
    assert "1 borradas" in run(["duplicados", "--borrar"], capsys)
    assert not extra.exists()


def test_duplicados_borrar_without_terminal_deletes_nothing(tmp_path, capsys, monkeypatch):
    add(tmp_path, "a", "Tema", "X", album="A", has_cover=True)
    extra = add(tmp_path, "b", "Tema", "X")

    def no_tty(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_tty)
    assert "Cancelado" in run(["duplicados", "--borrar"], capsys)
    assert extra.exists()


def test_duplicados_borrar_si_skips_the_question(tmp_path, capsys):
    add(tmp_path, "a", "Tema", "X", album="A", has_cover=True)
    extra = add(tmp_path, "b", "Tema", "X")

    assert "1 borradas" in run(["duplicados", "--borrar", "--si"], capsys)
    assert not extra.exists()


def test_editar_changes_tags_and_shows_the_difference(tmp_path, make_m4a, capsys):
    path = add(tmp_path, "a", "Viejo", "Antes")
    path.write_bytes(make_m4a(100))

    out = run(["editar", "viejo", "--titulo", "Nuevo", "--anio", "1999"], capsys)

    assert "Actualizada: Antes - Nuevo" in out
    assert "title: Viejo  ->  Nuevo" in out and "year: —  ->  1999" in out
    assert library.all_tracks()[0]["title"] == "Nuevo"


def test_editar_without_changes_or_unknown_song_fails(tmp_path, capsys):
    add(tmp_path, "a", "Tema")
    with pytest.raises(SystemExit, match="indica qué cambiar"):
        cli.main(["editar", "tema"])
    with pytest.raises(SystemExit):
        cli.main(["editar", "no existe", "--titulo", "X"])
