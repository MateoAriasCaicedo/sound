"""Aviso de versiones nuevas de Sound (la parte sin ventana está en core/app_update.py).

Al arrancar (como mucho una vez al día, y si está activado en Ajustes) se mira en GitHub si hay
una versión más nueva. Si la hay, un aviso lo dice y el engranaje de Ajustes se ilumina. En
Ajustes → Actualizaciones se leen las novedades y «Actualizar ahora» descarga el instalador,
comprueba su suma y, tras confirmarlo, cierra Sound y lo instala: vuelve a abrirse solo, con la
cola donde estaba (la sesión se guarda al cerrar, como siempre).

En desarrollo (desde el código) no se busca sola: «Buscar ahora» sí funciona, y «Actualizar»
abre la página de la versión en el navegador en vez de instalar nada.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from tkinter import messagebox

from .. import __version__
from ..core import app_update, config, startup
from ..core import log as _log

log = _log.get("app-update")

CHECK_DELAY_MS = 45_000  # que la ventana arranque (y yt-dlp mire lo suyo) antes de preguntar


class AppUpdateMixin:
    def _init_app_updates(self) -> None:
        self.app_updates = bool(self.prefs.get("app_updates", True))
        try:
            self._app_update_at = float(self.prefs.get("app_update_checked_at", 0) or 0)
        except (TypeError, ValueError):
            self._app_update_at = 0.0
        self.app_update: app_update.Release | None = None  # la versión nueva encontrada
        self.app_update_state = "idle"  # idle | checking | downloading | installing | error
        self.app_update_note = ""       # lo último que pasó, para enseñarlo en Ajustes
        self.app_update_progress = 0.0
        self._app_update_announced: str | None = None
        self._app_update_cancel = False

    def _app_update_prefs(self) -> dict:
        return {"app_updates": self.app_updates, "app_update_checked_at": self._app_update_at}

    @staticmethod
    def _updates_dir():
        return config.DATA_DIR / "updates"

    # ---------- buscar ----------
    def toggle_app_updates(self) -> None:
        self.app_updates = not self.app_updates
        self.save_prefs()
        self.status.configure(text="Aviso de versiones nuevas " + (
            "activado" if self.app_updates else "desactivado"))
        if self.app_updates:
            self._maybe_check_app_update()
        self._app_update_changed()

    def _maybe_check_app_update(self, manual: bool = False) -> None:
        """Pregunta a GitHub en segundo plano. Sin `manual`: solo en la app instalada, si está
        activado y si hace más de un día de la última vez."""
        if self.app_update_state in ("checking", "downloading", "installing"):
            return
        if not manual:
            if not self.app_updates or not config.is_frozen():
                return
            if time.time() - self._app_update_at < app_update.CHECK_INTERVAL_S:
                return
        self.app_update_state = "checking"
        self.app_update_note = "Buscando…"
        self._app_update_changed()

        def work() -> None:
            app_update.clean(self._updates_dir(), older_than_s=3600)  # restos de la anterior
            try:
                release = app_update.fetch_latest(__version__)
                self.q.put(("app_update", release, None, manual))
            except app_update.UpdateError as exc:
                self.q.put(("app_update", None, str(exc), manual))
            except Exception as exc:  # noqa: BLE001 - buscar nunca debe tumbar la app
                _log.failure(log, "no se pudo buscar una versión nueva", exc)
                self.q.put(("app_update", None, "No se pudo buscar una versión nueva", manual))

        threading.Thread(target=work, daemon=True, name="buscar-version").start()

    def _app_update_checked(self, release, error: str | None, manual: bool) -> None:
        self.app_update_state = "error" if error else "idle"
        if error:
            self.app_update_note = error
            if manual:
                self.status.configure(text=error)
            self._app_update_changed()
            return
        self._app_update_at = time.time()
        self.save_prefs()
        self.app_update = release
        if release is None:
            self.app_update_note = f"Tienes la última versión ({__version__})."
            if manual:
                self.status.configure(text=f"Sound está al día ({__version__})")
        else:
            self.app_update_note = ""
            if release.version != self._app_update_announced:
                self._app_update_announced = release.version
                self.toast.show(f"Hay una versión nueva de Sound: {release.version}", "Ver",
                                on_action=self.open_app_update)
        self._app_update_changed()

    def open_app_update(self) -> None:
        """Ajustes, en el apartado de actualizaciones."""
        self.show_page("settings")
        page = self.pages.get("settings")
        if page is not None:
            self.after_idle(page.show_updates)

    # ---------- descargar e instalar ----------
    def install_app_update(self) -> None:
        release = self.app_update
        if release is None or self.app_update_state in ("downloading", "installing"):
            return
        if not config.is_frozen() or not release.installable:
            # desde el código no se instala nada; sin suma publicada, se descarga a mano
            webbrowser.open(release.page_url)
            return
        size = f" ({release.installer_size / 1_048_576:.0f} MB)" if release.installer_size else ""
        if not messagebox.askyesno(
                "Actualizar Sound",
                f"Se descargará Sound {release.version}{size}. Al terminar, Sound se cerrará unos "
                "segundos para instalarla y volverá a abrirse solo, con tu cola donde estaba.\n\n"
                "¿Actualizar ahora?", parent=self):
            return
        self.app_update_state = "downloading"
        self.app_update_progress = 0.0
        self.app_update_note = "Descargando…"
        self._app_update_cancel = False
        self._app_update_changed()
        last = [-1]

        def progress(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else -1
            if percent != last[0]:  # un aviso por cada 1 %: no se inunda la cola de eventos
                last[0] = percent
                self.q.put(("app_update_progress", done, total))

        def work() -> None:
            try:
                path = app_update.download(release, self._updates_dir(), __version__, progress,
                                           cancelled=lambda: self._app_update_cancel)
                self.q.put(("app_update_ready", path))
            except app_update.UpdateError as exc:
                self.q.put(("app_update_failed", str(exc)))
            except Exception as exc:  # noqa: BLE001
                _log.failure(log, "no se pudo descargar la versión nueva", exc)
                self.q.put(("app_update_failed", "No se pudo descargar la versión nueva"))

        threading.Thread(target=work, daemon=True, name="descargar-version").start()

    def _app_update_progressed(self, done: int, total: int) -> None:
        if self.app_update_state != "downloading":
            return
        self.app_update_progress = done / total if total else 0.0
        mb = done / 1_048_576
        self.app_update_note = (f"Descargando… {self.app_update_progress:.0%}" if total
                                else f"Descargando… {mb:.0f} MB")
        self._app_update_changed()

    def _app_update_failed(self, message: str) -> None:
        self.app_update_state = "error"
        self.app_update_note = message
        self.status.configure(text=message)
        self._app_update_changed()

    def _app_update_ready(self, path) -> None:
        """Descargado y comprobado: se abre el instalador y Sound se cierra para dejarle sitio."""
        self.app_update_state = "installing"
        self.app_update_note = "Instalando: Sound se cerrará y volverá a abrirse solo…"
        self.status.configure(text=self.app_update_note)
        self._app_update_changed()

        def go() -> None:
            try:
                autostart = self.start_with_windows if startup.available() else None
                app_update.launch_installer(path, autostart)
            except OSError as exc:
                _log.failure(log, "no se pudo abrir el instalador", exc)
                self._app_update_failed("No se pudo abrir el instalador descargado")
                return
            self._close()

        self.after(900, go)  # que se lea el aviso antes de cerrar

    # ---------- en pantalla ----------
    def _app_update_changed(self) -> None:
        """El engranaje de Ajustes se ilumina con una versión nueva; Ajustes se pone al día."""
        self.btn_settings.set_on(self.app_update is not None)
        page = self.pages.get("settings")
        if page is not None:
            page.refresh_updates()

    def _settings_tip(self) -> str:
        if self.app_update is not None:
            return f"Ajustes · hay una versión nueva de Sound: {self.app_update.version}"
        return "Ajustes y atajos (Ctrl+,)"
