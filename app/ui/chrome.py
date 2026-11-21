"""Barra de arriba y tema: «＋ Añadir música», el contador de Descargas y el tema.

- «＋ Añadir música» reúne lo que antes eran iconos sueltos sin texto: buscar en YouTube,
  importar un enlace y música por frase.
- Descargas lleva un contador con lo que queda por bajar (se mira cada dos segundos).
- El tema sigue al de Windows («Como Windows», lo predeterminado) o es claro u oscuro. Siguiendo
  a Windows, si el sistema cambia de tema la ventana cambia sola, sin reiniciar.
"""
from __future__ import annotations

from .theme import system_is_dark
from .widgets import PillButton

THEMES = ("system", "light", "dark")
THEME_LABELS = {"system": "Como Windows", "light": "Claro", "dark": "Oscuro"}
SYSTEM_POLL_MS = 4000  # cada cuánto se mira el tema de Windows (si se le sigue)
BADGE_EVERY = 5        # ticks de reproducción (~2 s) entre cada recuento de descargas


def initial_theme(prefs: dict, system_dark: bool) -> str:
    """El tema guardado. Con ajustes de antes (solo «dark»): si coincidía con Windows, se sigue a
    Windows; si alguien lo cambió a propósito, se respeta su elección."""
    mode = prefs.get("theme")
    if mode in THEMES:
        return mode
    if "dark" not in prefs:
        return "system"
    dark = bool(prefs["dark"])
    return "system" if dark == system_dark else ("dark" if dark else "light")


def badge_text(left: int) -> str | None:
    return None if left <= 0 else ("99+" if left > 99 else str(left))


class ChromeMixin:
    def _init_chrome(self) -> None:
        self.theme_mode = initial_theme(self.prefs, system_is_dark())
        self.dark = system_is_dark() if self.theme_mode == "system" else self.theme_mode == "dark"
        self._badge_tick = 0
        self.downloads_left = 0

    # ---------- «＋ Añadir música» ----------
    def _build_add_button(self, top, column: int) -> None:
        k = self.k
        self.btn_add = PillButton(top, self.anim, k, "＋  Añadir música", self.open_add_menu,
                                  self.fonts["body"], variant="secondary", min_width=0,
                                  height=34)
        self.btn_add.grid(row=0, column=column, padx=(int(6 * k), int(8 * k)),
                          pady=(int(6 * k), 0))

    def open_add_menu(self) -> None:
        menu = self._menu()
        menu.add_command(label="Buscar en YouTube…", accelerator="Ctrl+F",
                         command=self._add_by_search)
        menu.add_command(label="Importar desde un enlace…", command=self.open_import)
        menu.add_command(label="Música por frase…", accelerator="Ctrl+G",
                         command=self.open_prompt)
        button = self.btn_add
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def _add_by_search(self) -> None:
        self._focus_search()
        self.status.configure(text="Escribe una canción, un artista o un álbum: lo de YouTube "
                                   "sale debajo de lo tuyo, con su botón para descargar")

    # ---------- contador de Descargas ----------
    def _count_downloads(self) -> None:
        self._badge_tick = (self._badge_tick + 1) % BADGE_EVERY
        if self._badge_tick == 0:
            self._refresh_downloads_badge()

    def _refresh_downloads_badge(self) -> None:
        try:
            left = self.dl.queue_left()
        except Exception:  # noqa: BLE001 - sin base de datos, sin contador
            left = 0
        self.downloads_left = left
        self.btn_downloads.set_badge(badge_text(left), self.fonts["badge"],
                                     muted=self.dl_paused)
        self._welcome_downloads(left)  # la bienvenida cuenta las primeras canciones

    def _downloads_tip(self) -> str:
        left = self.downloads_left
        if self.dl_paused:
            return f"Descargas en pausa ({left} pendientes)" if left else "Descargas en pausa"
        if self.disk_level != "ok":
            return "Descargas limitadas: poco espacio"
        if left:
            return f"Descargas: {left} {'pendiente' if left == 1 else 'pendientes'}"
        return "Descargas"

    # ---------- tema ----------
    def set_theme_mode(self, mode: str) -> None:
        if mode not in THEMES:
            return
        self.theme_mode = mode
        self._apply_dark(system_is_dark() if mode == "system" else mode == "dark")
        self.save_prefs()

    def cycle_theme(self) -> None:
        """Como Windows → Claro → Oscuro → Como Windows."""
        self.set_theme_mode(THEMES[(THEMES.index(self.theme_mode) + 1) % len(THEMES)])
        self.status.configure(text=f"Tema: {THEME_LABELS[self.theme_mode]}")

    def toggle_theme(self) -> None:
        """Claro ↔ oscuro, elegido a propósito (deja de seguir a Windows)."""
        self.set_theme_mode("light" if self.dark else "dark")

    def _apply_dark(self, dark: bool) -> None:
        if dark == self.dark:
            return
        self.dark = dark
        self.apply_theme()
        self.anim.tween("window", 200, lambda q: self.attributes("-alpha", 0.82 + 0.18 * q))

    def _follow_system_theme(self) -> None:
        """Siguiendo a Windows, la ventana cambia de tema cuando cambia el sistema."""
        if self.theme_mode == "system":
            self._apply_dark(system_is_dark())
        self.after(SYSTEM_POLL_MS, self._follow_system_theme)
