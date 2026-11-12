"""Pantalla «Reproduciendo»: la canción que suena en grande, con su álbum, su letra y la cola.

Cubre la ventana menos el reproductor (la onda y los controles siguen abajo) y sube desde él.
El fondo es la portada muy desenfocada, oscurecida en el tema oscuro y aclarada en el claro, que
abajo se funde con el color de la ventana. A la izquierda van la portada grande, el título, el
artista y el álbum; a la derecha, tres pestañas: las canciones de ese álbum que tienes, la letra
sincronizada y lo que sigue en la cola.

Todo se dibuja en un único Canvas para que el texto quede directamente sobre el fondo, sin cajas.
El panel de la derecha se desplaza moviendo sus elementos; arriba y abajo lo tapan dos tiras del
propio fondo, con un degradado, que hacen de recorte.

La ventana le pasa los datos (`set_track`, `set_art`, `set_album`, `set_queue`, `set_lyrics`) y
llama a `sync` en cada tick: la vista no lee por su cuenta el estado de la reproducción.
"""
import math
import time
import tkinter as tk
import tkinter.font as tkfont
from functools import lru_cache

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageTk

from ..core.lyrics import line_at
from .anim import ease_in_out, ease_out
from .icons import icon_mask
from .playback import fmt
from .theme import blend, readable_on, seed_color, vivid
from .widgets import HEART, PointerTip, _layer, _Painted, bind_touchpad, elide, touchpad_deltas

TABS = (("album", "Álbum"), ("lyrics", "Letra"), ("queue", "A continuación"))
SLIDE_MS = 300
ART_MS = 460
FRAME_S = 0.045        # los fundidos de imágenes grandes se pintan, como mucho, cada 45 ms
FOLLOW_PAUSE_S = 4.0   # tras mover la letra a mano, deja de seguir la canción un rato
QUEUE_LIMIT = 60       # filas de la cola que se dibujan; el resto se resume


# ---------- cálculos sin Tk ----------
def wrap_two(font, text: str, width: int) -> list[str]:
    """Hasta dos líneas cortando por palabras; la segunda se recorta con … si no cabe."""
    if width <= 0:
        return [""]
    if font.measure(text) <= width:
        return [text]
    words = text.split()
    line = ""
    for i, word in enumerate(words):
        trial = f"{line} {word}".strip()
        if line and font.measure(trial) > width:
            return [elide(font, line, width), elide(font, " ".join(words[i:]), width)]
        line = trial
    return [elide(font, text, width)]  # una sola palabra larguísima


def layout(w: int, h: int, k: float, text_h: int, tab_h: int) -> dict:
    """Portada a la izquierda y panel a la derecha, centrados como un solo grupo.

    `text_h` es lo que ocupa el texto bajo la portada y `tab_h`, la fila de pestañas. La
    portada crece con la ventana hasta 720 px (a 96 ppp) sin pasar del 46 % del ancho.
    """
    pad = int(36 * k)
    top = int(58 * k)      # la fila de «Cerrar» y «Pantalla completa»
    bottom = int(28 * k)
    gap = int(56 * k)
    avail = max(int(160 * k), h - top - bottom)
    size = min(avail - text_h, int((w - 2 * pad - gap) * 0.48), int(720 * k))
    size = max(int(120 * k), size)
    panel = max(int(220 * k), min(int(560 * k), w - 2 * pad - gap - size))
    x0 = max(pad, (w - (size + gap + panel)) // 2)
    block = size + text_h
    y0 = top + max(0, (avail - block) // 2)
    px0 = x0 + size + gap
    return dict(pad=pad, top=top, gap=gap, size=size, x0=x0, y0=y0, text_y=y0 + size,
                panel_x0=px0, panel_x1=px0 + panel, list_top=y0 + tab_h,
                list_bottom=y0 + block)


def _percentile(gray: Image.Image, q: float) -> int:
    hist = gray.histogram()
    total, acc = sum(hist), 0
    for value, count in enumerate(hist):
        acc += count
        if acc >= q * total:
            return value
    return 255


def tone_backdrop(img, seed: str | None, size: tuple[int, int], dark: bool,
                  base: str) -> Image.Image:
    """El fondo en pequeño (se amplía después): portada desenfocada, en el tono del tema.

    En el tema oscuro se oscurece hasta que el texto claro se lea en cualquier zona; en el
    claro se aclara para el texto oscuro. Abajo se funde con `base`, el fondo de la ventana,
    para que la onda del reproductor quede sin costura.
    """
    w, h = max(1, size[0]), max(1, size[1])
    sw = 72
    sh = max(1, round(sw * h / w))
    if img is not None:
        small = ImageOps.fit(img.convert("RGB"), (sw, sh), Image.BILINEAR)
    else:  # sin portada: el degradado propio de la canción
        color = seed_color(seed) if seed else blend(base, "#808080", 0.3)
        ramp = Image.linear_gradient("L").rotate(35).crop((48, 48, 208, 208)).resize((sw, sh))
        small = ImageOps.colorize(ramp, blend(color, "#000000", 0.4), blend(color, "#ffffff", 0.1))
    small = small.filter(ImageFilter.GaussianBlur(4))
    small = ImageEnhance.Color(small).enhance(1.25)
    solid = Image.new("RGB", small.size, base)
    upper = (0, 0, sw, max(1, int(sh * 0.7)))  # donde va el texto; abajo se funde
    if dark:
        small = Image.blend(ImageEnhance.Brightness(small).enhance(0.5), solid, 0.35)
        high = _percentile(small.crop(upper).convert("L"), 0.95)
        if high > 88:
            small = ImageEnhance.Brightness(small).enhance(88 / high)
    else:
        small = Image.blend(small, solid, 0.72)
        low = _percentile(small.crop(upper).convert("L"), 0.05)
        if low < 182:
            white = Image.new("RGB", small.size, "#ffffff")
            small = Image.blend(small, white, (182 - low) / max(1, 255 - low))
    fade = Image.linear_gradient("L").resize((sw, sh))
    fade = fade.point(lambda v: 0 if v < 150 else min(255, int((v - 150) / 105 * 250)))
    return Image.composite(solid, small, fade)


def _hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def scene_colors(small: Image.Image, pal: dict, dark: bool, color: str | None) -> dict:
    """Colores del texto sobre el fondo ya entonado: todos con el contraste WCAG que piden."""
    sw, sh = small.size
    avg = _hex_of(small.crop((0, 0, sw, max(1, int(sh * 0.62)))).resize((1, 1), Image.BOX)
                  .getpixel((0, 0)))
    top = _hex_of(small.crop((0, 0, sw, max(1, sh // 12))).resize((1, 1), Image.BOX)
                  .getpixel((0, 0)))
    fg = readable_on(pal["fg"], avg, 7.0)
    return dict(bg=avg, top=top, fg=fg, dim=readable_on(pal["dim"], avg, 4.5),
                faint=readable_on(blend(pal["dim"], avg, 0.35), avg, 3.0),
                accent=readable_on(vivid(color), avg, 4.5) if color else fg,
                hover="#ffffff" if dark else "#000000")


@lru_cache(maxsize=4)
def _round_mask(size: int, r: int) -> Image.Image:
    m = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size * 2 - 1, size * 2 - 1), radius=r * 2,
                                        fill=255)
    return m.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=4)
def _shadow_mask(size: int, r: int, blur: int) -> Image.Image:
    pad = blur * 2
    m = Image.new("L", (size + 2 * pad, size + 2 * pad), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + size - 1, pad + size - 1), radius=r,
                                        fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur))


