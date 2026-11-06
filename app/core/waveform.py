"""Forma de onda real de cada canción descargada.

ffmpeg decodifica el audio a muestras y aquí se reduce a BUCKETS niveles de volumen (RMS), que se
guardan en un archivo pequeño por canción: la primera vez tarda 1 o 2 segundos, las demás es leer
600 bytes. El análisis corre en hilos aparte para no molestar a la reproducción.
Sin ffmpeg instalado, o mientras la canción no esté descargada, simplemente no hay onda.
"""
import array
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

from . import log as _log
from .config import WAVE_DIR

log = _log.get("waveform")
BUCKETS = 600
SAMPLE_RATE = 2000  # basta para medir volumen, y decodificar es más rápido
TIMEOUT_S = 180
SWEEP_PAUSE_S = 0.05
_SAVE_LOCK = threading.Lock()  # dos hilos pueden terminar la misma canción a la vez
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


# ---------- cálculo ----------
def levels_from_pcm(pcm: bytes, buckets: int = BUCKETS) -> list[float]:
    """Volumen de cada tramo (0 a 1) a partir de muestras mono de 16 bits.

    Se normaliza con el percentil 99, no con el máximo: un único golpe aislado no debe aplastar
    el resto de la canción. Vacío si no hay audio o si es silencio absoluto.
    """
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    n = len(samples)
    if not n:
        return []
    buckets = min(buckets, n)
    rms = []
    for b in range(buckets):
        chunk = samples[b * n // buckets:(b + 1) * n // buckets]
        rms.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    ref = sorted(rms)[min(buckets - 1, buckets * 99 // 100)] or max(rms)
    if not ref:
        return []
    return [min(1.0, v / ref) for v in rms]


def resample(levels: list[float], n: int) -> list[float]:
    """Adapta los niveles a `n` barras: promedia si sobran, repite si faltan."""
    m = len(levels)
    if n <= 0 or not m:
        return []
    out = []
    for i in range(n):
        lo = i * m // n
        hi = max(lo + 1, (i + 1) * m // n)
        out.append(sum(levels[lo:hi]) / (hi - lo))
    return out


def level_at(levels: list[float] | None, fraction: float) -> float:
    """Nivel (0 a 1) en una posición de la canción, dada como fracción de 0 a 1."""
    if not levels:
        return 0.0
    i = int(max(0.0, min(1.0, float(fraction))) * len(levels))
    return levels[min(len(levels) - 1, i)]


def summary(levels: list[float] | None) -> dict[str, float]:
    """Resumen de intensidad de una canción a partir de sus niveles RMS.

    `energy` es el volumen medio, `punch` cuánto destacan los picos sobre el centro y `onset` la
    proporción de tramos que suben respecto al anterior. No es tempo ni tonalidad: sirve para
    comparar canciones por intensidad sin dependencias externas.
    """
    if not levels:
        return {"energy": 0.0, "punch": 0.0, "onset": 0.0}
    ordered = sorted(levels)
    middle = ordered[len(ordered) // 2]
    high = ordered[min(len(ordered) - 1, len(ordered) * 9 // 10)]
    rises = sum(1 for a, b in zip(levels, levels[1:]) if b - a > 0.04)
    return {
        "energy": sum(levels) / len(levels),
        "punch": max(0.0, high - middle),
        "onset": rises / max(1, len(levels) - 1),
    }


def analyze(path: str | Path, low_priority: bool = False) -> list[float]:
    """Decodifica el archivo con ffmpeg y devuelve sus niveles. Lanza si no puede."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if low_priority:
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    done = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-i", str(path), "-map", "a:0",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"],
        capture_output=True, timeout=TIMEOUT_S, creationflags=flags, check=False)
    if done.returncode:
        raise RuntimeError(f"ffmpeg terminó con {done.returncode}: "
                           f"{done.stderr.decode('utf-8', 'replace')[:200]}")
    levels = levels_from_pcm(done.stdout)
    if not levels:
        raise RuntimeError("el archivo no tiene audio")
    return levels


# ---------- caché en disco ----------
def _cache_path(video_id: str) -> Path:
    if not _SAFE_ID.fullmatch(video_id):
        raise ValueError(f"identificador no válido: {video_id!r}")
    return WAVE_DIR / f"{video_id}.wave"


def load(video_id: str) -> list[float] | None:
    """Niveles guardados de la canción, o None si aún no se ha analizado."""
    try:
        data = _cache_path(video_id).read_bytes()
    except (OSError, ValueError):
        return None
    return [b / 255 for b in data] if data else None


def save(video_id: str, levels: list[float]) -> None:
    path = _cache_path(video_id)
    data = bytes(round(max(0.0, min(1.0, v)) * 255) for v in levels)
    with _SAVE_LOCK:  # el temporal se comparte entre hilos del mismo proceso
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def forget(video_id: str) -> None:
    try:
        _cache_path(video_id).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        _log.failure(log, f"no se pudo borrar la onda de {video_id}", exc)


# ---------- análisis en segundo plano ----------
class Analyzer:
    """Dos hilos: uno para la canción que suena, que no espera a nadie, y otro para el resto.

    El barrido corre a baja prioridad y, con el equipo ocupado, un solo archivo puede tardar
    decenas de segundos: la canción que estás oyendo no puede quedar detrás. Si saltas varias
    seguidas, se atiende primero la última.
    """

    def __init__(self, on_ready: Callable[[str, list[float]], None]) -> None:
        self.on_ready = on_ready  # se llama desde los hilos del analizador
        self.available = shutil.which("ffmpeg") is not None
        self._urgent: list[tuple[str, str]] = []
        self._sweep: deque[tuple[str, str]] = deque()
        self._wake = threading.Condition()
        self._failed: set[str] = set()
        self._busy: set[str] = set()  # el que suena y el barrido pueden pedir la misma canción
        self._busy_lock = threading.Lock()
        self._started = False
        if not self.available:
            log.info("ffmpeg no está instalado: no habrá formas de onda")

    def want(self, video_id: str, path: str | Path, urgent: bool = False) -> None:
        """Pide analizar una canción; `urgent` no espera al barrido y usa prioridad normal."""
        self._add([(video_id, str(path))], urgent)

    def sweep(self, tracks: Iterable[tuple[str, str]]) -> None:
        """Analiza poco a poco lo que falte, para que la onda ya esté lista al reproducir."""
        self._add(tracks, urgent=False)

    def _add(self, tracks: Iterable[tuple[str, str]], urgent: bool) -> None:
        if not self.available:
            return
        with self._wake:
            (self._urgent if urgent else self._sweep).extend((v, str(p)) for v, p in tracks)
            self._wake.notify_all()
            if not self._started:
                self._started = True
                for name, take in (("waveform-actual", self._urgent.pop),
                                   ("waveform-barrido", self._sweep.popleft)):
                    threading.Thread(target=self._run, args=(name, take), name=name,
                                     daemon=True).start()

    def _run(self, name: str, take: Callable[[], tuple[str, str]]) -> None:
        urgent = name == "waveform-actual"
        pending = self._urgent if urgent else self._sweep
        while True:
            with self._wake:
                while not pending:
                    self._wake.wait()
                video_id, path = take()
            self._process(video_id, path, urgent)

    def _process(self, video_id: str, path: str, urgent: bool) -> None:
        if video_id in self._failed:
            return
        cached = load(video_id)
        if cached is not None:
            if urgent:
                self.on_ready(video_id, cached)
            return
        with self._busy_lock:  # el otro hilo ya la está midiendo: no se decodifica dos veces
            if video_id in self._busy:
                return
            self._busy.add(video_id)
        try:
            levels = analyze(path, low_priority=not urgent)
            save(video_id, levels)
        except Exception as exc:  # noqa: BLE001 - sin onda la canción suena igual; no se reintenta
            self._failed.add(video_id)
            _log.failure(log, f"no se pudo analizar {video_id}", exc)
            return
        finally:
            with self._busy_lock:
                self._busy.discard(video_id)
        self.on_ready(video_id, levels)
        if not urgent:
            time.sleep(SWEEP_PAUSE_S)
