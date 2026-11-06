"""Bucle de eventos que los hilos de descarga y streaming envían a la ventana.

Vive fuera de main.py para que la ventana no siga creciendo: aquí solo se traduce un evento a
una llamada de la interfaz, y se garantiza que el bucle se reprograme pase lo que pase.
"""
import queue
import time
from tkinter import messagebox

from ..core import config, library, waveform
from ..core import log as _log

log = _log.get("ui.events")
AHEAD_RESULTS = 12  # resultados de YT Music cuya URL se resuelve y se deja abierta en el reproductor
GB = 1024**3


class EventLoopMixin:
    # ---------- cola de eventos de hilos ----------
    def _pump(self) -> None:
        """Reprograma siempre: un evento roto no debe dejar la ventana sin eventos."""
        try:
            self._drain_events()
        except Exception as exc:  # noqa: BLE001 - descargas y streaming llegarían a pararse
            _log.failure(log, "el bucle de eventos de la ventana falló", exc)
        finally:
            self.after(100, self._pump)

    def _drain_events(self) -> None:
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "results":
                    if ev[1] == self.search_id:
                        final = ev[5] if len(ev) > 5 else True
                        self.searching = False
                        self.remote, self.remote_src, self.remote_q = ev[2], ev[3], ev[4]
                        self.refresh_view()
                        if final:
                            self._fade_rows()
                        if not self.yt_genres:  # la conexión volvió: carga los géneros
                            self._load_yt_genres()
                        # resuelve ya las URLs de los primeros resultados: al dar play, sin espera
                        self._resolve_ahead(
                            [t["video_id"] for t in self.rows if self._is_remote(t)][:AHEAD_RESULTS])
                    elif ev[3] == "songs":
                        # Llegó la respuesta de un prefijo de lo que se escribe: úsala como
                        # pre-resultados en vez de tirarla. La definitiva la sustituirá.
                        current = self.query.get().strip()
                        if current and self._related_query(current, ev[4]):
                            self._show_pre_results(current)
                            self.refresh_view()
                elif kind == "remote_error":
                    if ev[1] == self.search_id:
                        self.searching = False
                        self.refresh_view()
                        self.status.configure(
                            text=f"Sin conexión con YouTube: solo biblioteca  ({ev[2][:60]})")
                elif kind == "status":
                    self.status.configure(text=ev[1])
                elif kind == "genres":
                    self.yt_failed = ev[1] is None
                    self.yt_genres = ev[1] or []
                    self._build_sidebar()
                elif kind == "stream":
                    if ev[1] == self.play_token:
                        if len(ev) > 4:  # URL de streaming
                            self._start(ev[2], ev[1], url=ev[3], cover=False)
                        else:  # archivo local (vía de reserva)
                            self._start(ev[2], ev[1], path=ev[3], cover=False)
                            self.status.configure(text="Reproduciendo · guardada en tu biblioteca")
                elif kind == "radio":
                    self._on_radio(ev[1], ev[2])
                elif kind == "radio_err":
                    self._on_radio_error(ev[1], ev[2])
                elif kind == "online":
                    self._on_online(ev[1])
                elif kind == "fallback":
                    if ev[1] == self.play_token:
                        self._download_and_play(ev[2], ev[1])
                elif kind == "resolved":
                    self._prepare(ev[1])
                elif kind == "wave":
                    self._energy_cache[ev[1]] = waveform.summary(ev[2])["energy"]
                    self.scale.set_levels(ev[1], ev[2])
                elif kind == "cover":
                    if ev[1] == self.play_token:
                        self._set_cover(ev[2])
                elif kind == "preview_cover":
                    self._apply_preview_cover(ev[1], ev[2])
                elif kind == "lyrics":
                    self._apply_lyrics(ev[1], ev[3])
                elif kind == "thumb":
                    self._thumbs_pending.discard(ev[1])
                    self.grid.set_cover(ev[1], ev[2])
                elif kind == "queue_paused":
                    self._queue_paused_changed(ev[1])
                elif kind == "disk":
                    self._on_disk_event(ev[1], ev[2])
                elif kind == "needs_cookies":
                    self.status.configure(text=ev[1])
                    if not self._cookies_dialog_shown:
                        self._cookies_dialog_shown = True
                        messagebox.showinfo(
                            "YouTube pide iniciar sesión",
                            "YouTube está pidiendo iniciar sesión o verificar que no eres un "
                            "bot.\n\nAbre «Descargas» y elige, en «Cookies de YouTube», el "
                            "navegador donde tengas la sesión de YouTube iniciada. Después "
                            "pulsa «Reintentar».\n\nEl detalle queda en el registro.",
                            parent=self)
                elif kind == "crash":
                    self._show_crash(ev[1], ev[2])
                elif kind == "health":
                    page = self.pages.get("health")
                    if page is not None:
                        page.set_checks(ev[1])
                elif kind == "update_done":
                    if ev[1] == 0:
                        self._ytdlp_checked_at = time.time()
                        self.save_prefs()
                        self.status.configure(text="Componentes actualizados. Reinicia la ventana "
                                                    "para usar la versión nueva.")
                    else:
                        self.status.configure(
                            text=f"No se pudo actualizar: {ev[2] or 'falló la descarga'}")
                elif kind == "auto_update":
                    self._auto_update_running = False
                    checked, version = ev[1], ev[2]
                    forced = len(ev) > 3 and ev[3]
                    if checked:
                        self._ytdlp_checked_at = time.time()
                        self.save_prefs()
                    if version:
                        self.status.configure(
                            text=f"yt-dlp actualizado a {version}. Reinicia Sound para usarlo.")
                    elif forced:
                        self.status.configure(
                            text="yt-dlp ya está en la última versión. Si las descargas siguen "
                                 "fallando, revisa las cookies o abre Descargas → Diagnóstico."
                            if checked else
                            "No se pudo comprobar si hay una versión nueva de yt-dlp. "
                            "Revisa la conexión o actualízalo desde Descargas.")
                elif kind == "outdated":
                    if self.auto_update:
                        self.status.configure(
                            text="Varias descargas seguidas fallan como si yt-dlp estuviera "
                                 "desactualizado. Buscando una versión nueva…")
                        self._maybe_auto_update(force=True)
                    else:
                        self.status.configure(
                            text="Varias descargas seguidas fallan como si yt-dlp estuviera "
                                 "desactualizado. Pulsa «Actualizar yt-dlp» en Descargas.")
                elif kind == "gain":
                    self._gain_pending.discard(ev[1])
                    if self.normalize and self.playing_vid == ev[1] and ev[2] is not None:
                        self.player.set_gain_db(ev[2])
                elif kind == "dl_state":
                    self._set_state(ev[1], ev[2])
                elif kind == "dl_progress":
                    self._set_state(ev[1], f"{ev[2]}%")
                elif kind in ("downloaded", "dl_error"):
                    self.dl.inflight.discard(ev[1])
                    self.dl.quiet.discard(ev[1])
                    if kind == "downloaded":
                        self.damaged.discard(ev[1])
                        row = library.get_track(ev[1])
                        if row:
                            self._upsert_track(row)  # una fila, no toda la biblioteca
                        iid = self.vid_iid.get(ev[1])
                        track = self.lib_by_id.get(ev[1])
                        if iid is not None and track:  # la fila pasa a ser local
                            self.rows[int(iid)].update(track)
                        if track:  # ya hay audio que medir: su onda crece aquí
                            self.waves.want(ev[1], track["path"],
                                            urgent=ev[1] == self.playing_vid)
                        self.grid.forget_cover(ev[1])
                        self._set_state(ev[1], "✓")
                        self._retag(ev[1])
                        msg = f"Descargada: {ev[2]}"
                    elif len(ev) > 3 and ev[3]:  # precarga fallida: sin ruido, se reintenta al reproducir
                        self.dl.state.pop(ev[1], None)
                        iid = self.vid_iid.get(ev[1])
                        if iid is not None and self.tree.exists(iid):
                            self.tree.set(iid, "state", "☁")
                        self.grid.set_state(ev[1], "☁")
                        continue
                    else:
                        self._set_state(ev[1], "✗")
                        msg = f"Error — {ev[2]}"
                    if self.dl.queue_left() == 0:
                        msg += "  ·  listo"
                        self._build_sidebar()  # actualiza los contadores por género
                    self.status.configure(text=msg)
        except queue.Empty:
            pass

    def _on_disk_event(self, event: str, free: int | None) -> None:
        """Avisa del poco espacio y mantiene el estado visible hasta que se recupere."""
        self.disk_free = free
        available = "espacio desconocido" if free is None else f"{free / GB:.1f} GB"
        if event == "disk_ok":
            self.disk_level = "ok"
            self._disk_dialog_shown = False
            message = ("Espacio suficiente: descargas reanudadas" if not self.dl_paused
                       else "Espacio suficiente; las descargas siguen en pausa")
        elif event == "disk_low":
            self.disk_level = "low"
            message = f"Poco espacio en disco ({available}): descargas de una en una"
        else:
            self.disk_level = "critical"
            message = (f"Descargas en pausa por poco espacio ({available}). "
                       "Libera espacio y se reanudarán solas.")
            if not self._disk_dialog_shown:
                self._disk_dialog_shown = True
                detail = (f"Quedan {available} libres en el disco."
                          if free is not None else "El disco está casi lleno.")
                messagebox.showwarning(
                    "Poco espacio en disco",
                    f"{detail}\n\nLas descargas se pausaron para no llenarlo. "
                    f"Libera espacio en:\n{config.MUSIC_DIR}\n\n"
                    "Se reanudarán automáticamente cuando haya espacio suficiente.",
                    parent=self)
        self._refresh_downloads_icon()
        page = self.pages.get("downloads")
        if page is not None and self.active_page is page:
            page.refresh()
        self.status.configure(text=message)
