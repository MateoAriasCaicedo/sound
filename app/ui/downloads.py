"""Descargas desde la ventana: el servicio de fondo, lo que va en curso y los avisos de estado.

Es el único sitio de la interfaz que habla con `DownloadService`. La ventana le pasa el control de
la cola (encolar, reintentar, priorizar, quitar) y recibe de vuelta eventos por `app.q`, porque los
avisos del servicio llegan desde hilos de descarga y solo el hilo de la interfaz puede tocar Tk.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..core import library
from ..core import log as _log

if TYPE_CHECKING:
    from .main import App

log = _log.get("ui.downloads")
AHEAD_DOWNLOADS = 5  # las próximas canciones de streaming que se descargan solas


class DownloadsController:
    def __init__(self, app: App) -> None:
        self.app = app
        self.service = None  # DownloadService: se crea en un hilo, importarlo cuesta ~0,5 s
        self.inflight: set[str] = set()  # descargas en curso (manuales o por reproducción)
        self.state: dict[str, str] = {}  # texto de la columna «Estado» por canción
        self.quiet: set[str] = set()  # respaldos automáticos: si fallan, sin ruido
        self.ahead_tried: set[str] = set()  # ya encoladas por la descarga anticipada

    # ---------- servicio ----------
    def start_service(self) -> None:
        """Arranca los trabajadores de descarga en un hilo: importar yt-dlp tarda ~0,5 s."""
        try:
            from ..core.service import DownloadService
            service = DownloadService(self.on_event)
            service.start()
        except Exception as exc:  # noqa: BLE001 - la ventana sigue funcionando sin descargas
            log.exception("no se pudo arrancar el servicio de descargas")
            self.app.q.put(("status", f"Descargas no disponibles: {exc}"))
            return
        self.service = service
        left = self.queue_left()
        if left:  # quedó cola de una sesión anterior: el servicio ya la está retomando
            self.app.q.put(("status", f"Reanudando {left} descargas pendientes…"))

    def on_event(self, kind: str, track: dict | None, **info) -> None:
        """Avisos del servicio. Llegan desde los hilos de descarga: aquí solo se encolan."""
        q = self.app.q
        if kind == "low_disk":
            q.put(("status", "Poco espacio en disco: descargas en pausa. "
                             "Libera espacio y vuelve a pulsar Descargar."))
            return
        if kind == "outdated":
            q.put(("status", "Varias descargas seguidas fallan como si yt-dlp estuviera desactualizado. "
                             "Cierra la ventana y ejecuta: sound actualizar"))
            return
        if kind in ("paused", "resumed"):  # no traen canción
            q.put(("queue_paused", kind == "paused"))
            return
        vid = track["video_id"]
        if kind == "progress":
            q.put(("dl_progress", vid, int(info["fraction"] * 100)))
        elif kind == "retry":
            q.put(("dl_state", vid, f"reintento {info['attempt']}"))
        elif kind == "offline":
            q.put(("dl_state", vid, "sin conexión"))
        elif kind == "done":
            q.put(("downloaded", vid, track["title"]))
        elif kind == "failed":
            q.put(("dl_error", vid, f"{track['title']}: {info['error']}", vid in self.quiet))

    @staticmethod
    def queue_left() -> int:
        counts = library.queue_counts()
        return counts.get("pending", 0) + counts.get("running", 0)

    # ---------- encolar ----------
    def submit(self, tracks: list[dict], genre: str | None, priority: int = 0,
               quiet: bool = False) -> None:
        """Entrega canciones al servicio, que guarda la cola en la base de datos: reintenta,
        espera si no hay internet y continúa donde quedó si se cierra la ventana."""
        if self.service is None:  # el servicio aún está arrancando
            self.app.after(300, lambda: self.submit(tracks, genre, priority, quiet))
            return
        for t in tracks:
            self.inflight.add(t["video_id"])
            if quiet:
                self.quiet.add(t["video_id"])
            self.app._set_state(t["video_id"], "en cola")
        self.service.submit(tracks, genre, priority)

    def start_download(self, t: dict, quiet: bool = False) -> None:
        """Encola una canción (botón Descargar o respaldo automático de lo que suena).

        El respaldo va por delante de las descargas manuales pendientes: se está escuchando ya.
        """
        self.submit([t], t.get("_genre"), priority=10 if quiet else 0, quiet=quiet)

    def download_ahead(self) -> None:
        """Las próximas AHEAD_DOWNLOADS canciones que irían por streaming se descargan solas:
        así quedan guardadas (y suenan sin internet) aunque no llegues a escucharlas.
        Van detrás de las descargas que pides tú y una sola vez por sesión cada una."""
        app = self.app
        if not app.online or self.service is None:
            return
        by_genre: dict[str | None, list[dict]] = {}
        for t in app.pq.next_tracks(AHEAD_DOWNLOADS):
            vid = t["video_id"]
            if app._is_remote(t) and vid not in self.inflight and vid not in self.ahead_tried:
                self.ahead_tried.add(vid)
                by_genre.setdefault(t.get("_genre"), []).append(t)
        for genre, group in by_genre.items():
            self.submit(group, genre, priority=-5, quiet=True)

    # ---------- controles de la cola ----------
    def retry_failed(self) -> int:
        """Devuelve las descargas fallidas a la cola. Devuelve cuántas eran."""
        if self.service is None:
            return 0
        failed = library.failed()
        count = self.service.retry_failed()
        for row in failed:
            self.inflight.add(row["video_id"])
            self.app._set_state(row["video_id"], "en cola")
        self.app.status.configure(text=f"{count} descargas fallidas vuelven a la cola" if count
                                  else "No hay descargas fallidas")
        return count

    def prioritize(self, ids: list[str]) -> int:
        """Pone canciones de la cola al frente; una fallida vuelve a la cola. Devuelve cuántas."""
        if self.service is None:
            return 0
        failed_ids = {row["video_id"] for row in library.failed()} & set(ids)
        count = self.service.prioritize(ids)
        for vid in failed_ids:
            self.inflight.add(vid)
            self.app._set_state(vid, "en cola")
        text = f"{count} al frente de la cola" if count else "Nada que mover: ya se están bajando"
        if failed_ids:
            n = len(failed_ids)
            text += f" ({n} fallida vuelve a intentarse)" if n == 1 else f" ({n} fallidas vuelven a intentarse)"
        self.app.status.configure(text=text)
        return count

    def remove(self, ids: list[str]) -> int:
        """Quita canciones de la cola. Las que ya se están bajando no se pueden quitar."""
        if self.service is None:
            return 0
        removed = self.service.remove(ids)
        busy = 0
        for vid in ids:
            if library.status_of(vid) is None:
                self.forget(vid)
            else:
                busy += 1
        text = f"{removed} quitada de la cola" if removed == 1 else f"{removed} quitadas de la cola"
        if busy:
            text += (f" ({busy} ya se estaba bajando y termina)" if busy == 1
                     else f" ({busy} ya se estaban bajando y terminan)")
        self.app.status.configure(text=text)
        return removed

    def forget(self, vid: str) -> None:
        """La canción ya no está en la cola: su fila vuelve a mostrarse como de YT Music (☁)."""
        app = self.app
        self.inflight.discard(vid)
        self.quiet.discard(vid)
        self.state.pop(vid, None)
        iid = app.vid_iid.get(vid)
        if iid is not None and app.tree.exists(iid):
            app.tree.set(iid, "state", app._row_state(app.rows[int(iid)]))

    def close(self) -> None:
        if self.service:
            self.service.close()  # sus hilos son demonio: lo que quede en curso se retoma al abrir
