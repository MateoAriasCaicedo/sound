import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC_DIR = Path(os.environ.get("SOUND_MUSIC_DIR", ROOT / "Music"))
DB_PATH = Path(os.environ.get("SOUND_DB", ROOT / "library.db"))
GENRE_CACHE = Path(os.environ.get("SOUND_GENRE_CACHE", ROOT / "genres_cache.json"))
LOG_PATH =Path(os.environ.get("SOUND_LOG", ROOT / "sound.log"))
WAVE_DIR = Path(os.environ.get("SOUND_WAVE_DIR", ROOT / "cache" / "waves"))
LOUDNESS_CACHE = Path(os.environ.get("SOUND_LOUDNESS_CACHE", ROOT / "cache" / "loudness.json"))


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


MAX_PARALLEL = min(8, _int_env("SOUND_PARALLEL", 3))  # descargas simultáneas
