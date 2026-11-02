"""Servicio de descargas: trabajadores de fondo sobre la cola persistente.

Lo usan la ventana (persistente: espera trabajo nuevo mientras esté abierta) y la CLI (no
persistente: termina al vaciar la cola). Ambas pueden trabajar sobre la misma cola a la vez:
cada canción la reclama un solo trabajador (library.claim_next).

Los avisos salen por `on_event(tipo, canción, **datos)` desde los hilos de descarga:
    progress  fraction            avance de la canción (0..1)
    retry     attempt, delay, error
    offline                       sin internet: espera a que vuelva
    done      path
    failed    error
    low_disk  free                poco espacio: la cola se pausa (canción = None)
    paused / resumed              el usuario pausó o reanudó las descargas (canción = None)
    outdated  count               varias descargas seguidas fallan como si yt-dlp estuviera
                                  desactualizado: conviene actualizar (canción = None)
"""
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import downloader, errors, health, library
from . import log as _log
from .config import MAX_PARALLEL

log = _log.get("service")

IDLE_POLL = 3  # segundos entre consultas a la cola cuando no hay nada que hacer
LOW_DISK_POLL = 30
FETCH_WAIT = 120  # cuánto espera fetch_now a que otro trabajador termine la misma canción
PROGRESS_STEP = 2  # puntos porcentuales mínimos entre avisos de progreso
OUTDATED_STREAK = 3  # fallos seguidos "de yt-dlp viejo" antes de avisar de que hay que actualizar


def _lock_path() -> Path:
    return Path(library.DB_PATH).with_suffix(".lock")  # se lee al llamar, como DB_PATH


def _try_lock(path: Path):
    """Marca 'hay un proceso descargando'. Devuelve el manejador, o None si ya lo tiene otro.

    El sistema operativo suelta el bloqueo solo si el proceso muere, así que no queda atascado.
    """
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # ya soltado
    handle.close()