def artwork(img, seed: str | None, size: int, dark: bool, max_blur: int) -> tuple[Image.Image, int]:
    """Portada con esquinas redondeadas y una sombra suave. Devuelve la imagen y su margen."""
    r = max(6, round(size * 0.024))
    blur = max(4, min(round(size * 0.045), max_blur))
    pad = blur * 2
    drop = round(blur * 0.6)
    out = Image.new("RGBA", (size + 2 * pad, size + 2 * pad + drop), (0, 0, 0, 0))
    shadow = _shadow_mask(size, r, blur)
    out.alpha_composite(_layer(shadow.point(lambda v: round(v * (0.6 if dark else 0.28))),
                               "#000000"), (0, drop))
    if img is not None:
        cover = ImageOps.fit(img.convert("RGB"), (size, size), Image.LANCZOS)
    else:
        base = seed_color(seed) if seed else "#5a5a62"
        ramp = Image.linear_gradient("L").rotate(35).crop((48, 48, 208, 208)).resize((size, size))
        cover = ImageOps.colorize(ramp, blend(base, "#000000", 0.35), blend(base, "#ffffff", 0.18))
    cover = cover.convert("RGBA")
    cover.putalpha(_round_mask(size, r))
    out.alpha_composite(cover, (pad, pad))
    return out, pad


@lru_cache(maxsize=8)
def _fade_alpha(w: int, h: int, fade: int, top: bool) -> Image.Image:
    """Máscara de las tiras que recortan el panel: opaca, con un degradado hacia la lista."""
    column = [255] * h
    for i in range(min(fade, h)):
        value = round(255 * (i + 1) / (fade + 1))
        column[h - 1 - i if top else i] = value
    return Image.frombytes("L", (1, h), bytes(column)).resize((max(1, w), h))


@lru_cache(maxsize=16)
def _hover_pill(w: int, h: int, color: str, r: int) -> Image.Image:
    """Resalte translúcido de la fila bajo el puntero: deja ver el fondo."""
    mask = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), radius=r * 2, fill=255)
    return _layer(mask.resize((w, h), Image.LANCZOS).point(lambda v: round(v * 0.09)), color)


