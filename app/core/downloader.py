import os
import random
import re
import shutil
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import requests
import yt_dlp
from mutagen import MutagenError
from mutagen.mp4 import MP4, MP4Cover
from ytmusicapi.exceptions import YTMusicError

from . import library, ytsearch
from .config import MAX_PARALLEL, MUSIC_DIR

# m4a (AAC) nativo de YouTube: no requiere ffmpeg para convertir.
FORMAT = "140/bestaudio[ext=m4a]"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_BASE_OPTS = {
    "format": FORMAT,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,  # el avance se reporta por on_progress, no por consola
    "noplaylist": True,
    "retries": 5,
    "js_runtimes": {"node": {}},  # YouTube exige un runtime JS para resolver los enlaces
}
_COOKIES_BROWSER = os.environ.get("SOUND_COOKIES_FROM_BROWSER", "").strip()
if _COOKIES_BROWSER:  # p. ej. "firefox" para vídeos con restricción de edad
    _BASE_OPTS["cookiesfrombrowser"] = (_COOKIES_BROWSER,)
DURATION_SLACK_S = 10  # tolerancia entre la duración anunciada y la del archivo
DURATION_SLACK_PCT = 0.10


class IntegrityError(Exception):
    """El archivo descargado no se puede leer; se borra y se reintenta."""


class WrongDuration(Exception):
    """El archivo dura distinto de lo anunciado (video equivocado o truncado): reintentar no ayuda."""


def safe_name(text: str) -> str:
    return _BAD_CHARS.sub("_", text).strip(" .")[:120] or "sin_nombre"


MAX_PATH = 240  # margen bajo los 260 caracteres de Windows (yt-dlp añade '.part' al descargar)


def _target_path(dest: Path, track: dict) -> Path:
    """Ruta final de la canción: recortada a MAX_PATH y sin pisar el archivo de otra canción."""
    suffix = ".m4a"
    id_tag = f" [{track['video_id']}]"
    name = f"{safe_name(track['artist'])} - {safe_name(track['title'])}"
    room = MAX_PATH - len(str(dest)) - 1 - len(suffix) - len(id_tag)  # se reserva sitio para id_tag
    name = name[: max(room, 20)].rstrip(" .")
    path = dest / f"{name}{suffix}"
    if library.owner(path) not in (None, track["video_id"]) or not library.reserve(path, track["video_id"]):
        # Otro video ya usa ese nombre, o lo está bajando ahora mismo (p. ej. la misma canción en
        # dos álbumes): se distingue por id. El id es único, así que esa reserva no puede chocar.
        path = dest / f"{name}{id_tag}{suffix}"
        library.reserve(path, track["video_id"])
    return path


_JPEG, _PNG = b"\xff\xd8", b"\x89PNG\r\n\x1a\n"


def _fetch_cover(url: str | None, sleep: Callable[[float], None] | None = None) -> MP4Cover | None:
    """Descarga la carátula con 3 intentos. None si no hay URL o no se pudo (no es motivo de fallo)."""
    if not url:
        return None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=15) as resp:
                data = resp.read()
        except OSError:
            (sleep or time.sleep)(1 + attempt)  # se resuelve al llamar: así los tests pueden sustituirlo
            continue
        if data.startswith(_JPEG):
            return MP4Cover(data, MP4Cover.FORMAT_JPEG)
        if data.startswith(_PNG):
            return MP4Cover(data, MP4Cover.FORMAT_PNG)
        return None  # otro formato (p. ej. webp): MP4 no lo admite, reintentar no cambia nada
    return None


def _enrich(track: dict) -> dict:
    """Completa álbum y año que los videoclips no traen. Una consulta extra y no crítica."""
    if track.get("album") and track.get("year"):
        return track
    try:
        extra = ytsearch.song_details(track["video_id"])
    except (requests.RequestException, YTMusicError, KeyError, IndexError):
        return track  # se pierde un dato de etiqueta, no la canción
    return {**track, **{key: value for key, value in extra.items() if value and not track.get(key)}}


