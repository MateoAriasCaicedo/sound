"""Página de Ajustes: apariencia, reproducción, descargas, biblioteca y atajos.

Reúne en un solo sitio opciones que antes estaban repartidas entre iconos, menús
contextuales y la página de Descargas. Los cambios se aplican al momento y se guardan.

Lo que es sí / no lleva un interruptor; lo que tiene varias opciones, todas a la vista con la
elegida marcada. A la izquierda, un índice salta a cada sección y marca en cuál se está (se
oculta si la ventana es estrecha).
"""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__
from ..core import app_update, config, downloader, startup
from .anim import reduced_motion
from .chrome import THEME_LABELS, THEMES
from .page import Page
from .theme import system_is_dark
from .widgets import PillButton, RowList, Segmented, Switch, bind_touchpad, touchpad_deltas

XFADE_OPTIONS = ((0, "No"), (2000, "2 s"), (4000, "4 s"), (6000, "6 s"), (8000, "8 s"),
                 (12000, "12 s"))
INDEX_W = 168        # ancho del índice de secciones, a 96 ppp
INDEX_MIN_PAGE = 780  # por debajo de este ancho de página el índice se oculta


class SettingsPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Ajustes", status="Los cambios se guardan al momento")
        self._pal = None
        self._buttons: list[PillButton] = []
        self._controls: list = []  # interruptores y selectores
        self._sections: list[tuple[str, tk.Frame]] = []  # (nombre en el índice, sección)
        self._pinned: int | None = None  # sección elegida en el índice (manda mientras se vea)

        self.body.columnconfigure(1, weight=1)
        self.body.rowconfigure(0, weight=1)

        self.index = RowList(self.body, self.app.anim, self.k, self.fonts, multi=False,
                             on_select=self._index_picked)
        self.index.configure(width=int(INDEX_W * self.k))
        self.index.grid(row=0, column=0, sticky="ns", padx=(0, int(20 * self.k)))
        self.outer = tk.Canvas(self.body, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self.body, orient="vertical", command=self.outer.yview)
        self.outer.configure(yscrollcommand=self._scrolled)
        self.outer.grid(row=0, column=1, sticky="nsew")
        self.scroll.grid(row=0, column=2, sticky="ns")
        self.body.bind("<Configure>", self._body_resized, add="+")

        self.inner = tk.Frame(self.outer)
        self._window = self.outer.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _e: self.outer.configure(scrollregion=self.outer.bbox("all")))
        self.outer.bind(
            "<Configure>",
            lambda e: self.outer.itemconfigure(self._window, width=max(1, e.width)))

        self._build_appearance()
        self._build_playback()
        self._build_downloads()
        self._build_system()
        self._build_updates()
        self._build_library()
        self._build_shortcuts()
        self._build_about()
        self.index.set_items([dict(text=name) for name, _frame in self._sections])
        self.index.selection_set(0)
        self._bind_wheel(self.inner)
        self.outer.bind("<MouseWheel>", self._wheel)
        bind_touchpad(self.outer, self._touchpad)

        self.restyle()
        self._refresh_labels()

    # ---------- construcción ----------
    def _label(self, parent, text: str, role: str = "fg", font=None) -> tk.Label:
        label = tk.Label(parent, text=text, anchor="w", justify="left",
                         font=font or self.fonts["body"])
        label._sound_role = role  # lo usa _paint para recolorear en cada tema
        return label

    def _section(self, title: str, desc: str = "", nav: str | None = None) -> tk.Frame:
        frame = tk.Frame(self.inner)
        self._sections.append((nav or title, frame))
        frame.pack(fill="x", pady=(0, int(20 * self.k)))
        self._label(frame, title, "fg", self.fonts["strong"]).pack(anchor="w")
        if desc:
            self._label(frame, desc, "dim", self.fonts["small"]).pack(
                anchor="w", pady=(int(2 * self.k), 0))
        line = tk.Frame(frame, height=1)
        line._sound_role = "line"
        line.pack(fill="x", pady=(int(7 * self.k), int(10 * self.k)))
        return frame

    def _row_frame(self, parent, title: str, desc: str = "") -> tk.Frame:
        row = tk.Frame(parent)
        row.pack(fill="x", pady=(0, int(10 * self.k)))
        row.columnconfigure(0, weight=1)
        left = tk.Frame(row)
        left.grid(row=0, column=0, sticky="w")
        self._label(left, title, "fg", self.fonts["body"]).pack(anchor="w")
        if desc:
            self._label(left, desc, "dim", self.fonts["small"]).pack(
                anchor="w", pady=(int(2 * self.k), 0))
        return row

    def _switch(self, parent, title: str, desc: str, command) -> Switch:
        """Una fila de sí / no con su interruptor a la derecha."""
        row = self._row_frame(parent, title, desc)
        switch = Switch(row, self.app.anim, self.k, command)
        switch.grid(row=0, column=1, sticky="e", padx=(int(14 * self.k), int(4 * self.k)))
        self._controls.append(switch)
        return switch

    def _choice(self, parent, title: str, desc: str, options, command) -> Segmented:
        """Una fila con todas sus opciones a la vista y la elegida marcada."""
        row = self._row_frame(parent, title, desc)
        choice = Segmented(row, self.app.anim, self.k, options, command, self.fonts["body"])
        choice.grid(row=0, column=1, sticky="e", padx=(int(14 * self.k), 0))
        self._controls.append(choice)
        return choice

    def _build_appearance(self) -> None:
        section = self._section("Apariencia",
                                "El color de la ventana sigue a la portada de la canción.")
        self.btn_theme = self._choice(
            section, "Tema", "«Como Windows» cambia solo cuando cambia el sistema.",
            [(mode, THEME_LABELS[mode]) for mode in THEMES], self.set_theme)
        self.btn_view = self._choice(
            section, "Vista de la biblioteca",
            "Lista con columnas o cuadrícula con portadas; también Ctrl+Shift+L.",
            [("list", "Lista"), ("grid", "Cuadrícula")], self.set_view)
        self.btn_motion = self._switch(
            section, "Animaciones",
            "Desactívalas si prefieres transiciones instantáneas o usas lector de pantalla.",
            self.toggle_motion)

    def _build_playback(self) -> None:
        section = self._section("Reproducción", "Lo mismo que los botones de la barra inferior.")
        self.btn_shuffle = self._switch(
            section, "Orden aleatorio", "Mezcla la lista o la cola automática.",
            self.toggle_shuffle)
        self.btn_repeat = self._choice(
            section, "Repetir", "Toda la lista o solo la canción que suena.",
            [(0, "No"), (1, "Lista"), (2, "Canción")], self.set_repeat)
        self.btn_auto = self._switch(
            section, "Cola automática", "Añade canciones similares cuando se acaba la cola.",
            self.toggle_auto)
        self.btn_xfade = self._choice(
            section, "Transición",
            "Crossfade entre canciones, ajustado a su energía; con «No», paso sin hueco.",
            XFADE_OPTIONS, self.set_xfade)
        self.btn_normalize = self._switch(
            section, "Normalizar volumen",
            "Iguala el volumen con ReplayGain; necesita ffmpeg instalado.",
            self.toggle_normalize)
        self.btn_lyrics = self._switch(
            section, "Letras",
            "Busca la letra sincronizada en LRCLIB; requiere internet y envía título y artista.",
            self.toggle_lyrics)

    def _build_downloads(self) -> None:
        section = self._section(
            "Descargas",
            "Si YouTube pide iniciar sesión, elige aquí el navegador donde ya la tengas.")
        row = self._row_frame(
            section, "Cookies de YouTube",
            "Se leen en tu equipo; no se envían a ningún servidor de Sound.")
        self.cookie_var = tk.StringVar(value=self.app.cookies)
        self.cookie_box = ttk.Combobox(
            row, textvariable=self.cookie_var, state="readonly", width=14,
            values=("", *downloader.COOKIE_BROWSERS), font=self.fonts["body"])
        self.cookie_box.grid(row=0, column=1, sticky="e", padx=(int(14 * self.k), 0))
        self.cookie_box.bind("<<ComboboxSelected>>", self._cookie_selected)
        self.btn_auto_update = self._switch(
            section, "Actualizar yt-dlp automáticamente",
            "Busca una versión nueva al arrancar (como mucho una vez al día) y la usa al "
            "reiniciar. No interrumpe descargas.",
            self.toggle_auto_update)

    def _build_system(self) -> None:
        section = self._section(
            "Sistema",
            "Integración con Windows. No hace falta ser administrador.")
        self.btn_start_windows = self._switch(
            section, "Iniciar con Windows",
            "Abre Sound automáticamente al iniciar sesión. Al desinstalarlo se quita solo.",
            self.toggle_start_windows)

    def _build_updates(self) -> None:
        section = self._updates_section = self._section(
            "Actualizaciones de Sound",
            "Se mira la lista pública de versiones del proyecto en GitHub; no se envía nada tuyo.",
            nav="Actualizaciones")
        self.btn_app_updates = self._switch(
            section, "Avisar de versiones nuevas",
            "Mira una vez al día, al abrir Sound, y avisa si hay una versión más nueva.",
            self.toggle_app_updates)
        row = self._row_frame(section, f"Tienes Sound {__version__}")
        self.update_note = self._label(row.grid_slaves(row=0, column=0)[0], "", "dim",
                                       self.fonts["small"])
        self.update_note.pack(anchor="w", pady=(int(2 * self.k), 0))
        self.btn_check = PillButton(row, self.app.anim, self.k, "Buscar ahora",
                                    lambda: self.app._maybe_check_app_update(manual=True),
                                    self.fonts["body"], variant="secondary", min_width=148)
        self.btn_check.grid(row=0, column=1, sticky="e", padx=(int(14 * self.k), 0))
        self._buttons.append(self.btn_check)
        # lo nuevo: aparece solo si hay una versión más nueva
        self.update_box = tk.Frame(section)
        self.update_title = self._label(self.update_box, "", "fg", self.fonts["strong"])
        self.update_title.pack(anchor="w")
        self.update_notes = self._label(self.update_box, "", "dim", self.fonts["small"])
        self.update_notes.pack(anchor="w", pady=(int(4 * self.k), 0))
        buttons = tk.Frame(self.update_box)
        buttons.pack(anchor="w", pady=(int(10 * self.k), 0))
        self.btn_install = PillButton(buttons, self.app.anim, self.k, "Actualizar ahora",
                                      self.app.install_app_update, self.fonts["body"],
                                      variant="primary", min_width=170)
        self.btn_install.grid(row=0, column=0, sticky="w")
        self.btn_release = PillButton(buttons, self.app.anim, self.k, "Novedades en GitHub",
                                      self._open_release, self.fonts["body"],
                                      variant="secondary", min_width=0)
        self.btn_release.grid(row=0, column=1, sticky="w", padx=(int(8 * self.k), 0))
        self._buttons += [self.btn_install, self.btn_release]
        self._update_box_shown = False

    def _open_release(self) -> None:
        import webbrowser
        release = self.app.app_update
        webbrowser.open(release.page_url if release else app_update.RELEASES_PAGE)

    def show_updates(self) -> None:
        """Lleva la vista al apartado de actualizaciones (desde el aviso «Ver»)."""
        index = next(i for i, (_n, frame) in enumerate(self._sections)
                     if frame is self._updates_section)
        self._jump(index)

    def refresh_updates(self) -> None:
        app = self.app
        release, state = app.app_update, app.app_update_state
        self.btn_app_updates.set(app.app_updates)
        busy = state in ("checking", "downloading", "installing")
        self.btn_check.set_enabled(not busy)
        self.btn_check.set_text("Buscando…" if state == "checking" else "Buscar ahora")
        note = app.app_update_note
        if not note and release is not None:
            note = f"Sound {release.version} está disponible."
        if not note:
            note = ("Aún no se ha buscado." if not app._app_update_at else
                    f"Tienes la última versión ({__version__}).")
        self.update_note.configure(text=note)
        if release is None:
            self.update_box.pack_forget()
            self._update_box_shown = False
            return
        self.update_title.configure(text=f"Novedades de Sound {release.version}")
        notes = app_update.summary(release.notes) or "Mejoras y correcciones."
        self.update_notes.configure(text=notes, wraplength=int(640 * self.k))
        installs = config.is_frozen() and release.installable
        self.btn_install.set_text(
            {"downloading": app.app_update_note or "Descargando…",
             "installing": "Instalando…"}.get(state, "Actualizar ahora" if installs
                                             else "Descargar desde GitHub"))
        self.btn_install.set_enabled(state not in ("downloading", "installing"))
        if not self._update_box_shown:
            self._update_box_shown = True
            self.update_box.pack(fill="x", pady=(int(2 * self.k), int(4 * self.k)))
            self._paint(self.update_box)

    def _build_library(self) -> None:
        section = self._section(
            "Biblioteca",
            "Acciones de mantenimiento que también están en el clic derecho de la lista.")
        row = tk.Frame(section)
        row.pack(fill="x")
        for column, (text, command) in enumerate((
                ("Canciones repetidas…", self.app.find_duplicates),
                ("Exportar M3U…", self.app.export_m3u),
                ("Quitar archivos que faltan…", self.app.clean_missing))):
            button = PillButton(row, self.app.anim, self.k, text, command,
                                self.fonts["body"], variant="secondary", min_width=0)
            button.grid(row=0, column=column, sticky="w",
                        padx=(0 if column == 0 else int(8 * self.k), 0))
            self._buttons.append(button)

    def _build_shortcuts(self) -> None:
        section = self._section("Atajos", "Funciona en cualquier momento salvo al escribir en un campo.")
        shortcuts = (
            ("Ctrl+F", "Buscar en la biblioteca"),
            ("Ctrl+G", "Música por frase"),
            ("Ctrl+L", "Me gusta / quitar Me gusta (selección o lo que suena)"),
            ("Ctrl+Z", "Deshacer lo último (mientras se ve el aviso)"),
            ("Ctrl+A", "Seleccionar toda la vista"),
            ("Ctrl+Shift+L", "Alternar lista y cuadrícula"),
            ("Ctrl+Shift+M", "Mini reproductor / volver a la ventana"),
            ("Ctrl+Shift+F", "Reproduciendo en grande: portada, álbum, letra y cola"),
            ("F11", "Pantalla completa con la vista en grande"),
            ("Ctrl+,  /  F1", "Abrir Ajustes y esta ayuda"),
            ("Ctrl+M", "Silenciar / recuperar el volumen"),
            ("Ctrl+↑ / ↓", "Subir o bajar el volumen"),
            ("Espacio", "Reproducir o pausar"),
            ("Intro", "Reproducir la canción seleccionada o abrir el álbum"),
            ("Supr", "Eliminar de la biblioteca / quitar de la lista o de la cola"),
            ("Alt+← / →", "Atrás / Adelante (también los botones laterales del ratón)"),
            ("Alt+↑ / ↓", "Subir o bajar canciones dentro de una lista"),
            ("Esc", "Salir de la vista en grande, cerrar «A continuación» o volver atrás "
                    "desde un álbum, un artista o una página como esta"),
        )
        for keys, desc in shortcuts:
            row = tk.Frame(section)
            row.pack(fill="x", pady=(0, int(5 * self.k)))
            row.columnconfigure(0, minsize=int(116 * self.k))
            row.columnconfigure(1, weight=1)
            self._label(row, keys, "fg", self.fonts["digits"]).grid(
                row=0, column=0, sticky="w")
            self._label(row, desc, "dim", self.fonts["small"]).grid(
                row=0, column=1, sticky="w", padx=(int(12 * self.k), 0))

    def _build_about(self) -> None:
        section = self._section("Acerca de")
        self._label(section, f"Sound {__version__} · biblioteca local y YouTube Music",
                    "dim", self.fonts["small"]).pack(anchor="w")
        self._label(
            section,
            "La reproducción y las descargas se hacen en tu equipo. "
            "El detalle de errores está en Diagnóstico → Abrir registro.",
            "faint", self.fonts["small"]).pack(anchor="w", pady=(int(3 * self.k), 0))
        row = tk.Frame(section)
        row.pack(fill="x", pady=(int(12 * self.k), 0))
        reset = PillButton(row, self.app.anim, self.k, "Restaurar ajustes", self.reset,
                           self.fonts["body"], variant="secondary", min_width=150)
        reset.grid(row=0, column=0, sticky="w")
        self._buttons.append(reset)

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        self.cookie_var.set(self.app.cookies)
        self._refresh_labels()
        self._pinned = None
        self.outer.yview_moveto(0)
        self.outer.focus_set()

    # ---------- acciones ----------
    def set_theme(self, mode: str) -> None:
        self.app.set_theme_mode(mode)
        self._refresh_labels()

    def set_view(self, view: str) -> None:
        if view != self.app.view_pref:
            self.app.toggle_view_mode()
        self._refresh_labels()

    def toggle_motion(self) -> None:
        self.app.toggle_reduce_motion()
        self._refresh_labels()

    def toggle_shuffle(self) -> None:
        self.app.toggle_shuffle()
        self._refresh_labels()

    def set_repeat(self, mode: int) -> None:
        self.app.set_repeat(mode)
        self._refresh_labels()

    def toggle_auto(self) -> None:
        self.app.toggle_auto()
        self._refresh_labels()

    def set_xfade(self, ms: int) -> None:
        self.app.set_xfade(ms)
        self._refresh_labels()

    def toggle_normalize(self) -> None:
        self.app.var_normalize.set(not self.app.normalize)
        self.app.toggle_normalize()
        self._refresh_labels()

    def toggle_lyrics(self) -> None:
        self.app.toggle_lyrics()
        self._refresh_labels()

    def toggle_auto_update(self) -> None:
        self.app.toggle_auto_update()
        self._refresh_labels()

    def toggle_start_windows(self) -> None:
        self.app.toggle_start_with_windows()
        self._refresh_labels()

    def toggle_app_updates(self) -> None:
        self.app.toggle_app_updates()
        self._refresh_labels()

    def _cookie_selected(self, _e=None) -> None:
        self.app.set_cookies(self.cookie_var.get())

    def reset(self) -> None:
        if not messagebox.askyesno(
                "Restaurar ajustes",
                "Se restauran tema, volumen, orden, repetición, cola automática, transición, "
                "normalización, letras, animaciones y actualización automática.\n\nLa biblioteca, "
                "las cookies y las descargas no se tocan.\n\n¿Continuar?", parent=self):
            return
        app = self.app
        app.theme_mode = "system"
        app.dark = system_is_dark()
        app.pq.shuffle = False
        if app.pq.ctx and app.pq.order:
            app.pq.make_order(app.pq.order[app.pq.opos])
        app.pq.repeat = 0
        app.autoplay = True
        app.xfade_ms = 4000
        app.var_normalize.set(False)
        app.normalize = False
        app.var_lyrics.set(False)
        app.lyrics_enabled = False
        app.auto_update = True
        app._ytdlp_checked_at = 0.0
        app.player.set_gain_db(0)
        app.anim.set_reduced(reduced_motion())
        app.vol.set(80)
        app.apply_theme()
        app._refresh_toggles()
        app.save_prefs()
        self._refresh_labels()
        self.app.status.configure(text="Ajustes restaurados")

    # ---------- vista ----------
    def _refresh_labels(self) -> None:
        app = self.app
        self.btn_theme.set(app.theme_mode)
        self.btn_view.set(app.view_pref)
        self.btn_motion.set(not app.anim.reduced)
        self.btn_shuffle.set(app.pq.shuffle)
        self.btn_repeat.set(app.pq.repeat)
        self.btn_auto.set(app.autoplay)
        self.btn_xfade.set(app.xfade_ms if app.xfade_ms in dict(XFADE_OPTIONS) else 4000)
        self.btn_normalize.set(app.normalize)
        self.btn_lyrics.set(app.lyrics_enabled)
        self.btn_auto_update.set(app.auto_update)
        self.btn_start_windows.set_enabled(startup.available())
        self.btn_start_windows.set(app.start_with_windows and startup.available())
        self.refresh_updates()

    # ---------- índice de secciones ----------
    def _index_picked(self) -> None:
        picked = self.index.curselection()
        if picked:
            self._jump(picked[0])

    def _jump(self, index: int) -> None:
        """Lleva la sección arriba (o lo más arriba que se pueda) y la deja marcada."""
        self.update_idletasks()
        total = max(1, self.inner.winfo_height())
        self._pinned = index
        frame = self._sections[index][1]
        self.outer.yview_moveto(max(0.0, (frame.winfo_y() - 8) / total))
        self._mark_index(index)

    def _scrolled(self, first: str, last: str) -> None:
        """La barra y el índice siguen al desplazamiento: se marca la sección que se lee."""
        self.scroll.set(first, last)
        if not self._sections:
            return
        total = max(1, self.inner.winfo_height())
        top, bottom = float(first) * total, float(last) * total
        current = 0
        for i, (_name, frame) in enumerate(self._sections):
            if frame.winfo_y() <= top + int(30 * self.k):
                current = i
        if float(last) >= 0.999:  # abajo del todo: las últimas no llegan arriba
            current = len(self._sections) - 1
        if self._pinned is not None:
            y = self._sections[self._pinned][1].winfo_y()
            if top - 2 <= y <= bottom:
                current = self._pinned  # la que se eligió en el índice, mientras se vea
            else:
                self._pinned = None
        self._mark_index(current)

    def _mark_index(self, index: int) -> None:
        if self.index.curselection() != (index,):
            self.index.selection_clear()
            self.index.selection_set(index)

    def _body_resized(self, e) -> None:
        """Con la ventana estrecha el índice se oculta: las opciones necesitan el sitio."""
        if e.widget is not self.body:
            return
        if e.width < int(INDEX_MIN_PAGE * self.k):
            self.index.grid_remove()
        else:
            self.index.grid()

    def _wheel(self, e) -> str:
        self.outer.yview_scroll(self.app._wheel_steps(e), "units")
        return "break"

    def _touchpad(self, e) -> str:
        _dx, dy = touchpad_deltas(e)
        if dy:
            self.outer.tk.call("tk::ScrollByPixels", self.outer._w, 0, dy)
        return "break"

    def _bind_wheel(self, widget) -> None:
        for child in widget.winfo_children():
            child.bind("<MouseWheel>", self._wheel, add="+")
            bind_touchpad(child, self._touchpad, add=True)
            self._bind_wheel(child)

    def _paint(self, widget) -> None:
        pal = self.app.pal
        for child in widget.winfo_children():
            role = getattr(child, "_sound_role", None)
            if isinstance(child, tk.Label):
                if role:  # las etiquetas propias de esta página llevan su papel
                    child.configure(bg=pal["bg"], fg=pal.get(role, pal["fg"]))
                else:  # las de Page (← Volver, pista) conservan su color
                    child.configure(bg=pal["bg"])
            elif isinstance(child, (tk.Frame, tk.Canvas)):
                child.configure(bg=pal.get(role or "bg", pal["bg"]))
            self._paint(child)

    def _restyle_content(self) -> None:
        pal = self.app.pal
        self._pal = pal
        self._paint(self)
        style = ttk.Style(self)
        style.configure("Settings.TCombobox", fieldbackground=pal["field"],
                        background=pal["field"], foreground=pal["fg"], arrowcolor=pal["fg"],
                        bordercolor=pal["line"], lightcolor=pal["line"],
                        darkcolor=pal["line"])
        self.cookie_box.configure(style="Settings.TCombobox")
        for button in self._buttons:
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], on_accent=pal["accent_fg"],
                           danger=pal["danger"])
        for control in self._controls:
            control.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], accent=pal["accent"],
                            on_accent=pal["accent_fg"])
        self.index.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           sel=pal["sel"], hover=pal["hover"])
        self._refresh_labels()
