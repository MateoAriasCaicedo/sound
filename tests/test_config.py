import sqlite3
from pathlib import Path

from app import __version__
from app.core import config


def _db(path: Path, track_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tracks (video_id TEXT, path TEXT)")
    conn.execute("CREATE TABLE reservations (path TEXT, video_id TEXT)")
    conn.execute("INSERT INTO tracks VALUES ('v1', ?)", (track_path,))
    conn.execute("INSERT INTO reservations VALUES (?, 'v1')", (track_path + ".reserve",))
    conn.commit()
    conn.close()


def test_prepare_creates_every_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(config, "WAVE_DIR", tmp_path / "data" / "waves")
    monkeypatch.setattr(config, "COVER_DIR", tmp_path / "data" / "covers")
    monkeypatch.setattr(config, "LOUDNESS_CACHE", tmp_path / "data" / "loudness.json")
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "data" / "sound.log")

    config.prepare()

    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "music").is_dir()
    assert (tmp_path / "data" / "waves").is_dir()
    assert (tmp_path / "data" / "covers").is_dir()


def test_icon_path_points_to_the_packaged_icon():
    icon = config.icon_path()
    assert icon is not None
    assert icon.name == "sound.ico"
    assert icon.exists()


def test_migrate_moves_music_and_rewrites_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUND_NO_MIGRATE", raising=False)
    old = tmp_path / "old"
    old_music = old / "Music"
    (old_music / "por-genero" / "rock").mkdir(parents=True)
    song = old_music / "por-genero" / "rock" / "A - T.m4a"
    song.write_bytes(b"audio")
    legacy = {
        "db": old / "library.db",
        "music": old_music,
        "genres": old / "genres_cache.json",
        "waves": old / "cache" / "waves",
        "loudness": old / "cache" / "loudness.json",
        "prefs": old / "ui_prefs.json",
        "session": old / "ui_session.json",
        "log": old / "sound.log",
    }
    _db(legacy["db"], str(song))

    data = tmp_path / "data"
    music = tmp_path / "music"
    report = config.migrate_legacy(data, music, legacy)

    assert not report["failed"]
    assert (music / "por-genero" / "rock" / "A - T.m4a").read_bytes() == b"audio"
    conn = sqlite3.connect(data / "library.db")
    assert conn.execute("SELECT path FROM tracks").fetchone()[0].startswith(str(music))
    assert conn.execute("SELECT path FROM reservations").fetchone()[0].startswith(str(music))
    conn.close()
    assert (data / "migrated.json").exists()


def test_migrate_copies_uncheckpointed_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUND_NO_MIGRATE", raising=False)
    old = tmp_path / "old"
    old.mkdir()
    db = old / "library.db"
    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE tracks (video_id TEXT, path TEXT)")
    live.execute("INSERT INTO tracks VALUES ('v1', 'a.m4a')")
    live.commit()  # queda en el -wal hasta que se cierre o se haga checkpoint
    legacy = {key: old / name for key, name in (
        ("db", "library.db"), ("music", "Music"), ("genres", "g.json"),
        ("waves", "cache/waves"), ("loudness", "cache/loudness.json"),
        ("prefs", "p.json"), ("session", "s.json"), ("log", "l.log"))}

    config.migrate_legacy(tmp_path / "data", tmp_path / "music", legacy)

    copy = sqlite3.connect(tmp_path / "data" / "library.db")
    assert copy.execute("SELECT video_id FROM tracks").fetchone()[0] == "v1"
    copy.close()
    live.close()