def _bars(size: int, color: str, now: float | None, pump: float) -> Image.Image:
    """Las tres barras de «sonando», como en la lista; quietas si está en pausa."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bw = s / 5
    for i in range(3):
        level = (0.15 + (0.35 + 0.45 * pump) * (0.5 + 0.5 * math.sin(
            now * (6.3 + i * 1.7) + i * 2.1))) if now is not None else (0.45, 0.85, 0.6)[i]
        x = bw * 0.5 + i * bw * 1.5
        d.rounded_rectangle((x, s * (0.92 - 0.8 * level), x + bw, s * 0.92), radius=bw / 2,
                            fill=color)
    return img.resize((size, size), Image.LANCZOS)


class NowView(tk.Canvas):
    def __init__(self, app, actions: dict) -> None:
        super().__init__(app, width=1, height=1, highlightthickness=0, bd=0, takefocus=1)
        self.app, self.anim, self.k = app, app.anim, app.k
        self.on = actions  # close, fullscreen, like, artist, album, play_album, queue, seek, tab
        k = self.k
        families = set(tkfont.families(self))

        def pick(*names: str) -> str:
            return next((n for n in names if n in families), "Segoe UI")

        display = pick("Segoe UI Variable Display Semibold", "Segoe UI Semibold")
        text = pick("Segoe UI Variable Text", "Segoe UI")
        strong = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
        F = tkfont.Font
        self.f = dict(
            title=F(self, family=display, size=24),
            artist=F(self, family=text, size=13), artist_u=F(self, family=text, size=13,
                                                              underline=True),
            album=F(self, family=text, size=10), album_u=F(self, family=text, size=10,
                                                           underline=True),
            head=F(self, family=strong, size=9), context=F(self, family=text, size=9),
            tab=F(self, family=strong, size=11), hdr=F(self, family=strong, size=12),
            row=F(self, family=text, size=10), sub=F(self, family=text, size=9),
            lyric=F(self, family=display, size=17), msg=F(self, family=text, size=11),
        )

        # estado que llega de la ventana
        self.visible = False
        self.fullscreen = False
        self.shown = 0.0
        self.tab = "album"
        self.track: dict | None = None
        self.liked: bool | None = None
        self.context = ""
        self.artist_link = self.album_link = False
        self.art_src: tuple = (None, None)
        self.color: str | None = None
        self.album_rows: list[dict] = []
        self.album_info: dict | None = None
        self.album_note = ""
        self.album_action: tuple | None = None
        self.current_vid: str | None = None
        self.queue_entries: list[dict] = []
        self.lyric_lines: list[tuple[int | None, str]] = []
        self.synced = False
        self.lyrics_msg = ""
        self.lyrics_action: tuple | None = None
        self.playing = False
        self.position = 0

        # dibujo
        self.L: dict | None = None
        self.c = dict(bg="#101013", top="#101013", fg="#eeeef2", dim="#92929c",
                      faint="#7b7b86", accent="#ffffff", hover="#ffffff")
        self._size = (0, 0)
        self._relayout_job = None
        self._shown_frames: tuple | None = None  # (fondo, portada) que se ven ahora
        self._art_pad = 0
        self._zones: list[dict] = []
        self._rows: list[dict] = []
        self._hot: str | None = None
        self._hover_row: dict | None = None
        self._pointer: tuple[int, int] | None = None
        self.offset = 0.0
        self.content_h = 0
        self._lyric_index = -1
        self._follow_after = 0.0
        self._heart_pop = 0.0
        self._title_cut = False
        self._bar_h = 0
        self._bars_photo = None
        self._bars_item = None
        self._bars_last = 0.0
        self._bars_idle = False
        self.heart_size, self.heart_box = int(24 * k), int(38 * k)
        self._heart_masks = {name: icon_mask(name, self.heart_size)
                             for name in ("heart", "heart_fill")}
        self._icon_d = int(36 * k)
        self._icon_masks = {name: icon_mask(name, int(20 * k))
                            for name in ("chevron_down", "expand", "shrink")}

        self._bg = _Painted(self)
        self._art = _Painted(self)
        self._hover = _Painted(self)
        self.itemconfigure(self._hover.item, state="hidden")
        self._mask_top = _Painted(self)
        self._mask_bottom = _Painted(self)
        for painted in (self._mask_top, self._mask_bottom):
            self.itemconfigure(painted.item, tags=("mask",))
        self._title1 = self.create_text(0, 0, anchor="nw", font=self.f["title"], tags=("left",))
        self._title2 = self.create_text(0, 0, anchor="nw", font=self.f["title"], tags=("left",))
        self._artist = self.create_text(0, 0, anchor="nw", font=self.f["artist"], tags=("left",))
        self._album = self.create_text(0, 0, anchor="nw", font=self.f["album"], tags=("left",))
        self._heart = _Painted(self, anchor="center")
        self._close = _Painted(self, anchor="center")
        self._full = _Painted(self, anchor="center")
        self._head = self.create_text(0, 0, anchor="w", font=self.f["head"], text="REPRODUCIENDO",
                                      tags=("chrome",))
        self._ctx = self.create_text(0, 0, anchor="w", font=self.f["context"], tags=("chrome",))
        for painted in (self._close, self._full):
            self.itemconfigure(painted.item, tags=("chrome",))
        self._tabs = {key: self.create_text(0, 0, anchor="nw", font=self.f["tab"], text=label,
                                            tags=("chrome",)) for key, label in TABS}
        self._tab_line = self.create_rectangle(0, 0, 0, 0, width=0, tags=("chrome",))
        self.tip = PointerTip(self, self.anim, lambda: (blend(self.c["bg"], self.c["fg"], 0.16),
                                                        self.c["fg"]))

        self.bind("<Configure>", self._resized)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<MouseWheel>", self._wheel)
        bind_touchpad(self, self._touchpad)
        app.scale.bind("<Configure>", self._bar_moved, add="+")
        self.anim.add_loop(self._frame)

    # ---------- abrir y cerrar ----------
    def open(self) -> None:
        if self.visible:
            return
        self.visible = True
        app = self.app
        self._bar_h = max(0, app.winfo_height() - app.scale.winfo_y())
        height = max(1, app.winfo_height() - self._bar_h)
        self.place(in_=app, x=0, y=round((1 - self.shown) * height), relwidth=1.0,
                   relheight=1.0, height=-self._bar_h)
        # bajo la onda y el reproductor: sube desde detrás de ellos
        self.tk.call("lower", self._w, app.scale._w)
        app.toast.restack()
        self.update_idletasks()
        self._relayout()
        self._slide(1.0)
        self.focus_set()

    def close(self) -> None:
        if not self.visible:
            return
        self.visible = False
        self.tip.hide()
        self._set_hot(None)
        self._set_hover_row(None)
        self._slide(0.0)

    def _slide(self, to: float) -> None:
        start = self.shown

        def step(p: float) -> None:
            self.shown = start + (to - start) * p
            self.place_configure(y=round((1 - self.shown) * max(1, self.winfo_height())))

        def done() -> None:
            if to <= 0 and not self.visible:
                self.place_forget()

        self.anim.tween(("now-slide", id(self)), SLIDE_MS, step,
                        ease=ease_out if to > 0 else ease_in_out, done=done)

    def _bar_moved(self, _e=None) -> None:
        """Cambió la altura del reproductor (otra escala o tipografía): se ajusta el hueco."""
        if not self.visible:
            return
        bar_h = max(0, self.app.winfo_height() - self.app.scale.winfo_y())
        if bar_h != self._bar_h:
            self._bar_h = bar_h
            self.place_configure(height=-bar_h)

    def set_fullscreen(self, on: bool) -> None:
        self.fullscreen = on
        if self.L is not None:
            self._paint_icon("full")

    # ---------- datos que llegan de la ventana ----------
    def set_track(self, track: dict | None, liked: bool | None, context: str,
                  artist_link: bool, album_link: bool) -> None:
        self.track, self.liked, self.context = track, liked, context
        self.artist_link, self.album_link = artist_link, album_link
        if self._ready():
            self._place_chrome()
            self._place_text()

    def set_links(self, liked: bool | None, artist_link: bool, album_link: bool,
                  pop: bool = False) -> None:
        changed = (liked is None) != (self.liked is None)
        self.liked, self.artist_link, self.album_link = liked, artist_link, album_link
        if not self._ready():
            return
        if changed:
            self._place_text()  # el título deja o recupera el hueco del corazón
        else:
            self._text_zones()
        if pop and liked:
            self.anim.tween(("now-pop", id(self)), 360, self._set_pop,
                            done=lambda: self._set_pop(0.0))
        else:
            self._paint_heart()

    def set_art(self, img, seed: str | None, color: str | None) -> None:
        if img is self.art_src[0] and seed == self.art_src[1] and color == self.color:
            return
        self.art_src, self.color = (img, seed), color
        if self._ready():
            self._paint_scene(animate=True)

    def set_album(self, rows: list[dict], info: dict | None, note: str = "",
                  action: tuple | None = None, current: str | None = None) -> None:
        # otra canción u otro álbum: arriba del todo; si solo llegó una pista más, se queda
        reset = current != self.current_vid or (info or {}).get("title") != (
            self.album_info or {}).get("title")
        self.album_rows, self.album_info = rows, info
        self.album_note, self.album_action, self.current_vid = note, action, current
        if self._ready() and self.tab == "album":
            self._build_panel(reset=reset)

    def set_queue(self, entries: list[dict]) -> None:
        self.queue_entries = entries
        if self._ready() and self.tab == "queue":
            self._build_panel(reset=False)

    def set_lyrics(self, payload: dict | None, message: str = "",
                   action: tuple | None = None) -> None:
        lines: list[tuple[int | None, str]] = []
        synced = False
        if payload and not payload.get("negative") and not message:
            try:
                lines = [(int(ms), str(text)) for ms, text in payload.get("synced") or []]
                synced = bool(lines)
            except (TypeError, ValueError):
                lines = []
            if not lines:
                plain = (payload.get("plain") or "").strip()
                lines = [(None, line) for line in plain.splitlines()]
        if not lines and not message:
            message = "Sin letra para esta canción."
        self.lyric_lines, self.synced = lines, synced
        self.lyrics_msg, self.lyrics_action = message, action
        self._lyric_index = -1
        self._follow_after = 0.0
        if self._ready() and self.tab == "lyrics":
            self._build_panel(reset=True)

    def sync(self, position_ms: int, playing: bool) -> None:
        """En cada tick: la línea de la letra y si las barras se mueven o no."""
        self.position = position_ms
        if playing != self.playing:
            self.playing = playing
            self._bars_idle = False
            self.anim.wake()
        if self._ready() and self.tab == "lyrics":
            self._sync_lyric()

    def restyle(self) -> None:
        if self._ready():
            self._paint_scene(animate=False)

    def show_tab(self, tab: str) -> None:
        if tab not in dict(TABS) or tab == self.tab:
            return
        self.tab = tab
        if self._ready():
            self._place_tabs(animate=True)
            self._build_panel(reset=True)
        self.on["tab"](tab)

    # ---------- composición ----------
    def _ready(self) -> bool:
        return self.visible and self.L is not None

    def _resized(self, e) -> None:
        if not self.visible or (e.width, e.height) == self._size:
            return
        if self._relayout_job is not None:
            self.after_cancel(self._relayout_job)
        self._relayout_job = self.after(60, self._relayout)

    def _fit_fonts(self, h: int) -> None:
        k = self.k
        title = max(17, min(32, round(h / (28 * k))))
        f = self.f
        f["title"].configure(size=title)
        for name in ("artist", "artist_u"):
            f[name].configure(size=max(11, min(17, round(title * 0.55))))
        for name in ("album", "album_u"):
            f[name].configure(size=max(10, min(13, round(title * 0.43))))
        f["lyric"].configure(size=max(14, min(24, round(title * 0.72))))
        row = 10 if h < 820 * k else 11
        f["row"].configure(size=row)
        f["sub"].configure(size=row - 1)

    def _text_height(self) -> int:
        k, ls = self.k, lambda name: self.f[name].metrics("linespace")
        # se reservan dos líneas de título: la portada no cambia de tamaño entre canciones
        return int(22 * k) + 2 * ls("title") + int(6 * k) + ls("artist") + int(4 * k) + ls("album")

    def _relayout(self) -> None:
        self._relayout_job = None
        w, h = self.winfo_width(), self.winfo_height()
        if not self.visible or w < 100 or h < 100:
            return
        self._size = (w, h)
        self._fit_fonts(h)
        tab_h = self.f["tab"].metrics("linespace") + int(26 * self.k)
        self.L = layout(w, h, self.k, self._text_height(), tab_h)
        self._paint_scene(animate=False)

    def _paint_scene(self, animate: bool) -> None:
        """Fondo, portada y colores; con `animate`, la portada anterior se funde en la nueva."""
        L, k = self.L, self.k
        w, h = self._size
        img, seed = self.art_src
        dark = self.app.dark
        small = tone_backdrop(img, seed, (w, h), dark, self.app.pal["bg"])
        self.c = scene_colors(small, self.app.pal, dark, self.color)
        self.configure(bg=self.c["bg"])
        frame = small.resize((w, h), Image.BICUBIC)
        # la sombra no debe llegar al panel: las tiras que lo recortan no la llevan
        art, pad = artwork(img, seed, L["size"], dark, max(4, (L["gap"] - int(16 * k)) // 2))
        self._art_pad = pad
        before = self._shown_frames
        self._recolor()
        if (animate and before is not None and before[0].size == frame.size
                and before[1].size == art.size and not self.anim.reduced):
            last = [0.0]

            def step(p: float) -> None:
                now = time.monotonic()
                if p < 1 and now - last[0] < FRAME_S:
                    return
                last[0] = now
                self._show_frames(Image.blend(before[0], frame, p), Image.blend(before[1], art, p))

            self.anim.tween(("now-art", id(self)), ART_MS, step, ease=ease_in_out)
        else:
            self.anim.tween(("now-art", id(self)), 0, lambda p: None)  # corta un fundido a medias
            self._show_frames(frame, art)
        self.app.now_caption(self.c["top"])

    def _show_frames(self, bg: Image.Image, art: Image.Image) -> None:
        L = self.L
        self._shown_frames = (bg, art)
        self._bg.show(bg)
        self._art.show(art)
        self.coords(self._art.item, L["x0"] - self._art_pad, L["y0"] - self._art_pad)
        self._paint_masks(bg)

    def _paint_masks(self, bg: Image.Image) -> None:
        L, k = self.L, self.k
        w, h = bg.size
        x0 = max(0, L["panel_x0"] - int(16 * k))
        fade = int(18 * k)
        top_h = min(h, L["list_top"] + fade)
        strip = bg.crop((x0, 0, w, top_h)).convert("RGBA")
        strip.putalpha(_fade_alpha(w - x0, top_h, fade, True))
        self._mask_top.show(strip)
        self.coords(self._mask_top.item, x0, 0)
        y = max(0, L["list_bottom"] - fade)
        strip = bg.crop((x0, y, w, h)).convert("RGBA")
        strip.putalpha(_fade_alpha(w - x0, h - y, fade, False))
        self._mask_bottom.show(strip)
        self.coords(self._mask_bottom.item, x0, y)

    def _recolor(self) -> None:
        """Todo lo que no es imagen toma los colores del fondo nuevo."""
        self._place_chrome()
        self._place_text()
        self._place_tabs(animate=False)
        self._build_panel(reset=False)

    # ---------- arriba: cerrar, contexto y pantalla completa ----------
    def _place_chrome(self) -> None:
        L, k, c = self.L, self.k, self.c
        w = self._size[0]
        cy = L["top"] // 2
        x = int(30 * k)
        self._zones = [z for z in self._zones if z["group"] != "chrome"]
        d = self._icon_d
        self.coords(self._close.item, x, cy)
        self.coords(self._full.item, w - x, cy)
        self._zones.append(dict(group="chrome", key="close", box=(x - d // 2, cy - d // 2,
                                                                  x + d // 2, cy + d // 2),
                                action=self.on["close"], tip="Cerrar (Esc)"))
        self._zones.append(dict(group="chrome", key="full",
                                box=(w - x - d // 2, cy - d // 2, w - x + d // 2, cy + d // 2),
                                action=self.on["fullscreen"],
                                tip=lambda: "Salir de pantalla completa (F11)" if self.fullscreen
                                else "Pantalla completa (F11)"))
        self._paint_icon("close")
        self._paint_icon("full")
        hx = x + int(28 * k)
        self.coords(self._head, hx, cy)
        self.itemconfigure(self._head, fill=c["faint"])
        cx = hx + self.f["head"].measure("REPRODUCIENDO") + int(10 * k)
        room = w - x - d - int(16 * k) - cx
        self.coords(self._ctx, cx, cy)
        self.itemconfigure(self._ctx, fill=c["dim"],
                           text=elide(self.f["context"], self.context, room) if self.context
                           else "")

    def _paint_icon(self, key: str) -> None:
        c, d = self.c, self._icon_d
        hot = self._hot == key
        name = "chevron_down" if key == "close" else ("shrink" if self.fullscreen else "expand")
        img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        if hot:
            disc = Image.new("L", (d * 4, d * 4), 0)
            ImageDraw.Draw(disc).ellipse((4, 4, d * 4 - 4, d * 4 - 4), fill=255)
            disc = disc.resize((d, d), Image.LANCZOS).point(lambda v: round(v * 0.14))
            img.alpha_composite(_layer(disc, c["hover"]))
        mask = self._icon_masks[name]
        off = (d - mask.size[0]) // 2
        img.alpha_composite(_layer(mask, c["fg"] if hot else c["dim"]), (off, off))
        (self._close if key == "close" else self._full).show(img)

    # ---------- izquierda: título, artista y álbum ----------
    def _place_text(self) -> None:
        L, k, c, f = self.L, self.k, self.c, self.f
        x, size = L["x0"], L["size"]
        t = self.track
        y = L["text_y"] + int(22 * k)
        heart_room = self.heart_box + int(6 * k) if self.liked is not None else 0
        title = t["title"] if t else "Nada suena ahora mismo"
        lines = wrap_two(f["title"], title, size - heart_room)
        self._title_cut = "…" in "".join(lines) and "…" not in title
        ls = f["title"].metrics("linespace")
        self.coords(self._title1, x, y)
        self.coords(self._title2, x, y + ls)
        self.itemconfigure(self._title1, text=lines[0], fill=c["fg"])
        self.itemconfigure(self._title2, text=lines[1] if len(lines) > 1 else "", fill=c["fg"])
        self.coords(self._heart.item, x + size - self.heart_box // 2, y + ls // 2)
        self._paint_heart()
        y += len(lines) * ls + int(6 * k)
        artist = (t.get("artist") or "") if t else ""
        self.coords(self._artist, x, y)
        self.itemconfigure(self._artist, text=elide(f["artist"], artist, size))
        y += f["artist"].metrics("linespace") + int(4 * k)
        album = ((t.get("album") or "").strip() if t else "")
        year = t.get("year") if t else None
        line = " · ".join(str(v) for v in (album, year) if v)
        self.coords(self._album, x, y)
        self.itemconfigure(self._album, text=elide(f["album"], line, size))
        self._text_zones()

    def _text_zones(self) -> None:
        self._zones = [z for z in self._zones if z["group"] != "left"]
        t = self.track
        if t is not None and self._title_cut:
            box = self._box(self._title1, self._title2)
            if box:
                self._zones.append(dict(group="left", key="title", box=box, tip=t["title"]))
        if self.liked is not None:
            x, y = self.coords(self._heart.item)
            r = self.heart_box // 2
            self._zones.append(dict(group="left", key="heart", box=(x - r, y - r, x + r, y + r),
                                    action=self.on["like"],
                                    tip=lambda: "Quitar de Me gusta (Ctrl+L)" if self.liked
                                    else "Me gusta (Ctrl+L)"))
        if self.artist_link and self.itemcget(self._artist, "text"):
            self._zones.append(dict(group="left", key="artist", box=self._box(self._artist),
                                    action=self.on["artist"], tip="Ver el artista"))
        if self.album_link and self.itemcget(self._album, "text"):
            self._zones.append(dict(group="left", key="album", box=self._box(self._album),
                                    action=self.on["album"], tip="Abrir el álbum"))
        self._paint_links()

    def _box(self, *items) -> tuple | None:
        boxes = [self.bbox(i) for i in items if self.itemcget(i, "text")]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _paint_links(self) -> None:
        c, f = self.c, self.f
        for item, key, normal, under, rest in (
                (self._artist, "artist", f["artist"], f["artist_u"], c["dim"]),
                (self._album, "album", f["album"], f["album_u"], c["faint"])):
            hot = self._hot == key
            self.itemconfigure(item, font=under if hot else normal, fill=c["fg"] if hot else rest)

    def _paint_heart(self) -> None:
        if self.liked is None:
            self.itemconfigure(self._heart.item, state="hidden")
            return
        c, box = self.c, self.heart_box
        size = max(4, round(self.heart_size * (1 + 0.3 * math.sin(math.pi * self._heart_pop))))
        mask = self._heart_masks["heart_fill" if self.liked else "heart"]
        if size != self.heart_size:
            mask = mask.resize((size, size), Image.LANCZOS)
        if self.liked:
            color = readable_on(HEART, c["bg"], 3.0)
        else:
            color = c["fg"] if self._hot == "heart" else c["dim"]
        img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        img.alpha_composite(_layer(mask, color), ((box - size) // 2, (box - size) // 2))
        self._heart.show(img)
        self.itemconfigure(self._heart.item, state="normal")

    def _set_pop(self, p: float) -> None:
        self._heart_pop = p
        self._paint_heart()

    # ---------- derecha: pestañas ----------
    def _place_tabs(self, animate: bool) -> None:
        L, k, c, f = self.L, self.k, self.c, self.f
        self._zones = [z for z in self._zones if z["group"] != "tabs"]
        x, y = L["panel_x0"], L["y0"]
        ls = f["tab"].metrics("linespace")
        target = None
        for key, label in TABS:
            item = self._tabs[key]
            width = f["tab"].measure(label)
            self.coords(item, x, y)
            active = key == self.tab
            hot = self._hot == "tab:" + key
            self.itemconfigure(item, fill=c["fg"] if active or hot else c["dim"])
            if active:
                target = (x, x + width)
            self._zones.append(dict(group="tabs", key="tab:" + key,
                                    box=(x - int(6 * k), y - int(6 * k), x + width + int(6 * k),
                                         y + ls + int(10 * k)),
                                    action=lambda key=key: self.show_tab(key)))
            x += width + int(26 * k)
        self.itemconfigure(self._tab_line, fill=c["accent"])
        line_y = y + ls + int(6 * k)
        thick = max(2, int(2 * k))
        if target is None:
            return
        old = self.coords(self._tab_line)
        if not animate or not old or old[2] <= old[0]:
            self.coords(self._tab_line, target[0], line_y, target[1], line_y + thick)
            return
        a0, a1 = old[0], old[2]

        def step(p: float) -> None:
            self.coords(self._tab_line, a0 + (target[0] - a0) * p, line_y,
                        a1 + (target[1] - a1) * p, line_y + thick)

        self.anim.tween(("now-tab", id(self)), 220, step, ease=ease_in_out)

    # ---------- derecha: el panel ----------
    def _build_panel(self, reset: bool) -> None:
        """Vuelve a dibujar la pestaña visible. `reset` la lleva arriba (o a lo que suena)."""
        self.delete("panel")
        self._rows = []
        self._bars_item = None
        self._hover_row = None
        self.itemconfigure(self._hover.item, state="hidden")
        L = self.L
        if reset:
            self.offset = 0.0
        base = L["list_top"] - self.offset
        x0, x1 = L["panel_x0"], L["panel_x1"]
        if self.tab == "album":
            height = self._build_album(x0, x1, base)
        elif self.tab == "queue":
            height = self._build_queue(x0, x1, base)
        else:
            height = self._build_lyrics(x0, x1, base)
        self.content_h = height
        limit = self._max_offset()
        if self.offset > limit:
            self.move("panel", 0, self.offset - limit)
            self.offset = limit
        self.tag_raise("mask")
        self.tag_raise("chrome")
        if reset:
            if self.tab == "lyrics":
                self._sync_lyric(force=True, animate=False)
            else:
                self._reveal_current()
        self._repoint()

    def _row(self, y: int, h: int, action=None, tip=None, **extra) -> dict:
        row = dict(y0=y, y1=y + h, action=action, tip=tip, items=[], **extra)
        self._rows.append(row)
        return row

    def _text(self, row: dict, x: int, y: float, text: str, font, role: str, anchor="w",
              width: int = 0) -> int:
        item = self.create_text(x, y, text=text, font=font, anchor=anchor, width=width,
                                fill=self.c[role], tags=("panel",))
        row["items"].append((item, role))
        return item

    def _message(self, x0: int, x1: int, base: float, y: int, text: str,
                 action: tuple | None) -> int:
        """Un aviso dentro del panel y, si hay, el enlace que lo resuelve."""
        k = self.k
        row = self._row(y, 0)
        item = self._text(row, x0, base + y, text, self.f["msg"], "dim", anchor="nw",
                          width=x1 - x0)
        box = self.bbox(item)
        h = (box[3] - box[1]) if box else int(20 * k)
        row["y1"] = y + h
        y += h + int(12 * k)
        if action:
            label, callback = action
            link = self._row(y - int(4 * k), self.f["tab"].metrics("linespace") + int(8 * k),
                             action=callback)
            self._text(link, x0, base + y, label, self.f["tab"], "accent", anchor="nw")
            y += self.f["tab"].metrics("linespace") + int(12 * k)
        return y

    def _bars_at(self, x: float, y: float) -> None:
        size = int(16 * self.k)
        self._bars_photo = ImageTk.PhotoImage(_bars(size, self.c["accent"], None, 0.0))
        self._bars_item = self.create_image(x, y, image=self._bars_photo, tags=("panel",))
        self._bars_idle = False
        self.anim.wake()

    def _build_album(self, x0: int, x1: int, base: float) -> int:
        k, f = self.k, self.f
        y = int(2 * k)
        info = self.album_info
        if info:
            hdr_h = f["hdr"].metrics("linespace") + int(2 * k) + f["sub"].metrics("linespace")
            row = self._row(y, hdr_h + int(8 * k),
                            action=self.on["album"] if self.album_link else None,
                            tip="Abrir el álbum" if self.album_link else None, kind="hdr")
            self._text(row, x0, base + y, elide(f["hdr"], info["title"], x1 - x0), f["hdr"],
                       "fg", anchor="nw")
            parts = [info.get("artist"), info.get("year")]
            count = info.get("count") or 0
            if count:
                parts.append("1 canción tuya" if count == 1 else f"{count} canciones tuyas")
            meta = " · ".join(str(p) for p in parts if p)
            self._text(row, x0, base + y + f["hdr"].metrics("linespace") + int(2 * k),
                       elide(f["sub"], meta, x1 - x0), f["sub"], "dim", anchor="nw")
            y += hdr_h + int(18 * k)
        row_h = int(40 * k)
        num_w = int(34 * k)
        for i, t in enumerate(self.album_rows):
            current = t["video_id"] == self.current_vid
            dur = fmt(t.get("duration")) if t.get("duration") else ""
            dur_w = f["row"].measure(dur) if dur else 0
            room = x1 - x0 - num_w - dur_w - int(20 * k)
            title = elide(f["row"], t["title"], room)
            row = self._row(y, row_h, action=None if current else (lambda i=i: self.on["play_album"](i)),
                            tip=t["title"] if title != t["title"] else None, current=current)
            cy = base + y + row_h / 2
            if current:
                self._bars_at(x0 + int(12 * k), cy)
            else:
                number = t.get("track_no")
                self._text(row, x0 + int(12 * k), cy, str(number) if number else "·", f["sub"],
                           "faint", anchor="center")
            self._text(row, x0 + num_w, cy, title, f["row"], "accent" if current else "fg")
            if dur:
                self._text(row, x1 - int(4 * k), cy, dur, f["sub"], "dim", anchor="e")
            y += row_h
        if self.album_note or self.album_action:
            y = self._message(x0, x1, base, y + int(14 * k), self.album_note, self.album_action)
        return y

    def _build_queue(self, x0: int, x1: int, base: float) -> int:
        k, f = self.k, self.f
        y = 0
        if not self.queue_entries:
            return self._message(x0, x1, base, int(4 * k), "No hay nada en cola.", None)
        text_x = x0 + int(32 * k)
        for e in self.queue_entries:
            kind = e["kind"]
            if kind == "hdr":
                y += int(10 * k) if y else 0
                row = self._row(y, int(28 * k))
                self._text(row, x0, base + y + int(14 * k), e["text"].upper(), f["head"], "faint")
                y += int(28 * k)
                continue
            if kind == "more":
                row = self._row(y, int(32 * k))
                self._text(row, text_x, base + y + int(16 * k), e["text"], f["sub"], "dim")
                y += int(32 * k)
                continue
            t = e["track"]
            current = kind == "cur"
            h = int(50 * k)
            dur = ("☁ " if e.get("remote") else "") + (fmt(t.get("duration"))
                                                       if t.get("duration") else "")
            dur_w = f["sub"].measure(dur) if dur.strip() else 0
            room = x1 - text_x - dur_w - int(16 * k)
            title = elide(f["row"], t["title"], room)
            sub = t.get("artist") or ""
            if e.get("why"):
                sub = f"{sub} · {e['why']}" if sub else e["why"]
            row = self._row(y, h, action=None if current else (
                lambda kind=kind, idx=e["idx"]: self.on["queue"](kind, idx)),
                tip=f"{t['title']} — {t.get('artist') or ''}" if title != t["title"] else None,
                current=current)
            if current:
                self._bars_at(x0 + int(12 * k), base + y + h / 2)
            self._text(row, text_x, base + y + int(16 * k), title, f["row"],
                       "accent" if current else ("dim" if e.get("offline") else "fg"))
            self._text(row, text_x, base + y + int(35 * k), elide(f["sub"], sub, room), f["sub"],
                       "dim")
            if dur.strip():
                self._text(row, x1 - int(4 * k), base + y + h / 2, dur, f["sub"], "dim", anchor="e")
            y += h
        return y

    def _build_lyrics(self, x0: int, x1: int, base: float) -> int:
        k = self.k
        if self.lyrics_msg or not self.lyric_lines:
            return self._message(x0, x1, base, int(4 * k), self.lyrics_msg or
                                 "Sin letra para esta canción.", self.lyrics_action)
        gap = int(14 * k)
        y = int(6 * k)
        seek = self.on["seek"]
        for i, (ms, text) in enumerate(self.lyric_lines):
            row = self._row(y - gap // 2, 0, line=i,
                            action=(lambda ms=ms: seek(ms)) if self.synced and ms is not None
                            else None,
                            tip=None)
            item = self._text(row, x0, base + y, text.strip() or "♪", self.f["lyric"],
                              "dim" if self.synced else "fg", anchor="nw", width=x1 - x0)
            box = self.bbox(item)
            h = (box[3] - box[1]) if box else int(24 * k)
            row["y1"] = y + h + gap // 2
            y += h + gap
        return y + int(40 * k)

    # ---------- letra sincronizada ----------
    def _sync_lyric(self, force: bool = False, animate: bool = True) -> None:
        if not self.synced or not self._rows:
            return
        index = line_at(self.lyric_lines, int(self.position))
        if index == self._lyric_index and not force:
            return
        self._lyric_index = index
        c = self.c
        for row in self._rows:
            line = row.get("line")
            if line is None:
                continue
            role = "fg" if line == index else ("faint" if line < index else "dim")
            for item, _role in row["items"]:
                self.itemconfigure(item, fill=c[role])
        if time.monotonic() >= self._follow_after:
            self._follow(index, animate)

    def _follow(self, index: int, animate: bool) -> None:
        """La línea que suena queda a un tercio del panel, como en una partitura."""
        row = next((r for r in self._rows if r.get("line") == index), None)
        if row is None:
            target = 0.0
        else:
            target = row["y0"] - (self.L["list_bottom"] - self.L["list_top"]) * 0.32
        self._scroll_to(target, animate)

    def _reveal_current(self) -> None:
        """Si lo que suena queda fuera del panel, se desplaza hasta dejarlo a la vista."""
        row = next((r for r in self._rows if r.get("current")), None)
        if row is None:
            return
        visible = self.L["list_bottom"] - self.L["list_top"]
        if row["y1"] > self.offset + visible - int(24 * self.k):
            self._scroll_to(row["y0"] - visible / 3, animate=False)

    # ---------- desplazamiento ----------
    def _max_offset(self) -> float:
        visible = self.L["list_bottom"] - self.L["list_top"]
        return max(0.0, self.content_h - visible + int(24 * self.k))

    def _scroll_to(self, target: float, animate: bool) -> None:
        target = max(0.0, min(self._max_offset(), target))
        start = self.offset

        def step(p: float) -> None:
            value = start + (target - start) * p
            self.move("panel", 0, self.offset - value)
            self.offset = value
            self._repoint()

        self.anim.tween(("now-scroll", id(self)), 380 if animate else 0, step, ease=ease_in_out)

    def _scroll_by(self, dy: float) -> None:
        if self.L is None:
            return
        if self.tab == "lyrics":
            self._follow_after = time.monotonic() + FOLLOW_PAUSE_S
        self._scroll_to(self.offset + dy, animate=False)

    def _in_panel(self, x: int, y: int) -> bool:
        L = self.L
        return (L is not None and L["panel_x0"] - int(24 * self.k) <= x <= self._size[0]
                and L["y0"] <= y <= L["list_bottom"])

    def _wheel(self, e) -> str:
        if self._in_panel(e.x, e.y):
            notches = e.delta / 120 if abs(e.delta) >= 120 else (1 if e.delta > 0 else -1)
            self._scroll_by(-notches * 60 * self.k)
        return "break"

    def _touchpad(self, e) -> str:
        _dx, dy = touchpad_deltas(e)
        if dy and self._in_panel(e.x, e.y):
            self._scroll_by(-dy)
        return "break"

    # ---------- ratón ----------
    def _zone_at(self, x: int, y: int) -> dict | None:
        for zone in reversed(self._zones):
            box = zone.get("box")
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return zone
        return None

    def _row_at(self, x: int, y: int) -> dict | None:
        L = self.L
        if L is None or not (L["list_top"] <= y <= L["list_bottom"]):
            return None
        if not L["panel_x0"] - int(12 * self.k) <= x <= L["panel_x1"] + int(8 * self.k):
            return None
        content_y = y - L["list_top"] + self.offset
        return next((r for r in self._rows if r["y0"] <= content_y < r["y1"]
                     and (r["action"] or r["tip"])), None)

    def _motion(self, e) -> None:
        self._pointer = (e.x, e.y)
        self._repoint()

    def _repoint(self) -> None:
        """Vuelve a mirar qué hay bajo el puntero (también tras desplazar o redibujar)."""
        if self._pointer is None or not self.visible:
            return
        x, y = self._pointer
        zone = self._zone_at(x, y)
        row = None if zone else self._row_at(x, y)
        self._set_hot(zone["key"] if zone else None)
        self._set_hover_row(row if row and row["action"] else None)
        target = zone or row
        clickable = bool(target and target.get("action"))
        self.configure(cursor="hand2" if clickable else "")
        tip = target.get("tip") if target else None
        self.tip.set(tip() if callable(tip) else tip)

    def _leave(self, _e=None) -> None:
        self._pointer = None
        self._set_hot(None)
        self._set_hover_row(None)
        self.tip.set(None)

    def _set_hot(self, key: str | None) -> None:
        if key == self._hot:
            return
        old, self._hot = self._hot, key
        for k_ in (old, key):
            if k_ in ("close", "full"):
                self._paint_icon(k_)
            elif k_ == "heart":
                self._paint_heart()
            elif k_ in ("artist", "album"):
                self._paint_links()
            elif k_ and k_.startswith("tab:"):
                tab = k_[4:]
                self.itemconfigure(self._tabs[tab], fill=self.c["fg"] if tab == self.tab or
                                   k_ == key else self.c["dim"])

    def _set_hover_row(self, row: dict | None) -> None:
        if row is self._hover_row:
            return
        self._hover_row = row
        if row is None:
            self.itemconfigure(self._hover.item, state="hidden")
            return
        L, k = self.L, self.k
        w = L["panel_x1"] - L["panel_x0"] + int(20 * k)
        h = max(1, int(row["y1"] - row["y0"]))
        self._hover.show(self._hover_image(w, h))
        self.coords(self._hover.item, L["panel_x0"] - int(12 * k),
                    L["list_top"] - self.offset + row["y0"])
        self.itemconfigure(self._hover.item, state="normal")
        self.tag_raise(self._hover.item, self._bg.item)  # bajo el texto del panel

    def _hover_image(self, w: int, h: int) -> Image.Image:
        return _hover_pill(w, h, self.c["hover"], int(8 * self.k))

    def _click(self, e) -> None:
        zone = self._zone_at(e.x, e.y)
        target = zone or self._row_at(e.x, e.y)
        if target and target.get("action"):
            self.tip.hide()
            target["action"]()

    # ---------- barras de lo que suena ----------
    def _frame(self, now: float) -> bool:
        if self._bars_item is None or self._bars_photo is None or not self.visible:
            return False
        moving = self.playing and not self.anim.reduced
        if not moving and self._bars_idle:
            return False
        if moving and now - self._bars_last < 0.12:
            return True
        self._bars_last = now
        self._bars_idle = not moving
        try:
            pump = max(0.0, min(1.0, float(self.app.scale.pulse)))
        except (AttributeError, TypeError, ValueError):
            pump = 0.0
        size = self._bars_photo.width()
        self._bars_photo.paste(_bars(size, self.c["accent"], now if moving else None, pump))
        return moving
