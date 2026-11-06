import atexit
import os
import shutil
import struct
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import pytest

# Antes de importar `app`: los tests nunca deben usar ni migrar los datos reales del usuario.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="sound-tests-"))
os.environ["SOUND_DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["SOUND_MUSIC_DIR"] = str(_TEST_ROOT / "Music")
os.environ["SOUND_LOG"] = str(_TEST_ROOT / "sound.log")
os.environ["SOUND_NO_MIGRATE"] = "1"
atexit.register(lambda: shutil.rmtree(_TEST_ROOT, ignore_errors=True))

from app.core import downloader, library, log, ytsearch  # noqa: E402


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _full(kind: bytes, payload: bytes) -> bytes:
    return _box(kind, struct.pack(">I", 0) + payload)


def build_m4a(seconds: int = 200, timescale: int = 44100) -> bytes:
    """Un .m4a mínimo pero válido para mutagen: dura `seconds`, estéreo, sin audio real.

    Permite probar la validación y el etiquetado sin red ni ffmpeg.
    """
    ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A isom")
    mdhd = _full(b"mdhd", struct.pack(">IIIIHH", 0, 0, timescale, seconds * timescale, 0x55C4, 0))
    hdlr = _full(b"hdlr", struct.pack(">I4s", 0, b"soun") + b"\0" * 12 + b"x\0")
    entry = (
        b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 8
        + struct.pack(">HHHHI", 2, 16, 0, 0, timescale << 16) + _box(b"btrt", b"\0" * 12)
    )
    stsd = _full(b"stsd", struct.pack(">I", 1) + _box(b"mp4a", entry))
    stco = _full(b"stco", struct.pack(">II", 1, 0))
    stbl = _box(b"stbl", stsd + stco)
    moov = _box(b"moov", _box(b"trak", _box(b"mdia", mdhd + hdlr + _box(b"minf", stbl))))
    return ftyp + moov + _box(b"mdat", b"\0" * 16)


@pytest.fixture
def make_m4a():
    return build_m4a


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Ningún test toca la biblioteca real, y cada uno parte sin instancias de yt-dlp heredadas."""
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "test_library.db")
    monkeypatch.setattr(log, "LOG_PATH", tmp_path / "test.log")  # los tests no escriben en el sound.log real
    log.reset()  # cada test escribe en su propia carpeta, sin heredar el manejador anterior
    monkeypatch.setattr(downloader, "MUSIC_DIR", tmp_path / "Music")
    monkeypatch.setattr(downloader, "_local", threading.local())
    # El free_bytes real crea Music/ en el proyecto: aquí siempre hay espacio, y cada test lo cambia si lo necesita.
    monkeypatch.setattr(downloader, "free_bytes", lambda *args: 10 * 1024**4)

    def no_network():
        raise AssertionError("este test llamó a YouTube sin simularlo: sustituye ytsearch.search_songs, etc.")

    # Guardia: ningún test usa la red sin querer (los que simulan YouTube lo sustituyen por su cuenta).
    monkeypatch.setattr(ytsearch, "_client", no_network)
    # Cada test empieza con la caché de búsquedas vacía: nada de resultados de otro test.
    monkeypatch.setattr(ytsearch, "_songs_cache", OrderedDict())
