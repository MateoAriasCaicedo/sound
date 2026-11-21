"""«Descubre en YouTube» en la ventana: la estantería de Inicio y las portadas de la bienvenida.

Al abrir se enseña lo guardado de la vez anterior y, unos segundos después (con internet y si
está activado en Ajustes), se pide lo nuevo; también al volver la conexión. Lo que ya está en
la biblioteca no sale: al descargar una canción, deja su sitio a la siguiente.
"""
from __future__ import annotations

import datetime
import threading

from ..core import discover, history
from ..core import log as _log

log = _log.get("ui")
DISCOVER_DELAY_MS = 4000  # deja arrancar la ventana (y la búsqueda) antes de pedir nada


class DiscoverMixin:
    def _init_discover(self) -> None:
        self.discover_on = bool(self.prefs.get("discover", True))
        self.discover_tracks: list[dict] = []
        self.discover_busy = False
        self.discover_failed = False
        self._discover_fresh = False

    # ---------- qué se enseña ----------
    def _discover_rows(self) -> list[dict]:
        """Las sugerencias que aún no tienes (vacío si Descubre está desactivado)."""
        if not self.discover_on:
            return []
        return [t for t in self.discover_tracks if t["video_id"] not in self.lib_by_id]

    def _on_home(self) -> bool:
        return (self.view_kind == "home" and self.sub_view is None and self.active_page is None
                and not self.query.get().strip())

    def _discover_changed(self) -> None:
        """Llegaron sugerencias: la bienvenida las pinta ya; Inicio, solo si está arriba del
        todo (repintarlo a mitad movería la lista bajo el ratón)."""
        if self.welcome_on:
            self._sync_welcome()
        elif self._on_home() and self._scroll_fraction() < 0.02:
            self.refresh_view()

    # ---------- pedirlas ----------
    def _discover_seeds(self) -> list[dict]:
        try:
            top, recent = history.most_played(40), history.recent(40)
        except Exception as exc:  # noqa: BLE001 - sin historial, lo popular
            _log.failure(log, "no se pudo leer el historial para Descubre", exc)
            top, recent = [], []
        return discover.pick_seeds(top, recent, set(self.lib_by_id),
                                   day=datetime.date.today().toordinal())

    def _start_discover(self) -> None:
        """Al abrir: lo guardado al momento y, si ya no vale, lo nuevo en unos segundos."""
        if not self.discover_on:
            return
        seeds = self._discover_seeds()
        cached, self._discover_fresh = discover.load_cache(discover.seed_key(seeds))
        if cached:
            self.discover_tracks = cached
            self._discover_changed()
        if not self._discover_fresh:
            self.discover_busy = True  # la bienvenida enseña huecos mientras llegan
            self._sync_welcome()
            self.after(DISCOVER_DELAY_MS, lambda: self._load_discover(seeds))

    def _load_discover(self, seeds: list[dict] | None = None) -> None:
        if not self.discover_on or (self.discover_busy and seeds is None):
            return
        if not self.online:
            self.discover_busy = False
            self.discover_failed = not self.discover_tracks
            self._discover_changed()
            return
        seeds = self._discover_seeds() if seeds is None else seeds
        key = discover.seed_key(seeds)
        day = datetime.date.today().toordinal()
        self.discover_busy = True

        def work() -> None:
            try:
                have_ids, have_songs = discover.known()
                tracks = discover.fetch(seeds, have_ids, have_songs, day)
                if tracks:
                    discover.save_cache(key, tracks)
                self.q.put(("discover", tracks))
            except Exception as exc:  # noqa: BLE001 - sin internet, API cambiada…
                _log.failure(log, "no se pudieron preparar las sugerencias de Descubre", exc)
                self.q.put(("discover", None))

        threading.Thread(target=work, daemon=True, name="descubre").start()

    def _discover_loaded(self, tracks: list[dict] | None) -> None:
        """Desde la cola de eventos: None si falló (se queda lo que hubiera)."""
        self.discover_busy = False
        if tracks:
            self.discover_tracks = tracks
            self._discover_fresh = True
        self.discover_failed = not self.discover_tracks
        self._discover_changed()

    def _discover_online(self) -> None:
        """Volvió la conexión: si lo que hay es viejo (o no hay nada), se renueva."""
        if self.discover_on and not self._discover_fresh and not self.discover_busy:
            self._load_discover()

    # ---------- Ajustes ----------
    def toggle_discover(self) -> None:
        self.discover_on = not self.discover_on
        self.save_prefs()
        if self.discover_on:
            self.status.configure(text="Descubre activado: sugerencias de YouTube en Inicio")
            self._start_discover()
        else:
            self.status.configure(text="Descubre desactivado")
        if self.welcome_on:
            self._sync_welcome()
        elif self._on_home():
            self.refresh_view()
