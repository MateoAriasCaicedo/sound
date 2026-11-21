"""«Descubre en YouTube»: semillas, mezcla, caché y la estantería de Inicio (sin red)."""
import json
import time
from pathlib import Path

from app.core import discover, library
from app.ui.home import DISCOVER, shelves


def _t(vid, artist="A", title=None):
    return {"video_id": vid, "title": title or f"Canción {vid}", "artist": artist}


def test_seeds_come_from_top_songs_still_in_the_library_and_different_artists():
    top = [_t("1", "Shakira"), _t("2", "Shakira"), _t("3", "Juanes"), _t("4", "Karol G"),
           _t("gone", "Otro")]
    seeds = discover.pick_seeds(top, [], {"1", "2", "3", "4"}, day=7)
    assert len(seeds) == 3
    assert "gone" not in {s["video_id"] for s in seeds}
    assert len({s["artist"] for s in seeds}) == 3  # una por artista
    assert seeds == discover.pick_seeds(top, [], {"1", "2", "3", "4"}, day=7)  # estable en el día


def test_without_plays_the_seeds_are_the_recent_songs_and_without_both_none():
    recent = [_t("9", "Bad Bunny")]
    assert [s["video_id"] for s in discover.pick_seeds([], recent, {"9"}, day=1)] == ["9"]
    assert discover.pick_seeds([], [], set(), day=1) == []


def test_mix_interleaves_and_drops_what_you_have_or_is_not_original():
    a = [_t("a1", "X"), _t("a2", "X"), _t("a3", "X"), _t("dup", "Z", "Tema")]
    b = [_t("b1", "Y"), _t("live", "Y", "Algo (En Vivo)"), _t("mine", "W"),
         _t("dup2", "Z", "Tema - Remastered 2011")]
    out = discover.mix([a, b], have_ids={"mine"}, have_songs=set(), per_artist=2)
    vids = [t["video_id"] for t in out]
    assert vids[:2] == ["a1", "b1"]           # una de cada lista a la vez
    assert "a3" not in vids                   # tope de dos por artista
    assert "live" not in vids and "mine" not in vids
    assert "dup" in vids and "dup2" not in vids  # el mismo tema con otro vídeo


def test_mix_skips_songs_already_in_the_library_by_name():
    have_songs = {discover.filters.song_key("Queen", "Bohemian Rhapsody")}
    out = discover.mix([[_t("x", "Queen", "Bohemian Rhapsody (Remastered 2011)")]], set(),
                       have_songs)
    assert out == []


def test_fetch_uses_the_radio_of_the_seeds_and_falls_back_to_popular():
    calls = []

    def radio(vid, limit):
        calls.append(vid)
        if vid == "boom":
            raise RuntimeError("sin red")
        return [_t(f"{vid}-r{i}", f"Artista {vid}{i}") for i in range(3)]

    seeds = [_t("s1"), _t("boom")]
    tracks = discover.fetch(seeds, set(), set(), day=1, radio=radio,
                            popular=lambda day: [[_t("p1")]])
    assert sorted(calls) == ["boom", "s1"]
    assert [t["video_id"] for t in tracks] == ["s1-r0", "s1-r1", "s1-r2"]  # una semilla falló
    popular = discover.fetch([], set(), set(), day=1, radio=radio,
                             popular=lambda day: [[_t("p1", "P")], [_t("p2", "Q")]])
    assert [t["video_id"] for t in popular] == ["p1", "p2"]


def test_cache_round_trip_and_freshness(tmp_path: Path):
    path = tmp_path / "discover.json"
    discover.save_cache("s1,s2", [dict(_t("x"), cover="http://c", _genre="no se guarda")], path)
    tracks, fresh = discover.load_cache("s1,s2", path)
    assert fresh and tracks[0]["video_id"] == "x" and "_genre" not in tracks[0]
    assert discover.load_cache("otra", path)[1] is False  # otras semillas: hay que renovarla
    later = time.time() + discover.CACHE_TTL_S + 1
    tracks, fresh = discover.load_cache("s1,s2", path, now=later)
    assert tracks and not fresh  # vieja, pero se enseña mientras llega la nueva
    path.write_text("{roto", encoding="utf-8")
    assert discover.load_cache("s1,s2", path) == ([], False)
    path.write_text(json.dumps({"saved_at": 1, "tracks": [{"title": "sin id"}]}), "utf-8")
    assert discover.load_cache("popular", path) == ([], False)


def test_known_includes_library_and_queue():
    library.add_track({"video_id": "kn1", "title": "Tema Uno", "artist": "Grupo", "album": None,
                       "genre": None, "duration": 200}, Path("kn1.m4a"))
    library.enqueue([{"video_id": "kn2", "title": "Tema Dos", "artist": "Otro", "duration": 200}],
                    None)
    ids, songs = discover.known()
    assert {"kn1", "kn2"} <= ids
    assert discover.filters.song_key("Grupo", "Tema Uno") in songs


def test_home_puts_discover_right_after_continue_listening():
    library_ = [dict(_t(str(i)), added_at=f"2026-01-{i + 1:02d}") for i in range(6)]
    by_id = {t["video_id"]: t for t in library_}
    found = [_t("yt1", "N1"), _t("3"), _t("yt2", "N2")]  # «3» ya la tienes: no se sugiere
    rows, sections = shelves(by_id, [_t("1")], [], library_, [], {}, 4, 1, discover=found)
    titles = [title for _start, title in sections]
    assert titles[:2] == ["Seguir escuchando", DISCOVER]
    start = sections[1][0]
    assert [r["video_id"] for r in rows[start:start + 2]] == ["yt1", "yt2"]
    rows, sections = shelves(by_id, [], [], library_, [], {}, 4, 1, discover=found)
    assert sections[0] == (0, DISCOVER)  # sin nada que seguir, Descubre va primero
