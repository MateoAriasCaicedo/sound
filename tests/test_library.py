import sqlite3
from contextlib import closing
from pathlib import Path

from app.core import library


def _track(video_id="a", **extra):
    return {"video_id": video_id, "title": f"T{video_id}", "artist": "Artista", **extra}


def test_migration_adds_columns_to_old_database(tmp_path):
    old = tmp_path / "old.db"
    with closing(sqlite3.connect(old)) as conn, conn:
        conn.execute(
            "CREATE TABLE tracks (video_id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,"
            " album TEXT, genre TEXT, duration INTEGER, path TEXT NOT NULL, added_at TEXT)"
        )
        conn.execute("INSERT INTO tracks (video_id, title, artist, path) VALUES ('x', 'T', 'A', 'p.m4a')")

    library.connect(old).close()

    with closing(library.connect(old)) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)")}
        survivor = conn.execute("SELECT title FROM tracks WHERE video_id = 'x'").fetchone()
    assert {"year", "has_cover"} <= columns
    assert survivor["title"] == "T"  # los datos viejos siguen ahí


def test_add_track_stores_year_and_cover_flag():
    library.add_track(_track("a", year=1968, has_cover=True), Path("a.m4a"))
    library.add_track(_track("b", has_cover=False), Path("b.m4a"))

    with closing(library.connect()) as conn:
        rows = {r["video_id"]: (r["year"], r["has_cover"]) for r in conn.execute("SELECT * FROM tracks")}
    assert rows == {"a": (1968, 1), "b": (None, 0)}
    assert [t["video_id"] for t in library.without_cover()] == ["b"]


def test_owner_ignores_case_like_windows():
    library.add_track(_track("a"), Path("C:/Music/Artista - Tema.m4a"))

    assert library.owner(Path("c:/music/ARTISTA - tema.m4a")) == "a"
    assert library.owner(Path("C:/Music/otro.m4a")) is None


def test_all_tracks_sorted_by_artist_then_title_ignoring_case():
    library.add_track({**_track("a", ), "artist": "beta", "title": "Z"}, Path("a.m4a"))
    library.add_track({**_track("b"), "artist": "Alfa", "title": "y"}, Path("b.m4a"))
    library.add_track({**_track("c"), "artist": "Alfa", "title": "X"}, Path("c.m4a"))

    assert [t["video_id"] for t in library.all_tracks()] == ["c", "b", "a"]
    assert set(library.all_tracks()[0]) == {
        "video_id", "title", "artist", "album", "genre", "duration", "path"}


def test_database_uses_wal_for_concurrent_readers_and_writers():
    with closing(library.connect()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_is_prepared_once_per_database(monkeypatch):
    library.connect().close()
    monkeypatch.setattr(library, "_migrate", lambda conn: (_ for _ in ()).throw(AssertionError("repetido")))

    library.connect().close()  # ya preparada: no vuelve a migrar


def test_database_deleted_while_running_is_prepared_again():
    library.connect().close()
    for suffix in ("", "-wal", "-shm"):
        Path(str(library.DB_PATH) + suffix).unlink(missing_ok=True)

    with closing(library.connect()) as conn:  # sin esto fallaría con 'no such table'
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_known_combines_downloaded_and_queued_without_repeats():
    library.add_track(_track("a"), Path("a.m4a"))
    library.enqueue([_track("a"), _track("b")], "salsa")

    assert sorted(video_id for video_id, _, _ in library.known()) == ["a", "b"]


def test_export_m3u_writes_existing_tracks_and_skips_missing(tmp_path):
    present = tmp_path / "a.m4a"
    present.write_bytes(b"x")
    library.add_track(_track("a", duration=180), present)
    library.add_track(_track("b", duration=60), tmp_path / "b.m4a")  # no existe

    dest = tmp_path / "lista.m3u"
    assert library.export_m3u(dest) == 1

    text = dest.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n")
    assert "#EXTINF:180,Artista - Ta" in text
    assert str(present.resolve()) in text
    assert "Tb" not in text


def test_missing_files_lists_and_removes_entries_without_file(tmp_path):
    present = tmp_path / "a.m4a"
    present.write_bytes(b"x")
    library.add_track(_track("a"), present)
    library.add_track(_track("b"), tmp_path / "b.m4a")

    assert [t["video_id"] for t in library.missing_files()] == ["b"]
    assert library.remove_missing() == 1
    assert [t["video_id"] for t in library.all_tracks()] == ["a"]

