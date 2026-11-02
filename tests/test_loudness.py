"""Ganancia por canción: interpretar ffmpeg, acotar y cachear."""

from app.core import loudness


def test_parse_lufs_takes_the_last_integrated_measurement():
    text = """
    [Parsed_ebur128_0] t: 0.1   I: -23.4 LUFS
    [Parsed_ebur128_0] Summary:
      Integrated loudness:
        I:         -14.2 LUFS
        Threshold: -24.7 LUFS
    """
    assert loudness.parse_lufs(text) == -14.2
    assert loudness.parse_lufs("sin mediciones") is None


def test_gain_targets_minus_fourteen_and_is_clamped():
    assert loudness.gain_db(-14.0) == 0.0
    assert loudness.gain_db(-20.0) == 6.0
    assert loudness.gain_db(-3.0) == -11.0
    assert loudness.gain_db(-100.0) == loudness.MAX_GAIN_DB
    assert loudness.gain_db(40.0) == loudness.MIN_GAIN_DB
    assert loudness.gain_db(None) == 0.0


def test_cached_gain_roundtrip_and_invalidation(tmp_path, monkeypatch):
    song = tmp_path / "a.m4a"
    song.write_bytes(b"1234")
    cache = tmp_path / "loudness.json"
    monkeypatch.setattr(loudness, "measure_file", lambda _path: -18.0)

    gain = loudness.measure_and_store("a", song, cache)
    assert gain == 4.0
    assert loudness.cached_gain("a", song, cache) == 4.0

    song.write_bytes(b"12345")  # cambia el tamaño: la medición ya no vale
    assert loudness.cached_gain("a", song, cache) is None


def test_cached_gain_missing_file_or_entry(tmp_path):
    cache = tmp_path / "loudness.json"
    assert loudness.cached_gain("a", tmp_path / "nope.m4a", cache) is None
    song = tmp_path / "a.m4a"
    song.write_bytes(b"x")
    assert loudness.cached_gain("a", song, cache) is None


def test_measure_and_store_returns_none_without_measurement(tmp_path, monkeypatch):
    song = tmp_path / "a.m4a"
    song.write_bytes(b"x")
    monkeypatch.setattr(loudness, "measure_file", lambda _path: None)
    assert loudness.measure_and_store("a", song, tmp_path / "c.json") is None


def test_scan_skips_missing_files_and_reports_results(tmp_path, monkeypatch):
    present = tmp_path / "a.m4a"
    present.write_bytes(b"x")
    tracks = [{"video_id": "a", "path": str(present)},
              {"video_id": "b", "path": str(tmp_path / "nope.m4a")}]
    monkeypatch.setattr(loudness, "measure_file", lambda _path: -20.0)
    seen = []

    assert loudness.scan(tracks, lambda t, g: seen.append((t["video_id"], g)),
                         tmp_path / "c.json") == 1
    assert seen == [("a", 6.0)]