def test_migrate_merges_into_an_existing_genre_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUND_NO_MIGRATE", raising=False)
    old = tmp_path / "old"
    old_music = old / "Music"
    (old_music / "por-genero" / "rock").mkdir(parents=True)
    (old_music / "por-genero" / "rock" / "nueva.m4a").write_bytes(b"nueva")
    legacy = {key: old / name for key, name in (
        ("db", "library.db"), ("music", "Music"), ("genres", "g.json"),
        ("waves", "cache/waves"), ("loudness", "cache/loudness.json"),
        ("prefs", "p.json"), ("session", "s.json"), ("log", "l.log"))}
    _db(legacy["db"], str(old_music / "por-genero" / "rock" / "nueva.m4a"))
    music = tmp_path / "music"
    (music / "por-genero" / "rock").mkdir(parents=True)
    (music / "por-genero" / "rock" / "existente.m4a").write_bytes(b"existente")

    report = config.migrate_legacy(tmp_path / "data", music, legacy)

    assert not report["failed"]
    assert (music / "por-genero" / "rock" / "nueva.m4a").exists()
    assert (music / "por-genero" / "rock" / "existente.m4a").exists()


def test_migrate_is_idempotent_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUND_NO_MIGRATE", raising=False)
    old = tmp_path / "old"
    old_music = old / "Music"
    old_music.mkdir(parents=True)
    (old_music / "a.m4a").write_bytes(b"viejo")
    legacy = {key: old / name for key, name in (
        ("db", "library.db"), ("music", "Music"), ("genres", "g.json"),
        ("waves", "cache/waves"), ("loudness", "cache/loudness.json"),
        ("prefs", "p.json"), ("session", "s.json"), ("log", "l.log"))}
    _db(legacy["db"], str(old_music / "a.m4a"))
    data, music = tmp_path / "data", tmp_path / "music"

    config.migrate_legacy(data, music, legacy)
    (music / "a.m4a").write_bytes(b"nuevo")
    report = config.migrate_legacy(data, music, legacy)

    assert report == {"moved": [], "failed": []}
    assert (music / "a.m4a").read_bytes() == b"nuevo"


def test_migrate_respects_the_no_migrate_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_NO_MIGRATE", "1")
    old = tmp_path / "old"
    legacy = {key: old / name for key, name in (
        ("db", "library.db"), ("music", "Music"), ("genres", "g.json"),
        ("waves", "cache/waves"), ("loudness", "cache/loudness.json"),
        ("prefs", "p.json"), ("session", "s.json"), ("log", "l.log"))}
    _db(legacy["db"], str(old / "Music" / "a.m4a"))

    assert config.migrate_legacy(tmp_path / "data", tmp_path / "music", legacy) == {
        "moved": [], "failed": []}


def test_ensure_node_path_finds_a_bundled_node(tmp_path, monkeypatch):
    folder = tmp_path / "node"
    folder.mkdir()
    node = folder / ("node.exe" if __import__("os").name == "nt" else "node")
    node.write_bytes(b"")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("PATH", "C:/otro")

    assert config.ensure_node_path() == str(node)
    assert str(folder) in __import__("os").environ["PATH"]


def test_activate_vendor_refuses_a_path_outside_the_vendor_folder(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    outside = tmp_path / "fuera"
    (outside / "yt_dlp").mkdir(parents=True)
    (outside / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (vendor / "current.json").write_text(
        '{"path": "%s", "version": "9.9.9"}' % str(outside).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "VENDOR_DIR", vendor)
    monkeypatch.setattr(config, "_is_frozen", lambda: True)

    assert config.activate_vendor() is None


def test_activate_vendor_accepts_a_path_inside_the_vendor_folder(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    package = vendor / "yt_dlp-9.9.9"
    (package / "yt_dlp").mkdir(parents=True)
    (package / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (vendor / "current.json").write_text(
        '{"path": "%s", "version": "9.9.9"}' % str(package).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "VENDOR_DIR", vendor)
    monkeypatch.setattr(config, "_is_frozen", lambda: True)
    monkeypatch.setattr(config.sys, "path", list(config.sys.path))

    assert config.activate_vendor() == "9.9.9"
    assert str(package) in config.sys.path


def test_version_matches_pyproject():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
