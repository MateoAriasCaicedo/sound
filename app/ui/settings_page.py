"""Página de Ajustes: apariencia, reproducción, descargas, biblioteca y atajos.

Reúne en un solo sitio opciones que antes estaban repartidas entre iconos, menús
contextuales y la página de Descargas. Los cambios se aplican al momento y se guardan.
"""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__
from ..core import downloader, startup
from .anim import reduced_motion
from .page import Page
from .theme import system_is_dark
from .widgets import PillButton


class SettingsPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Ajustes", hint="Apariencia, reproducción, descargas y atajos",
                         status="Los cambios se guardan al momento")
        self._pal = None
        self._buttons: list[PillButton] = []

        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        self.outer = tk.Canvas(self.body, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self.body, orient="vertical", command=self.outer.yview)
        self.outer.configure(yscrollcommand=self.scroll.set)
        self.outer.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")

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
        self._build_library()
        self._build_shortcuts()
        self._build_about()
        self._bind_wheel(self.inner)
        self.outer.bind("<MouseWheel>", self._wheel)

        self.restyle()
        self._refresh_labels()

    # ---------- construcción ----------
    def _label(self, parent, text: str, role: str = "fg", font=None) -> tk.Label:
        label = tk.Label(parent, text=text, anchor="w", justify="left",
                         font=font or self.fonts["body"])
        label._sound_role = role  # lo usa _paint para recolorear en cada tema
        return label

    def _section(self, title: str, desc: str = "") -> tk.Frame:
        frame = tk.Frame(self.inner)
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

    def _setting(self, parent, title: str, desc: str, text: str, command,
                 variant: str = "secondary", width: int = 148) -> PillButton:
        row = self._row_frame(parent, title, desc)
        button = PillButton(row, self.app.anim, self.k, text, command, self.fonts["body"],
                            variant=variant, min_width=width)
        button.grid(row=0, column=1, sticky="e", padx=(int(14 * self.k), 0))
        self._buttons.append(button)
        return button

    def _build_appearance(self) -> None:
        section = self._section("Apariencia",
                                "El color de la ventana sigue a la portada de la canción.")
        self.btn_theme = self._setting(
            section, "Tema", "Claro u oscuro; también con el icono de la barra superior.",
            "", self.toggle_theme)
        self.btn_motion = self._setting(
            section, "Animaciones",
            "Desactívalas si prefieres transiciones instantáneas o usas lector de pantalla.",
            "", self.toggle_motion)

    def _build_playback(self) -> None:
        section = self._section("Reproducción", "Lo mismo que los botones de la barra inferior.")
        self.btn_shuffle = self._setting(
            section, "Orden aleatorio", "Mezcla la lista o la cola automática.",
            "", self.toggle_shuffle)
        self.btn_repeat = self._setting(
            section, "Repetir", "Sin repetir, repetir toda la lista o repetir una canción.",
            "", self.cycle_repeat)
        self.btn_auto = self._setting(
            section, "Cola automática", "Añade canciones similares cuando se acaba la cola.",
            "", self.toggle_auto)
        self.btn_xfade = self._setting(
            section, "Transición", "Crossfade entre canciones; sin crossfade el paso es sin hueco.",
            "", self.cycle_xfade)
        self.btn_normalize = self._setting(
            section, "Normalizar volumen",
            "Iguala el volumen con ReplayGain; necesita ffmpeg instalado.",
            "", self.toggle_normalize)

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
        self.btn_auto_update = self._setting(
            section, "Actualizar yt-dlp automáticamente",
            "Busca una versión nueva al arrancar (como mucho una vez al día) y la usa al "
            "reiniciar. No interrumpe descargas.",
            "", self.toggle_auto_update)

    def _build_system(self) -> None:
        section = self._section(
            "Sistema",
            "Integración con Windows. No hace falta ser administrador.")
        self.btn_start_windows = self._setting(
            section, "Iniciar con Windows",
            "Abre Sound automáticamente al iniciar sesión. Al desinstalarlo se quita solo.",
            "", self.toggle_start_windows)

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
            ("Ctrl+L", "Me gusta / quitar Me gusta"),
            ("Ctrl+A", "Seleccionar toda la vista"),
            ("Ctrl+,  /  F1", "Abrir Ajustes y esta ayuda"),
            ("Ctrl+M", "Silenciar / recuperar el volumen"),
            ("Ctrl+↑ / ↓", "Subir o bajar el volumen"),
            ("Espacio", "Reproducir o pausar"),
            ("Intro", "Reproducir la canción seleccionada"),
            ("Supr", "Eliminar de la biblioteca / quitar de la cola"),
            ("Esc", "Volver a la biblioteca o cerrar «A continuación»"),
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
        self.outer.yview_moveto(0)
        self.outer.focus_set()

    # ---------- acciones ----------
    def toggle_theme(self) -> None:
        self.app.toggle_theme()
        self._refresh_labels()

    def toggle_motion(self) -> None:
        self.app.toggle_reduce_motion()
        self._refresh_labels()

    def toggle_shuffle(self) -> None:
        self.app.toggle_shuffle()
        self._refresh_labels()

    def cycle_repeat(self) -> None:
        self.app.cycle_repeat()
        self._refresh_labels()

    def toggle_auto(self) -> None:
        self.app.toggle_auto()
        self._refresh_labels()

    def cycle_xfade(self) -> None:
        self.app.cycle_xfade()
        self._refresh_labels()

    def toggle_normalize(self) -> None:
        self.app.var_normalize.set(not self.app.normalize)
        self.app.toggle_normalize()
        self._refresh_labels()

    def toggle_auto_update(self) -> None:
        self.app.toggle_auto_update()
        self._refresh_labels()

    def toggle_start_windows(self) -> None:
        self.app.toggle_start_with_windows()
        self._refresh_labels()

    def _cookie_selected(self, _e=None) -> None:
        self.app.set_cookies(self.cookie_var.get())

    def reset(self) -> None:
        if not messagebox.askyesno(
                "Restaurar ajustes",
                "Se restauran tema, volumen, orden, repetición, cola automática, transición, "
                "normalización, animaciones y actualización automática.\n\nLa biblioteca, las "
                "cookies y las descargas no se tocan.\n\n¿Continuar?", parent=self):
            return
        app = self.app
        app.dark = system_is_dark()
        app.pq.shuffle = False
        if app.pq.ctx and app.pq.order:
            app.pq.make_order(app.pq.order[app.pq.opos])
        app.pq.repeat = 0
        app.autoplay = True
        app.xfade_ms = 4000
        app.var_normalize.set(False)
        app.normalize = False
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
        self.btn_theme.set_text("Oscuro" if self.app.dark else "Claro")
        self.btn_motion.set_text("Activadas" if not self.app.anim.reduced else "Reducidas")
        self.btn_shuffle.set_text("Activado" if self.app.pq.shuffle else "Desactivado")
        repeat = ("Sin repetir", "Repetir lista", "Repetir canción")[self.app.pq.repeat]
        self.btn_repeat.set_text(repeat)
        self.btn_auto.set_text("Activada" if self.app.autoplay else "Desactivada")
        xfade = self.app.xfade_ms
        self.btn_xfade.set_text("Sin crossfade" if not xfade else f"{xfade // 1000} s")
        self.btn_normalize.set_text("Activada" if self.app.normalize else "Desactivada")
        self.btn_auto_update.set_text("Activada" if self.app.auto_update else "Desactivada")
        self.btn_start_windows.set_enabled(startup.available())
        self.btn_start_windows.set_text(
            ("Activado" if self.app.start_with_windows else "Desactivado")
            if startup.available() else "No disponible")

    def _wheel(self, e) -> None:
        self.outer.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _bind_wheel(self, widget) -> None:
        for child in widget.winfo_children():
            child.bind("<MouseWheel>", self._wheel, add="+")
            self._bind_wheel(child)

    def _paint(self, widget) -> None:
        pal = self.app.pal
        for child in widget.winfo_children():
            role = getattr(child, "_sound_role", None)
            if isinstance(child, tk.Label):
                if role:  # las etiquetas propias de esta página llevan su papel
                    child.configure(bg=pal["bg"], fg=pal.get(role, pal["fg"]))
                else:  # las de Page (← Biblioteca, pista) conservan su color
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
        self._refresh_labels()