def _finish(path: Path, track: dict, genre: str | None) -> bool:
    """Valida el archivo descargado y escribe las etiquetas. Devuelve si quedó con carátula."""
    try:
        audio = MP4(path)
    except MutagenError as exc:
        path.unlink(missing_ok=True)
        raise IntegrityError(f"archivo descargado ilegible: {exc}") from exc

    expected, length = track.get("duration"), audio.info.length
    if expected and abs(length - expected) > max(DURATION_SLACK_S, expected * DURATION_SLACK_PCT):
        path.unlink(missing_ok=True)
        raise WrongDuration(f"duración {length:.0f}s, se esperaban {expected}s")

    audio["\xa9nam"] = track["title"]
    audio["\xa9ART"] = track["artist"]
    audio["aART"] = track["artist"].split(",")[0]  # artista del álbum: agrupa bien en los reproductores
    if track.get("album"):
        audio["\xa9alb"] = track["album"]
    if track.get("year"):
        audio["\xa9day"] = str(track["year"])
    if genre:
        audio["\xa9gen"] = genre
    cover = _fetch_cover(track.get("cover"))
    if cover:
        audio["covr"] = [cover]
    audio.save()
    return cover is not None


_local = threading.local()


def _get_ydl() -> yt_dlp.YoutubeDL:
    ydl = getattr(_local, "ydl", None)
    if ydl is None:
        def hook(d: dict) -> None:
            callback = getattr(_local, "callback", None)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if callback and d["status"] == "downloading" and total:
                callback(d["downloaded_bytes"] / total)

        ydl = _local.ydl = yt_dlp.YoutubeDL({**_BASE_OPTS, "progress_hooks": [hook]})
    return ydl


def _download_audio(video_id: str, outtmpl: str, on_progress: Callable[[float], None] | None) -> None:
    """Descarga el audio con una instancia de yt-dlp por hilo.

    Reutilizarla ahorra ~1 s por canción (medido: 3.9 s -> 2.8 s). yt-dlp no está documentado
    como seguro entre hilos, así que cada hilo tiene la suya en vez de compartir una sola.
    """
    ydl = _get_ydl()
    ydl.params["outtmpl"] = {"default": outtmpl}
    _local.callback = on_progress
    try:
        ydl.download([f"https://music.youtube.com/watch?v={video_id}"])
    except Exception:  # noqa: BLE001 - se vuelve a lanzar tal cual
        _local.ydl = None  # tras un error su estado es dudoso: la próxima vez se crea una limpia
        raise
    finally:
        _local.callback = None


