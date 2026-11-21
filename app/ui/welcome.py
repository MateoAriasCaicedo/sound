"""Bienvenida: lo que se ve con la biblioteca vacía (la primera vez, o si se borra todo).

En Inicio y en Todo, en vez de una tabla vacía con sus columnas:
- tres maneras de empezar: buscar, importar un enlace o pedir música con una frase;
- los géneros de YouTube del lateral, como atajo;
- mientras bajan las primeras canciones, cuántas quedan y un enlace a Descargas.

Se va sola en cuanto llega la primera canción (la vista se repinta y ya hay filas).
"""
from __future__ import annotations

import tkinter as tk

from PIL import ImageTk

from ..core import config
from .icons import icon_mask
from .theme import blend
from .widgets import ChipBar, TextButton, _layer, _rounded_mask

TITLE = "Te damos la bienvenida"  # corto: en la ventana mínima no se recorta
INTRO = ("Sound descarga música de YouTube Music (solo la versión original: sin mixes, covers ni "
         "directos), la ordena por géneros y la guarda en tu equipo, así que lo descargado suena "
         "también sin conexión. Para empezar:")
CARDS = (  # (icono, título, atajo, frase)
    ("search", "Buscar", "Ctrl+F",
     "Una canción, un artista o un álbum. Suena al momento y se descarga con un clic."),
    ("link", "Importar un enlace", "",
     "Pega un álbum, una playlist o un artista de YouTube Music y se descarga lo que falte."),
    ("note", "Música por frase", "Ctrl+G",
     "Escribe lo que te apetece, como «rock de los 90», y Sound busca las canciones."),
)
GENRES_TITLE = "O empieza por un género"
MAX_WIDTH = 820  # más ancho, las tarjetas solo se estirarían


def wanted(library_size: int, view_kind: str, query: str, sub_view, searching: bool,
           filtered: bool) -> bool:
    """¿Toca la bienvenida? Con la biblioteca vacía, en Inicio o Todo, sin buscar ni filtrar."""
    return (library_size == 0 and view_kind in ("home", "all") and not query.strip()
            and sub_view is None and not searching and not filtered)


def progress_text(left: int) -> str | None:
    """Lo que se dice mientras bajan las primeras canciones; None si no baja nada."""
    if left <= 0:
        return None
    rest = "queda 1" if left == 1 else f"quedan {left}"
    return f"Descargando tus primeras canciones: {rest}. Aparecerán aquí en cuanto terminen."


def fit_count(widths: list[int], room: int) -> int:
    """Cuántas pastillas seguidas (cada ancho ya con su hueco) caben en `room`."""
    total = 0
    for i, width in enumerate(widths):
        total += width
        if total > room:
            return i
    return len(widths)


