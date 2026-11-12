"""Estanterías de «Inicio» (función pura, sin Tk ni base de datos)."""
from app.ui.home import greeting, section_of, shelves


def _t(vid, added="2026-01-01 00:00:00"):
    return {"video_id": vid, "title": f"Canción {vid}", "artist": "A", "added_at": added}


def _lib(n=12):
    tracks = [_t(str(i), f"2026-01-{i + 1:02d} 10:00:00") for i in range(n)]
    return tracks, {t["video_id"]: t for t in tracks}


def test_each_song_appears_once_in_the_first_shelf_that_takes_it():
    library, by_id = _lib()
    recent = [_t("3"), _t("4")]
    top = [_t("4"), _t("5"), _t("3")]
    rows, sections = shelves(by_id, recent, top, library, [], {"3": 2, "4": 5, "5": 1}, 4, 1)
    vids = [r["video_id"] for r in rows]
    assert len(vids) == len(set(vids))
    assert sections[0] == (0, "Seguir escuchando")
    assert vids[:2] == ["3", "4"]
    assert sections[1] == (2, "Lo que más escuchas")
    assert vids[2] == "5"  # el 4 y el 3 ya estaban en «Seguir escuchando»


def test_shelves_are_capped_and_newest_additions_come_first():
    library, by_id = _lib()
    rows, sections = shelves(by_id, [], [], library, [], {}, 4, 1)
    titles = [title for _start, title in sections]
    assert titles == ["Añadidas hace poco", "Todavía sin escuchar"]
    assert [r["video_id"] for r in rows[:4]] == ["11", "10", "9", "8"]
    assert len(rows) == 8


def test_only_songs_still_in_the_library_are_shown_with_current_data():
    library, by_id = _lib(3)
    by_id["1"] = dict(by_id["1"], title="Título editado")
    recent = [{"video_id": "borrada", "title": "Ya no está"}, {"video_id": "1", "title": "Viejo"}]
    rows, _sections = shelves(by_id, recent, [], [], [], {}, 4, 1)
    assert [r["title"] for r in rows] == ["Título editado"]


def test_unheard_shelf_is_stable_for_the_same_seed():
    library, by_id = _lib(30)
    first, _ = shelves(by_id, [], [], library, [], {}, 5, 7)
    again, _ = shelves(by_id, [], [], library, [], {}, 5, 7)
    assert first == again


def test_greeting_and_section_lookup():
    assert [greeting(h) for h in (6, 13, 22, 3)] == [
        "Buenos días", "Buenas tardes", "Buenas noches", "Buenas noches"]
    sections = [(0, "A"), (4, "B"), (9, "C")]
    assert section_of(sections, 12, 0) == (0, 4, "A")
    assert section_of(sections, 12, 5) == (4, 9, "B")
    assert section_of(sections, 12, 11) == (9, 12, "C")
