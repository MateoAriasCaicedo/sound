"""Onda real: niveles a partir del audio, caché en disco y análisis en segundo plano."""
import array
import math
import shutil
import threading
import time
import wave

import pytest

from app.core import waveform


@pytest.fixture(autouse=True)
def wave_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(waveform, "WAVE_DIR", tmp_path / "waves")


def tone(amplitude: int, count: int) -> list[int]:
    return [int(amplitude * math.sin(i * 0.3)) for i in range(count)]


def pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


def test_levels_follow_the_real_volume():
    # silencio, luego fuerte, luego a media potencia
    levels = waveform.levels_from_pcm(pcm([0] * 3000 + tone(20000, 3000) + tone(10000, 3000)), 9)
    assert levels[0] == 0
    assert levels[4] == pytest.approx(1.0, abs=0.02)
    assert levels[7] == pytest.approx(0.5, abs=0.03)


def test_isolated_hit_does_not_flatten_the_song():
    samples = tone(5000, 100_000)
    samples[50_000:50_010] = [32000] * 10
    levels = waveform.levels_from_pcm(pcm(samples), 600)
    assert sorted(levels)[300] > 0.9


@pytest.mark.parametrize("data", [b"", b"\x01", bytes(2000)])
def test_no_audio_or_pure_silence_gives_no_levels(data):
    assert waveform.levels_from_pcm(data) == []


def test_resample_averages_and_stretches():
    assert waveform.resample([0.0, 1.0, 0.0, 1.0], 2) == [0.5, 0.5]
    assert waveform.resample([0.0, 1.0], 4) == [0.0, 0.0, 1.0, 1.0]
    assert waveform.resample([], 4) == []
    assert waveform.resample([1.0], 0) == []


def test_cache_round_trip_and_forget():
    assert waveform.load("abc-123_X") is None
    waveform.save("abc-123_X", [0.0, 0.5, 1.0])
    assert waveform.load("abc-123_X") == pytest.approx([0.0, 0.5, 1.0], abs=0.01)
    waveform.forget("abc-123_X")
    assert waveform.load("abc-123_X") is None


def test_cache_rejects_ids_that_could_escape_the_folder():
    assert waveform.load("../../secreto") is None
    with pytest.raises(ValueError):
        waveform.save("../../secreto", [1.0])


def test_concurrent_saves_leave_one_complete_wave():
    threads = [threading.Thread(target=waveform.save, args=("misma", [i / 10] * waveform.BUCKETS))
               for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    levels = waveform.load("misma")
    assert levels is not None and len(levels) == waveform.BUCKETS
    assert len(set(levels)) == 1  # la última escritura completa, no una mezcla
    assert not list(waveform.WAVE_DIR.glob("*.tmp"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no instalado")
def test_analyze_reads_a_real_audio_file(tmp_path):
    path = tmp_path / "prueba.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(pcm([0] * 8000 + tone(20000, 8000)))  # 1 s de silencio y 1 s de sonido
    levels = waveform.analyze(path)
    half = len(levels) // 2
    assert max(levels[:half - 10]) < 0.05
    assert min(levels[half + 10:]) > 0.8


def test_analyze_reports_an_unreadable_file(tmp_path, monkeypatch):
    def fake_run(*_a, **_k):
        import subprocess
        return subprocess.CompletedProcess([], 1, b"", b"Invalid data")
    monkeypatch.setattr(waveform.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Invalid data"):
        waveform.analyze(tmp_path / "roto.m4a")


class Recorder:
    def __init__(self, expected: int) -> None:
        self.calls = []
        self.expected = expected
        self.done = threading.Event()

    def __call__(self, video_id, levels):
        self.calls.append(video_id)
        if len(self.calls) >= self.expected:
            self.done.set()


def make_analyzer(monkeypatch, recorder, fn):
    monkeypatch.setattr(waveform, "analyze", fn)
    monkeypatch.setattr(waveform, "SWEEP_PAUSE_S", 0)
    analyzer = waveform.Analyzer(recorder)
    analyzer.available = True
    return analyzer


def test_analyzer_saves_and_notifies(monkeypatch):
    rec = Recorder(1)
    analyzer = make_analyzer(monkeypatch, rec, lambda path, **_kw: [0.5, 1.0])
    analyzer.want("cancion", "x.m4a")
    assert rec.done.wait(5)
    assert rec.calls == ["cancion"]
    assert waveform.load("cancion") == pytest.approx([0.5, 1.0], abs=0.01)


def test_urgent_track_does_not_wait_for_the_sweep(monkeypatch):
    rec = Recorder(1)
    gate = threading.Event()

    def slow(path, **_kw):
        if path == "lenta":  # el barrido está atascado en un archivo
            gate.wait(10)
        return [1.0]

    analyzer = make_analyzer(monkeypatch, rec, slow)
    analyzer.sweep([("barrido", "lenta")])
    analyzer.want("la-que-suena", "c", urgent=True)
    try:
        assert rec.done.wait(5)
        assert rec.calls == ["la-que-suena"]
    finally:
        gate.set()


def test_only_the_sweep_runs_at_low_priority(monkeypatch):
    rec = Recorder(2)
    seen = {}

    def fn(path, low_priority=False):
        seen[path] = low_priority
        return [1.0]

    analyzer = make_analyzer(monkeypatch, rec, fn)
    analyzer.sweep([("a", "barrido")])
    analyzer.want("b", "actual", urgent=True)
    assert rec.done.wait(5)
    assert seen == {"barrido": True, "actual": False}


def test_same_song_is_measured_once_even_if_both_queues_ask(monkeypatch):
    rec = Recorder(1)
    started, release = threading.Event(), threading.Event()
    attempts = []

    def fn(path, **_kw):
        attempts.append(path)
        started.set()
        release.wait(10)
        return [1.0]

    analyzer = make_analyzer(monkeypatch, rec, fn)
    analyzer.want("misma", "x", urgent=True)
    assert started.wait(5)
    analyzer.sweep([("misma", "x")])  # el barrido pide la que ya se está midiendo
    deadline = time.monotonic() + 5
    while analyzer._sweep and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    release.set()
    assert rec.done.wait(5)
    assert attempts == ["x"]


def test_failed_track_is_not_retried_and_does_not_stop_the_rest(monkeypatch):
    rec = Recorder(1)
    attempts = []

    def fn(path, **_kw):
        attempts.append(path)
        if path == "mala":
            raise RuntimeError("rota")
        return [1.0]

    analyzer = make_analyzer(monkeypatch, rec, fn)
    analyzer.want("rota", "mala")
    analyzer.want("rota", "mala")
    analyzer.want("buena", "bien")
    assert rec.done.wait(5)
    assert rec.calls == ["buena"]
    assert attempts.count("mala") == 1


def test_sweep_skips_what_is_already_cached(monkeypatch):
    waveform.save("hecha", [1.0])
    rec = Recorder(1)
    attempts = []

    def fn(path, **_kw):
        attempts.append(path)
        return [1.0]

    analyzer = make_analyzer(monkeypatch, rec, fn)
    analyzer.sweep([("hecha", "a"), ("nueva", "b")])
    assert rec.done.wait(5)
    assert attempts == ["b"] and rec.calls == ["nueva"]


def test_without_ffmpeg_nothing_runs(monkeypatch):
    monkeypatch.setattr(waveform.shutil, "which", lambda _name: None)
    analyzer = waveform.Analyzer(lambda *_a: pytest.fail("no debería llamarse"))
    analyzer.want("x", "y", urgent=True)
    assert not analyzer.available and not analyzer._started
