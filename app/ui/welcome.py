"""Bienvenida: lo que se ve con la biblioteca vacía (la primera vez, o si se borra todo).

En Inicio y en Todo, en vez de una tabla vacía con sus columnas:
- portadas de canciones para escuchar ya (las de «Descubre en YouTube», ver discover.py);
- tres maneras de empezar: buscar, importar un enlace o pedir música con una frase;
- los géneros de YouTube del lateral, como atajo;
- mientras bajan las primeras canciones, cuántas quedan y un enlace a Descargas.

Se va sola en cuanto llega la primera canción (la vista se repinta y ya hay filas).
"""
from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from ..core import config
from .icons import icon_mask
from .theme import blend, seed_color
from .widgets import ChipBar, TextButton, _layer, _rounded_mask, elide

TITLE = "Te damos la bienvenida"  # corto: en la ventana mínima no se recorta
INTRO = ("Sound descarga música de YouTube Music (solo la versión original: sin mixes, covers ni "
         "directos), la ordena por géneros y la guarda en tu equipo, así que lo descargado suena "
         "también sin conexión.")
CARDS = (  # (icono, título, atajo, frase)
    ("search", "Buscar", "Ctrl+F",
     "Una canción, un artista o un álbum. Suena al momento y se descarga con un clic."),
    ("link", "Importar un enlace", "",
     "Pega un álbum, una playlist o un artista de YouTube Music y se descarga lo que falte."),
    ("note", "Música por frase", "Ctrl+G",
     "Escribe lo que te apetece, como «rock de los 90», y Sound busca las canciones."),
)
GENRES_TITLE = "O empieza por un género"
PICKS_TITLE = "Para empezar a escuchar"
MAX_WIDTH = 820  # más ancho, las tarjetas solo se estirarían
TILE_MIN, TILE_MAX, TILE_GAP = 120, 168, 14  # portadas de la bienvenida, a 96 ppp
MAX_TILES = 6


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