def download_track(
    track: dict,
    genre: str | None = None,
    music_dir: Path = MUSIC_DIR,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Descarga una canción a Music/por-genero/<genre>/ y la registra. Devuelve la ruta."""
    existing = library.get_path(track["video_id"])
    if existing:
        return existing

    dest = music_dir / "por-genero" / safe_name(genre) if genre else music_dir / "sueltas"
    dest.mkdir(parents=True, exist_ok=True)
    final = _target_path(dest, track)  # reserva el nombre: otro trabajador no puede elegir el mismo
    try:
        with ThreadPoolExecutor(1) as pool:
            enriching = pool.submit(_enrich, track)  # la consulta de álbum/año corre mientras baja el audio
            _download_audio(track["video_id"], str(final.with_suffix(".%(ext)s")), on_progress)
        track = enriching.result()
        has_cover = _finish(final, track, genre)
        library.add_track({**track, "genre": genre, "has_cover": has_cover}, final)
    finally:
        library.release(track["video_id"])  # ya figura en tracks (o falló): la reserva sobra
    return final


MIN_FREE_BYTES = 500 * 1024**2  # por debajo de esto la cola se detiene sin perder nada
EST_TRACK_BYTES = 5 * 1024**2  # tamaño medio de una canción, para avisar antes de empezar


def free_bytes(path: Path = MUSIC_DIR) -> int:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


MAX_ATTEMPTS = 4
MAX_BACKOFF = 60
THROTTLE_COOLDOWN = 30
OFFLINE_POLL = 5
OFFLINE_MAX_WAIT = 30 * 60

# Reintentar esto es inútil: el video no va a aparecer. Ojo: "unavailable" a secas no sirve,
# porque "503 Service Unavailable" sí es transitorio.
PERMANENT_MARKERS = (
    "video unavailable", "video is unavailable", "video is not available", "video is private",
    "private video", "has been removed", "no longer available", "not available in your country",
    "sign in to confirm your age", "copyright", "members-only", "has been terminated",
)
# YouTube está limitando el ritmo: conviene que todos los trabajadores frenen.
THROTTLE_MARKERS = ("http error 403", "http error 429", "too many requests")


def is_permanent(exc: Exception) -> bool:
    return isinstance(exc, WrongDuration) or any(m in str(exc).lower() for m in PERMANENT_MARKERS)


def is_throttle(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in THROTTLE_MARKERS)


def is_online(host: str = "music.youtube.com", port: int = 443, timeout: float = 3.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_for_internet(sleep: Callable[[float], None] = time.sleep, max_wait: float = OFFLINE_MAX_WAIT) -> None:
    waited = 0.0
    while not is_online():
        if waited >= max_wait:
            raise ConnectionError(f"Sin internet durante {int(max_wait // 60)} minutos")
        sleep(OFFLINE_POLL)
        waited += OFFLINE_POLL


class Pacer:
    """Pausa compartida: si un trabajador choca con el límite de YouTube, frenan todos."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._until = 0.0
        self._lock = threading.Lock()

    def cooldown(self, seconds: float) -> None:
        with self._lock:
            self._until = max(self._until, self._clock() + seconds)

    def wait(self) -> None:
        while (left := self._until - self._clock()) > 0:
            self._sleep(left)


def download_with_retry(
    track: dict,
    genre: str | None = None,
    pacer: Pacer | None = None,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[dict, int, float, Exception], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
    offline_wait: float = OFFLINE_MAX_WAIT,
    on_offline: Callable[[dict], None] | None = None,
) -> tuple[Path, int]:
    """Descarga con espera progresiva. Devuelve (ruta, intentos usados).

    Los cortes de internet no gastan intentos: se espera a que vuelva la conexión hasta
    `offline_wait` segundos (0 = fallar enseguida, p. ej. al reproducir una canción que el
    usuario espera oír ya). `on_offline(canción)` avisa de que empieza esa espera.
    """
    pacer = pacer or Pacer()
    extra = {"on_progress": on_progress} if on_progress else {}
    attempt = 0
    while True:
        pacer.wait()
        attempt += 1
        try:
            return download_track(track, genre, **extra), attempt
        except Exception as exc:  # noqa: BLE001 - se clasifica abajo
            exc.attempts = attempt  # para que quien lo registre sepa cuántos se usaron
            if is_permanent(exc):
                raise
            if not is_online():
                attempt -= 1  # sin conexión no cuenta como intento
                if on_offline:
                    on_offline(track)
                wait_for_internet(sleep, offline_wait)
                continue
            if attempt >= attempts:
                raise
            delay = min(MAX_BACKOFF, 2**attempt) + random.uniform(0, 1)
            if is_throttle(exc):
                pacer.cooldown(THROTTLE_COOLDOWN)
            if on_retry:
                on_retry(track, attempt, delay, exc)
            sleep(delay)


def run_queue(
    on_done: Callable[[dict, Path | None, Exception | None], None] | None = None,
    on_retry: Callable[[dict, int, float, Exception], None] | None = None,
    on_progress: Callable[[dict, float], None] | None = None,
) -> dict[str, int]:
    """Procesa la cola pendiente en paralelo. Un fallo no detiene el resto.

    El estado vive en SQLite: si se interrumpe (Ctrl+C, corte de luz), lo que no terminó
    sigue 'pending' y se retoma al volver a llamar. `on_progress(canción, 0..1)` se llama
    desde los hilos de descarga, así que quien lo use (p. ej. una ventana) debe ser seguro entre hilos.
    """
    from .service import DownloadService  # import tardío: el servicio usa este módulo

    def on_event(kind: str, track: dict | None, **info) -> None:
        if kind == "done" and on_done:
            on_done(track, info["path"], None)
        elif kind == "failed" and on_done:
            on_done(track, None, info["error"])
        elif kind == "retry" and on_retry:
            on_retry(track, info["attempt"], info["delay"], info["error"])
        elif kind == "progress" and on_progress:
            on_progress(track, info["fraction"])

    service = DownloadService(on_event, persistent=False)
    service.start()
    try:
        service.wait_idle()
    except KeyboardInterrupt:
        service.stop()
        raise
    finally:
        service.close()
    return library.queue_counts()