class DownloadService:
    def __init__(
        self,
        on_event: Callable[..., None] | None = None,
        persistent: bool = True,
        workers: int | None = None,
    ) -> None:
        self._on_event = on_event
        self.persistent = persistent
        self.workers = workers or MAX_PARALLEL
        self.pacer = downloader.Pacer()  # compartido: si YouTube limita, frenan todos
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = None
        self._low_disk_reported = False
        self._outdated_streak = 0
        self._cookies_reported = False
        self._streak_lock = threading.Lock()

    # ---------- ciclo de vida ----------
    def start(self) -> None:
        if self._threads:
            return
        self._lock = _try_lock(_lock_path())
        if self._lock is not None:
            # Nadie más está descargando, así que lo que quedó 'running' es de una sesión caída.
            library.recover_running()
        for i in range(self.workers):
            thread = threading.Thread(target=self._worker, name=f"descarga-{i}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        """Pide parar: cada trabajador termina su canción actual y sale."""
        self._stop.set()
        self._wake.set()

    def close(self) -> None:
        self.stop()
        if self._lock is not None:
            _unlock(self._lock)
            self._lock = None

    def wait_idle(self) -> None:
        """Bloquea hasta que los trabajadores terminan (solo tiene sentido si no es persistente)."""
        for thread in self._threads:
            while thread.is_alive():
                thread.join(0.5)  # con timeout, para que Ctrl+C pueda interrumpir en Windows

    # ---------- trabajo ----------
    def submit(self, tracks: list[dict], genre: str | None, priority: int = 0) -> int:
        """Encola canciones y despierta a los trabajadores. Devuelve cuántas eran nuevas."""
        added = library.enqueue(tracks, genre, priority)
        self._low_disk_reported = False
        self._wake.set()
        return added

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        """Deja de empezar canciones nuevas. Las que ya se están bajando terminan: yt-dlp no se
        puede cortar a mitad sin dejar el archivo a medias. fetch_now sigue funcionando: es lo que
        el usuario pidió oír ahora."""
        if not self._paused.is_set():
            self._paused.set()
            self._emit("paused")

    def resume(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            self._wake.set()
            self._emit("resumed")

    def wake(self) -> None:
        """Avisa a los trabajadores de que la cola cambió (y vuelve a avisar si falta espacio)."""
        self._low_disk_reported = False
        self._wake.set()

    def retry_failed(self) -> int:
        """Devuelve las fallidas a la cola y despierta a los trabajadores. Devuelve cuántas eran."""
        with self._streak_lock:
            self._cookies_reported = False
        count = library.retry_failed()
        self.wake()
        return count

    def prioritize(self, video_ids: list[str]) -> int:
        """Pone canciones al frente de la cola (las fallidas vuelven a pendiente). Devuelve cuántas."""
        count = library.prioritize(video_ids)
        self.wake()
        return count

    def remove(self, video_ids: list[str]) -> int:
        """Quita pendientes y fallidas de la cola. Las que ya se están bajando terminan."""
        return library.remove_from_queue(video_ids)

    def fetch_now(self, track: dict, genre: str | None) -> Path:
        """Descarga una canción ya, sin esperar turno (p. ej. el usuario la quiere oír ahora).

        Si no hay internet falla enseguida en vez de esperar. Si otro trabajador (o proceso) ya
        la está bajando, espera a que termine.
        """
        video_id = track["video_id"]
        path = library.get_path(video_id)
        if path:
            return path
        if library.claim(track, genre):
            return self._run(track, genre, offline_wait=0)

        deadline = time.monotonic() + FETCH_WAIT
        while time.monotonic() < deadline:
            path = library.get_path(video_id)
            if path:
                return path
            state = library.status_of(video_id)
            if state and state[0] == "failed":
                raise RuntimeError(state[1] or "la descarga falló")
            time.sleep(0.5)
        raise TimeoutError("la descarga no terminó a tiempo")

    # ---------- internos ----------
    def _emit(self, kind: str, track: dict | None = None, **info) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(kind, track, **info)
        except Exception:  # noqa: BLE001 - un manejador roto no debe matar al trabajador
            log.exception("el manejador de eventos falló con '%s'", kind)

    def _track_outdated(self, error: Exception | None) -> None:
        """Cuenta fallos seguidos de aspecto «yt-dlp desactualizado» y avisa al llegar al umbral.

        Una descarga correcta, o un fallo de otro tipo, reinicia la cuenta. Avisa una sola vez por
        racha: si el usuario no actualiza, no se repite en cada canción.
        """
        with self._streak_lock:
            if error is not None and health.looks_outdated([str(error)]):
                self._outdated_streak += 1
                notify = self._outdated_streak == OUTDATED_STREAK
            else:
                self._outdated_streak = 0
                notify = False
        if notify:
            log.warning("%d fallos seguidos parecen de un yt-dlp desactualizado", OUTDATED_STREAK)
            self._emit("outdated", None, count=OUTDATED_STREAK)

    def _sleep_or_wake(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _worker(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._sleep_or_wake(IDLE_POLL)
                continue
            if downloader.free_bytes() < downloader.MIN_FREE_BYTES:
                if not self.persistent:
                    return  # lo pendiente queda para reanudar cuando haya espacio
                if not self._low_disk_reported:
                    self._low_disk_reported = True
                    self._emit("low_disk", None, free=downloader.free_bytes())
                self._sleep_or_wake(LOW_DISK_POLL)
                continue
            row = library.claim_next()
            if row is None:
                if not self.persistent:
                    return
                self._sleep_or_wake(IDLE_POLL)
                continue
            if self._paused.is_set():  # se pausó justo mientras la reclamaba: no llegó a empezar
                library.unclaim(row["video_id"])
                continue
            track = {key: row[key] for key in library.QUEUE_FIELDS}
            try:
                self._run(track, row["genre"])
            except Exception:  # noqa: BLE001, S110 - _run ya lo anotó en la cola y avisó con 'failed'
                pass

    def _run(self, track: dict, genre: str | None, offline_wait: float | None = None) -> Path:
        """Descarga con reintentos, avisa por eventos y deja el resultado anotado en la cola."""
        video_id = track["video_id"]
        last = [-PROGRESS_STEP]

        def progress(fraction: float) -> None:
            pct = int(fraction * 100)
            if pct >= last[0] + PROGRESS_STEP or pct >= 100:
                last[0] = pct
                self._emit("progress", track, fraction=fraction)

        extra = {} if offline_wait is None else {"offline_wait": offline_wait}
        try:
            path, used = downloader.download_with_retry(
                track, genre, self.pacer,
                on_retry=lambda t, attempt, delay, error: self._emit(
                    "retry", t, attempt=attempt, delay=delay, error=error),
                on_progress=progress,
                on_offline=lambda t: self._emit("offline", t),
                **extra,
            )
        except Exception as exc:
            library.mark(video_id, "failed", getattr(exc, "attempts", 0), str(exc)[:300])
            log.warning("falló %s - %s (%s) tras %s intentos: %s", track.get("artist"),
                        track.get("title"), video_id, getattr(exc, "attempts", "?"), exc)
            self._track_outdated(exc)
            self._warn_cookies(exc)
            self._emit("failed", track, error=exc)
            raise
        library.mark(video_id, "done", used)
        self._track_outdated(None)
        with self._streak_lock:
            self._cookies_reported = False
        self._emit("done", track, path=path)
        return path

    def _warn_cookies(self, exc: Exception) -> None:
        """Avisa una sola vez por racha de que YouTube pide una sesión (cookies)."""
        if not errors.needs_cookies(exc):
            return
        with self._streak_lock:
            first = not self._cookies_reported
            self._cookies_reported = True
        if first:
            log.info("YouTube pidió una sesión: se sugiere configurar cookies")
            self._emit("needs_cookies", None, error=errors.friendly(exc))