class ActionCard(tk.Canvas):
    """Tarjeta pulsable: icono, título, atajo y una frase. Se aviva al pasar el ratón y se
    activa también con Intro o Espacio (entra en el orden de tabulación)."""

    def __init__(self, master, anim, k: float, fonts, icon: str, title: str, shortcut: str,
                 text: str, command) -> None:
        super().__init__(master, width=int(180 * k), height=int(140 * k), highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=1)
        self.anim, self.k, self.fonts, self.command = anim, k, fonts, command
        self.title, self.shortcut, self.text = title, shortcut, text
        self.h = 0.0
        self.focused = False
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555",
                      accent="#ffffff")
        self._mask = icon_mask(icon, int(24 * k))
        self._photos: list = []
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", lambda e: self._hover(0.0))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.command())
        self.bind("<space>", lambda e: self.command())
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))

    def restyle(self, bg: str, fg: str, dim: str, faint: str, accent: str) -> None:
        self.c.update(bg=bg, fg=fg, dim=dim, faint=faint, accent=accent)
        self.configure(bg=bg)
        self._render()

    def _hover(self, to: float) -> None:
        start = self.h

        def step(p: float) -> None:
            self.h = start + (to - start) * p
            self._render()

        self.anim.tween(("card", id(self)), 120 if to else 180, step)

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._render()

    def _release(self, e) -> None:
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

    def _render(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        k, c, f = self.k, self.c, self.fonts
        self.delete("all")
        lift = max(self.h, 0.6 if self.focused else 0.0)
        back = _layer(_rounded_mask(w, h, int(12 * k)), blend(c["bg"], c["fg"], 0.05 + 0.05 * lift))
        self._photos = [ImageTk.PhotoImage(back, master=self),
                        ImageTk.PhotoImage(_layer(self._mask, c["accent"]), master=self)]
        pad = int(18 * k)
        self.create_image(0, 0, anchor="nw", image=self._photos[0])
        self.create_image(pad, pad, anchor="nw", image=self._photos[1])
        if self.shortcut:
            self.create_text(w - pad, pad + self._mask.height // 2, text=self.shortcut,
                             anchor="e", font=f["small"], fill=c["faint"])
        y = pad + self._mask.height + int(12 * k)
        title = self.create_text(pad, y, text=self.title, anchor="nw", font=f["strong"],
                                 fill=c["fg"], width=w - 2 * pad)
        y = self.bbox(title)[3] + int(4 * k)
        text = self.create_text(pad, y, text=self.text, anchor="nw", font=f["small"],
                                fill=c["dim"], width=w - 2 * pad)
        need = self.bbox(text)[3] + pad  # la frase envuelve según el ancho: la altura la sigue
        if abs(need - int(self.cget("height"))) > 1:
            self.configure(height=need)


class Welcome(tk.Frame):
    """El panel de bienvenida, sobre la zona de la lista (la tapa entera, columnas incluidas)."""

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        k, f = app.k, app.fonts
        self.k = k
        self._genres: list[str] = []
        self._width = 0
        self.body = tk.Frame(self)
        self.body.columnconfigure(0, weight=1)
        self.intro = tk.Label(self.body, text=INTRO, font=f["body"], justify="left", anchor="w")
        self.intro.grid(row=0, column=0, sticky="ew")

        self.progress = tk.Frame(self.body)  # solo mientras bajan las primeras canciones
        self.progress_label = tk.Label(self.progress, font=f["strong"], justify="left",
                                       anchor="w")
        self.progress_label.pack(side="left")
        self.progress_link = TextButton(self.progress, app.anim, "Ver descargas", f["body"],
                                        app.open_queue)
        self.progress_link.pack(side="left", padx=(int(4 * k), 0))

        self.cards_row = tk.Frame(self.body)
        self.cards_row.grid(row=2, column=0, sticky="ew", pady=(int(18 * k), 0))
        actions = (app._welcome_search, app.open_import, app.open_prompt)
        self.cards: list[ActionCard] = []
        for i, ((icon, title, shortcut, text), command) in enumerate(zip(CARDS, actions)):
            card = ActionCard(self.cards_row, app.anim, k, f, icon, title, shortcut, text,
                              command)
            # tarjetas en las columnas pares y huecos fijos en las impares: anchos iguales
            card.grid(row=0, column=2 * i, sticky="nsew")
            self.cards_row.columnconfigure(2 * i, weight=1, uniform="tarjetas")
            if i:
                self.cards_row.columnconfigure(2 * i - 1, minsize=int(12 * k))
            self.cards.append(card)

        self.genres_title = tk.Label(self.body, text=GENRES_TITLE, font=f["strong"], anchor="w")
        self.genres_title.grid(row=3, column=0, sticky="w", pady=(int(26 * k), int(8 * k)))
        self.chips = ChipBar(self.body, app.anim, k, f["small"], self._pick, idle=0.06)
        self.chips.grid(row=4, column=0, sticky="w")
        self.footer = tk.Label(
            self.body, font=f["small"], justify="left", anchor="w",
            text=f"Las canciones se guardan en {config.MUSIC_DIR}. El tema, las transiciones y "
                 "más están en Ajustes (Ctrl+,).")
        self.footer.grid(row=5, column=0, sticky="ew", pady=(int(26 * k), 0))
        self._chip_count = 0
        self._fit_pending = False
        self.bind("<Configure>", self._resized)

    # ---------- contenido ----------
    def set_genres(self, names: list[str]) -> None:
        if list(names) != self._genres:
            self._genres = list(names)
            self._fit_genres()

    def set_progress(self, left: int) -> None:
        text = progress_text(left)
        if text is None:
            self.progress.grid_remove()
        else:
            self.progress_label.configure(text=text)
            self.progress.grid(row=1, column=0, sticky="w", pady=(int(12 * self.k), 0))
        self._refit()

    def _pick(self, key) -> None:
        if key:
            self.app._pick_yt_genre(key)

    # ---------- tamaño ----------
    def _resized(self, e) -> None:
        k = self.k
        right = int(16 * k)
        width = max(1, min(e.width - right, int(MAX_WIDTH * k)))
        self._refit()  # la altura también cuenta: en la ventana baja sobran la nota y los géneros
        if width == self._width:
            return
        self._width = width
        self.body.place(x=0, y=int(2 * k), width=width)
        for label in (self.intro, self.footer):
            label.configure(wraplength=width)
        self.progress_label.configure(
            wraplength=max(1, width - self.progress_link.winfo_reqwidth() - int(8 * k)))
        self._fit_genres()

    def _fit_genres(self) -> None:
        """Los géneros que quepan en una fila; sin ninguno (aún sin cargar), ni el título."""
        names = self._genres
        count = fit_count([self.chips.chip_width(n) for n in names], self._width or 1)
        self.chips.set_options([(n, n) for n in names[:count]])
        self._chip_count = count
        self._refit()

    def _refit(self) -> None:
        if not self._fit_pending:  # cuando las etiquetas ya tengan su altura con el ancho nuevo
            self._fit_pending = True
            self.after_idle(self._fit_height)

    def _fit_height(self) -> None:
        """Lo que no quepa en alto se queda fuera, de abajo arriba: primero la nota de dónde se
        guardan las canciones y luego los géneros (las tarjetas siempre se ven)."""
        self._fit_pending = False
        k = self.k
        room = self.winfo_height() - int(2 * k)
        if room <= 1:
            return
        footer_h = self.footer.winfo_reqheight() + int(26 * k)
        genres_h = self.genres_title.winfo_reqheight() + int(34 * k) + self.chips.h
        shown_footer = bool(self.footer.winfo_manager())
        shown_genres = bool(self.genres_title.winfo_manager())
        base = (self.body.winfo_reqheight() - (footer_h if shown_footer else 0)
                - (genres_h if shown_genres else 0))
        genres = bool(self._chip_count) and base + genres_h <= room
        footer = base + (genres_h if genres else 0) + footer_h <= room
        for widget, on in ((self.genres_title, genres), (self.chips, genres),
                           (self.footer, footer)):
            if on:
                widget.grid()
            else:
                widget.grid_remove()

    def restyle(self, p: dict) -> None:
        bg = p["bg"]
        for widget in (self, self.body, self.progress, self.cards_row):
            widget.configure(bg=bg)
        self.intro.configure(bg=bg, fg=p["dim"])
        self.progress_label.configure(bg=bg, fg=p["fg"])
        self.progress_link.restyle(bg, p["fg"], p["dim"])
        self.genres_title.configure(bg=bg, fg=p["fg"])
        self.footer.configure(bg=bg, fg=p["dim"])
        self.chips.restyle(bg, p["fg"], p["dim"])
        for card in self.cards:
            card.restyle(bg, p["fg"], p["dim"], p["faint"], p["accent"])


class WelcomeMixin:
    def _build_welcome(self, mid) -> None:
        self.welcome = Welcome(mid, self)
        self.welcome_on = False

    def _welcome_wanted(self) -> bool:
        return wanted(len(self.library), self.view_kind, self.query.get(), self.sub_view,
                      self.searching, bool(self.genre_filter or self.yt_sel))

    def _sync_welcome(self) -> bool:
        """Enseña o quita la bienvenida según la vista (desde `_update_empty`, al llegar los
        géneros de YouTube...). True si se ve."""
        on = self._welcome_wanted()
        if on:
            self.welcome.set_genres(self.yt_genres)
            self.welcome.set_progress(self.downloads_left)
            if not self.welcome_on:
                self.welcome.place(x=0, y=0, relwidth=1, relheight=1)
            self.welcome.lift()
        elif self.welcome_on:
            self.welcome.place_forget()
        self.welcome_on = on
        return on

    def _welcome_downloads(self, left: int) -> None:
        """Desde el contador de Descargas: cuántas de las primeras canciones quedan."""
        if self.welcome_on:
            self.welcome.set_progress(left)

    def _welcome_search(self) -> None:
        self._add_by_search()  # el buscador, con una pista en la barra de estado

    def _pick_yt_genre(self, name: str) -> None:
        """Abre un género de YouTube como si se hubiera pulsado en el lateral."""
        if "yt" in self._side_folded:  # plegada, el género no estaría en el lateral
            self._side_folded.discard("yt")
            self.save_prefs()
            self._build_sidebar()
        index = next((i for i, (kind, n, _label) in enumerate(self.side_items)
                      if kind == "yt" and n == name), None)
        if index is None:
            return
        self.genres.selection_clear()
        self.genres.selection_set(index)
        self.genres.see(index)
        self._pick_genre()