def tile_layout(width: int, k: float) -> tuple[int, int]:
    """(cuántas portadas, lado de cada una) para `width` píxeles: las que quepan sin bajar del
    mínimo (hasta seis) y sin pasar del máximo."""
    gap = int(TILE_GAP * k)
    count = max(1, min(MAX_TILES, (width + gap) // (int(TILE_MIN * k) + gap)))
    side = min(int(TILE_MAX * k), (width - gap * (count - 1)) // count)
    return count, max(1, side)


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


def _disc(d: int, fill: str, glyph: str, kind: str) -> Image.Image:
    """Botón redondo: ▶ («play»), ↓ («download») o ✓ («done»), sobremuestreado."""
    ss = 4
    img = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, d * ss - 1, d * ss - 1), fill=fill)
    c = d * ss / 2
    if kind == "play":
        r = d * ss * 0.2
        draw.polygon([(c - r * 0.7, c - r), (c - r * 0.7, c + r), (c + r, c)], fill=glyph)
    elif kind == "done":
        w = max(2, int(d * ss * 0.09))
        draw.line([(c - d * ss * 0.2, c), (c - d * ss * 0.05, c + d * ss * 0.15),
                   (c + d * ss * 0.22, c - d * ss * 0.15)], fill=glyph, width=w, joint="curve")
    small = img.resize((d, d), Image.LANCZOS)
    if kind == "download":
        mask = icon_mask("downloads", int(d * 0.62), 1.8)
        small.alpha_composite(_layer(mask, glyph), ((d - mask.width) // 2, (d - mask.height) // 2))
    return small


class SuggestionTile(tk.Canvas):
    """Portada de una canción sugerida: un clic (o Intro) la reproduce; al pasar el ratón salen
    ▶ y, arriba a la derecha, ↓ para descargarla."""

    def __init__(self, master, anim, k: float, fonts, on_play, on_download) -> None:
        super().__init__(master, highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.anim, self.k, self.fonts = anim, k, fonts
        self.on_play, self.on_download = on_play, on_download
        self.track: dict | None = None
        self.image: Image.Image | None = None
        self.queued = False
        self.side = int(TILE_MIN * k)
        self.h = 0.0
        self.focused = False
        self.over_dl = False
        self._scaled: tuple | None = None  # (lado, imagen) para no reescalar en cada fotograma
        self._photos: list = []
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555",
                      accent="#ffffff", on_accent="#000000")
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", self._leave)
        self.bind("<Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self._play())
        self.bind("<space>", lambda e: self._play())
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))

    def text_height(self) -> int:
        f = self.fonts
        return int(8 * self.k) + f["body"].metrics("linespace") + f["small"].metrics("linespace")

    def set_side(self, side: int) -> None:
        if side != self.side or int(self.cget("height")) != side + self.text_height():
            self.side = side
            self.configure(width=side, height=side + self.text_height())
            self._render()

    def set_track(self, track: dict | None, image: Image.Image | None, queued: bool) -> None:
        if track is self.track and image is self.image and queued == self.queued:
            return
        if track is not self.track or image is not self.image:
            self._scaled = None
        self.track, self.image, self.queued = track, image, queued
        self.configure(cursor="hand2" if track else "arrow")
        self._render()

    def restyle(self, **colors) -> None:
        self.c.update(colors)
        self.configure(bg=self.c["bg"])
        self._render()

    # ---------- ratón y teclado ----------
    def _dl_box(self) -> tuple[int, int, int]:
        d = int(28 * self.k)
        pad = int(8 * self.k)
        return self.side - pad - d, pad, d

    def _leave(self, _e=None) -> None:
        self.over_dl = False
        self._hover(0.0)

    def _motion(self, e) -> None:
        x, y, d = self._dl_box()
        over = self.track is not None and x <= e.x <= x + d and y <= e.y <= y + d
        if over != self.over_dl:
            self.over_dl = over
            self._render()

    def _release(self, e) -> None:
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        if self.track is None or not inside:
            return
        if self.over_dl:
            if not self.queued:
                self.on_download(self.track)
        else:
            self._play()

    def _play(self) -> None:
        if self.track is not None:
            self.on_play(self.track)

    def _hover(self, to: float) -> None:
        start = self.h

        def step(p: float) -> None:
            self.h = start + (to - start) * p
            self._render()

        self.anim.tween(("tile", id(self)), 120 if to else 180, step)

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._render()

    # ---------- pintar ----------
    def _cover(self) -> Image.Image:
        side, k, c = self.side, self.k, self.c
        if self.image is not None:
            if self._scaled is None or self._scaled[0] != side:
                self._scaled = (side, self.image.convert("RGB").resize((side, side), Image.LANCZOS))
            art = self._scaled[1].convert("RGBA")
        else:  # mientras llega la portada (o si no hay), un color propio y una nota
            seed = (self.track or {}).get("video_id") or ""
            base = blend(c["bg"], seed_color(seed), 0.35) if seed else blend(c["bg"], c["fg"], 0.07)
            art = Image.new("RGBA", (side, side), base)
            note = icon_mask("note", int(side * 0.22))
            art.alpha_composite(_layer(note, blend(base, c["fg"], 0.35)),
                                ((side - note.width) // 2, (side - note.height) // 2))
        lift = max(self.h, 0.6 if self.focused else 0.0)
        if lift and self.track is not None:
            art.alpha_composite(Image.new("RGBA", (side, side), (0, 0, 0, int(70 * lift))))
            d = int(36 * k)
            pad = int(10 * k)
            play = _disc(d, c["accent"], c["on_accent"], "play")
            play.putalpha(play.getchannel("A").point(lambda a: int(a * lift)))
            art.alpha_composite(play, (side - pad - d, side - pad - d))
            x, y, dd = self._dl_box()
            fill = c["accent"] if self.over_dl else "#202020"
            glyph = c["on_accent"] if self.over_dl else "#ffffff"
            dl = _disc(dd, fill, glyph, "done" if self.queued else "download")
            dl.putalpha(dl.getchannel("A").point(lambda a: int(a * lift * 0.92)))
            art.alpha_composite(dl, (x, y))
        out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        out.paste(art, (0, 0), _rounded_mask(side, side, int(8 * k)))
        return out

    def _render(self) -> None:
        self.delete("all")
        k, f, c = self.k, self.fonts, self.c
        side = self.side
        self._photos = [ImageTk.PhotoImage(self._cover(), master=self)]
        self.create_image(0, 0, anchor="nw", image=self._photos[0])
        y = side + int(8 * k)
        if self.track is None:  # cargando: dos barras donde irán el título y el artista
            bar = blend(c["bg"], c["fg"], 0.08)
            for width, top in ((0.8, y + int(4 * k)),
                               (0.5, y + f["body"].metrics("linespace") + int(3 * k))):
                self.create_rectangle(0, top, int(side * width), top + int(9 * k), fill=bar,
                                      outline="")
            return
        self.create_text(0, y, anchor="nw", font=f["body"], fill=c["fg"],
                         text=elide(f["body"], self.track.get("title") or "", side))
        y += f["body"].metrics("linespace")
        note = "En descarga" if self.queued else (self.track.get("artist") or "")
        self.create_text(0, y, anchor="nw", font=f["small"], fill=c["dim"],
                         text=elide(f["small"], note, side))


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

        # portadas para escuchar ya (las de Descubre): solo si hay o están llegando
        self.picks_head = tk.Frame(self.body)
        self.picks_head.columnconfigure(0, weight=1)
        self.picks_title = tk.Label(self.picks_head, text=PICKS_TITLE, font=f["strong"],
                                    anchor="w")
        self.picks_title.grid(row=0, column=0, sticky="w")
        self.picks_more = TextButton(self.picks_head, app.anim, "Ver otras", f["small"],
                                     self._more_picks)
        self.picks_more.grid(row=0, column=1, sticky="e")
        self.picks_row = tk.Frame(self.body)
        self.tiles: list[SuggestionTile] = []
        self._picks: list[dict] = []
        self._picks_loading = False
        self._picks_offset = 0
        self._covers: dict[str, Image.Image] = {}
        self._queued: set[str] = set()
        self._cover_retry = None
        self._colors: dict = {}

        self.cards_row = tk.Frame(self.body)
        self.cards_row.grid(row=4, column=0, sticky="ew", pady=(int(22 * k), 0))
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
        self.genres_title.grid(row=5, column=0, sticky="w", pady=(int(26 * k), int(8 * k)))
        self.chips = ChipBar(self.body, app.anim, k, f["small"], self._pick, idle=0.06)
        self.chips.grid(row=6, column=0, sticky="w")
        self.footer = tk.Label(
            self.body, font=f["small"], justify="left", anchor="w",
            text=f"Las canciones se guardan en {config.MUSIC_DIR}. El tema, las transiciones y "
                 "más están en Ajustes (Ctrl+,).")
        self.footer.grid(row=7, column=0, sticky="ew", pady=(int(26 * k), 0))
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

    # ---------- portadas para escuchar ya ----------
    def set_picks(self, tracks: list[dict], loading: bool) -> None:
        if [t["video_id"] for t in tracks] != [t["video_id"] for t in self._picks]:
            self._picks_offset = 0
        self._picks, self._picks_loading = list(tracks), loading
        show = bool(tracks) or loading
        for widget, row, pady in ((self.picks_head, 2, (int(22 * self.k), int(10 * self.k))),
                                  (self.picks_row, 3, (0, 0))):
            if show:
                widget.grid(row=row, column=0, sticky="ew", pady=pady)
            else:
                widget.grid_remove()
        self._layout_tiles()

    def set_cover(self, vid: str, image) -> None:
        if image is None or not any(t.track and t.track["video_id"] == vid for t in self.tiles):
            return
        self._covers[vid] = image
        self._layout_tiles()

    def mark_queued(self, vid: str) -> None:
        self._queued.add(vid)
        self._layout_tiles()

    def _more_picks(self) -> None:
        if self._picks:
            self._picks_offset = (self._picks_offset + len(self.tiles)) % len(self._picks)
            self._layout_tiles()

    def _layout_tiles(self) -> None:
        """Tantas portadas como quepan; se crean o quitan al cambiar el ancho."""
        if not self.picks_row.winfo_manager() or self._width < int(TILE_MIN * self.k):
            return  # aún sin medir: `_resized` vuelve a llamar con el ancho de verdad
        count, side = tile_layout(self._width or 1, self.k)
        if self._picks:
            count = min(count, len(self._picks))
        gap = int(TILE_GAP * self.k)
        while len(self.tiles) < count:
            tile = SuggestionTile(self.picks_row, self.app.anim, self.k, self.app.fonts,
                                  self.app._play_suggestion, self.app._download_suggestion)
            if self._colors:
                tile.restyle(**self._colors)
            self.tiles.append(tile)
        while len(self.tiles) > count:
            self.tiles.pop().destroy()
        missing = []
        for i, tile in enumerate(self.tiles):
            tile.grid(row=0, column=i, padx=(0 if i == 0 else gap, 0), sticky="nw")
            tile.set_side(side)
            track = (self._picks[(self._picks_offset + i) % len(self._picks)]
                     if self._picks else None)
            vid = track["video_id"] if track else None
            tile.set_track(track, self._covers.get(vid), vid in self._queued)
            if track is not None and vid not in self._covers:
                missing.append(track)
        if len(self._picks) > len(self.tiles):
            self.picks_more.grid()
        else:
            self.picks_more.grid_remove()
        if missing:
            self._ask_covers(missing)
        self._refit()

    def _ask_covers(self, tracks: list[dict]) -> None:
        """Pide las portadas que faltan; si el cupo de portadas a la vez está lleno, vuelve a
        intentarlo en un momento."""
        refused = [t for t in tracks if not self.app._request_thumb(t)]
        if refused and self._cover_retry is None:
            def retry() -> None:
                self._cover_retry = None
                self._layout_tiles()
            self._cover_retry = self.after(700, retry)

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
        self._layout_tiles()

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
        """Lo que no quepa en alto se queda fuera, de abajo arriba: la nota de dónde se guardan
        las canciones, luego los géneros y, si hay portadas para escuchar, las tarjetas (están
        también en «＋ Añadir música»). Sin portadas, las tarjetas siempre se ven."""
        self._fit_pending = False
        k = self.k
        room = self.winfo_height() - int(2 * k)
        if room <= 1:
            return
        picks = bool(self.picks_row.winfo_manager())
        parts = [  # (piezas, alto, ¿puede ir?) en orden de prioridad
            ((self.cards_row,), self.cards_row.winfo_reqheight() + int(22 * k), True),
            ((self.genres_title, self.chips),
             self.genres_title.winfo_reqheight() + int(34 * k) + self.chips.h,
             bool(self._chip_count)),
            ((self.footer,), self.footer.winfo_reqheight() + int(26 * k), True),
        ]
        base = self.body.winfo_reqheight() - sum(
            h for widgets, h, _ok in parts if widgets[0].winfo_manager())
        used, fits = base, True
        for i, (widgets, h, ok) in enumerate(parts):
            must = i == 0 and not picks  # sin portadas, las tarjetas no se quitan
            # en cuanto una no cabe, las de debajo tampoco: no saldría la nota sin los géneros
            on = ok and (must or (fits and used + h <= room))
            if ok and not on:
                fits = False
            if on:
                used += h
            for widget in widgets:
                if on:
                    widget.grid()
                else:
                    widget.grid_remove()

    def restyle(self, p: dict) -> None:
        bg = p["bg"]
        for widget in (self, self.body, self.progress, self.cards_row, self.picks_head,
                       self.picks_row):
            widget.configure(bg=bg)
        self.picks_title.configure(bg=bg, fg=p["fg"])
        self.picks_more.restyle(bg, p["fg"], p["dim"])
        self._colors = dict(bg=bg, fg=p["fg"], dim=p["dim"], faint=p["faint"],
                            accent=p["accent"], on_accent=p["accent_fg"])
        for tile in self.tiles:
            tile.restyle(**self._colors)
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
            self.welcome.set_picks(self._discover_rows(),
                                   loading=self.discover_on and self.discover_busy)
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

    def _welcome_cover(self, vid: str, image) -> None:
        """Desde la cola de eventos: llegó una portada (puede ser de la bienvenida)."""
        if self.welcome_on:
            self.welcome.set_cover(vid, image)

    def _play_suggestion(self, track: dict) -> None:
        """Una portada de la bienvenida: suena ya y siguen las demás sugerencias."""
        rows = self._discover_rows()
        ids = [t["video_id"] for t in rows]
        if track["video_id"] in ids:
            self.play_tracks(rows, ids.index(track["video_id"]), label="Descubre en YouTube")

    def _download_suggestion(self, track: dict) -> None:
        self.download_tracks([track])
        self.welcome.mark_queued(track["video_id"])

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
