"""Controles dibujados a mano sobre Canvas.

Tk no suaviza bordes ni tiene transparencias entre controles: las formas se pintan con Pillow con
sobremuestreo y las animaciones las mueve `anim.Animator`. Donde tiene sentido, los controles
mantienen la interfaz del control de Tk al que sustituyen (`get`/`set`, `configure(text=...)`).
"""
import math
import random
import time
import tkinter as tk
import tkinter.font as tkfont
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps, ImageTk

from ..core.waveform import resample
from .anim import Animator, ease_in_out, ease_out
from .icons import icon_mask
from .theme import _rgb, blend, seed_color, text_on

SS = 4
MAX_BARS = 240
FLAT_LEVEL = 0.05  # barras sin canción: antes de elegir nada


def _standard_wave(n: int = MAX_BARS) -> list[float]:
    """Silueta genérica e idéntica para todas las canciones.

    Es solo un relleno: la onda real la sustituye en cuanto ffmpeg termina de medirla. Se genera
    una vez con una semilla fija para que no cambie entre canciones ni entre arranques.
    """
    rng = random.Random(20240517)
    raw = [rng.random() for _ in range(n)]
    for _ in range(3):  # suaviza el ruido hasta que parece una onda, no un código de barras
        raw = [(raw[max(0, i - 1)] + 2 * raw[i] + raw[min(n - 1, i + 1)]) / 4 for i in range(n)]
    # entra y sale suave en los extremos, como una canción cualquiera
    edges = max(1.0, n * 0.12)
    return [min(1.0, 0.08 + 0.92 * min(1.0, (i + 1) / edges, (n - i) / edges)
                * raw[i] ** 2.2 * 1.6)
            for i in range(n)]


STANDARD_LEVELS = _standard_wave()


def ui_fonts(root: tk.Misc) -> dict[str, tkfont.Font]:
    have = set(tkfont.families(root))

    def pick(*names: str) -> str:
        return next((n for n in names if n in have), "Segoe UI")

    text = pick("Segoe UI Variable Text", "Segoe UI")
    strong = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
    return dict(
        display=tkfont.Font(root, family=pick("Bahnschrift Light", "Segoe UI Light"), size=24),
        mark=tkfont.Font(root, family=pick("Bahnschrift SemiBold", "Segoe UI Semibold"), size=13),
        body=tkfont.Font(root, family=text, size=10),
        strong=tkfont.Font(root, family=strong, size=11),
        small=tkfont.Font(root, family=text, size=9),
        digits=tkfont.Font(root, family=pick("Bahnschrift", "Segoe UI"), size=9),
    )


def elide(font: tkfont.Font, text: str, width: int) -> str:
    """Recorta con … hasta que quepa en `width` píxeles."""
    if width <= 0 or font.measure(text) <= width:
        return text if width > 0 else ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def _layer(mask: Image.Image, color: str) -> Image.Image:
    img = Image.new("RGBA", mask.size, color)
    img.putalpha(mask)
    return img


@lru_cache(maxsize=64)
def _rounded_mask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=r * SS, fill=255)
    return m.resize((w, h), Image.LANCZOS)


class _Painted:
    """Una imagen de Pillow mostrada en un Canvas, reutilizando la foto si el tamaño no cambia."""

    def __init__(self, canvas: tk.Canvas, x: int = 0, y: int = 0, anchor: str = "nw") -> None:
        self.canvas = canvas
        self.photo = None
        self.size = None
        self.item = canvas.create_image(x, y, anchor=anchor)

    def show(self, img: Image.Image) -> None:
        if self.photo is not None and self.size == img.size:
            self.photo.paste(img)
            return
        self.photo, self.size = ImageTk.PhotoImage(img), img.size
        self.canvas.itemconfigure(self.item, image=self.photo)


class Tooltip:
    """Etiqueta discreta que aparece tras una pausa: los iconos no llevan texto."""

    def __init__(self, widget: tk.Misc, anim: Animator, text, colors) -> None:
        self.widget, self.anim, self.text, self.colors = widget, anim, text, colors
        self.win = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress-1>", self.hide, add="+")

    def _schedule(self, _e=None) -> None:
        self.hide()
        self.job = self.widget.after(450, self._show)

    def _show(self) -> None:
        self.job = None
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        bg, fg = self.colors()
        win = self.win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.0)
        tk.Label(win, text=text, bg=bg, fg=fg, padx=9, pady=4, font=("Segoe UI", 9)).pack()
        win.update_idletasks()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.widget.winfo_rooty() - win.winfo_reqheight() - 6
        if y < self.widget.winfo_screenheight() // 8:  # en la parte alta de la ventana: debajo
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        win.geometry(f"+{x}+{y}")
        self.anim.tween(("tip", id(self)), 120, lambda p: win.winfo_exists()
                        and win.attributes("-alpha", p))

    def hide(self, _e=None) -> None:
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win is not None:
            self.win.destroy()
            self.win = None


class IconButton(tk.Canvas):
    """Icono sin caja: aparece un disco suave al pasar el ratón y encoge un poco al pulsar."""

    def __init__(self, master, anim: Animator, k: float, icon: str, command=None, tip="",
                 size: int = 36, glyph: int = 20) -> None:
        self.d, self.g = int(size * k), int(glyph * k)
        super().__init__(master, width=self.d, height=self.d, highlightthickness=0, bd=0,
                         cursor="hand2" if command else "arrow", takefocus=1 if command else 0)
        self.anim, self.k, self.command = anim, k, command
        self.h = self.press = 0.0
        self.on = self.active = self.focused = False
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", accent="#ff6a4d")
        self._disc = self._make_disc()
        self._mask = icon_mask(icon, self.g)
        self._painted = _Painted(self, self.d // 2, self.d // 2, "center")
        if command:
            self.bind("<Enter>", lambda e: self._hover(1.0, 110))
            self.bind("<Leave>", lambda e: self._hover(0.0, 170))
            self.bind("<ButtonPress-1>", lambda e: self._press(1.0, 60))
            self.bind("<ButtonRelease-1>", self._release)
            self.bind("<Return>", lambda e: self.command())
            self.bind("<FocusIn>", lambda e: self._focus(True))
            self.bind("<FocusOut>", lambda e: self._focus(False))
            Tooltip(self, anim, tip, lambda: (blend(self.c["bg"], self.c["fg"], 0.14), self.c["fg"]))

    def _make_disc(self) -> Image.Image:
        d = self.d * SS
        m = Image.new("L", (d, d), 0)
        ImageDraw.Draw(m).ellipse((SS, SS, d - SS, d - SS), fill=255)
        return m.resize((self.d, self.d), Image.LANCZOS)

    def _animate(self, attr: str, to: float, ms: int) -> None:
        start = getattr(self, attr)

        def step(p: float) -> None:
            setattr(self, attr, start + (to - start) * p)
            self._render()

        self.anim.tween((attr, id(self)), ms, step)

    def _hover(self, to: float, ms: int) -> None:
        self._animate("h", to, ms)

    def _press(self, to: float, ms: int) -> None:
        self._animate("press", to, ms)

    def _release(self, e) -> None:
        self._press(0.0, 170)
        if 0 <= e.x < self.d and 0 <= e.y < self.d and self.command:
            self.command()

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._render()

    def restyle(self, bg: str, fg: str, dim: str, accent: str) -> None:
        """El icono es transparente: si solo cambia el fondo, basta con cambiar el del Canvas."""
        c = self.c
        repaint = (c["fg"], c["dim"]) != (fg, dim) or self.h > 0.01 or (
            (self.on or self.active or self.focused) and c["accent"] != accent) or self._painted.photo is None
        c.update(bg=bg, fg=fg, dim=dim, accent=accent)
        self.configure(bg=bg)
        if repaint:
            self._render()

    def set_on(self, on: bool) -> None:
        if on != self.on:
            self.on = on
            self._render()

    def set_active(self, active: bool) -> None:
        """Marca la sección abierta; el punto bajo el icono no tapa el estado de `set_on`."""
        if active != self.active:
            self.active = active
            self._render()

    def set_icon(self, icon: str) -> None:
        self._mask = icon_mask(icon, self.g)
        self._render()

    def _render(self) -> None:
        c, d = self.c, self.d
        img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        if self.h > 0.01:
            alpha = self.h
            img.alpha_composite(_layer(self._disc.point(lambda v: int(v * alpha)),
                                       blend(c["bg"], c["fg"], 0.10)))
        if self.focused:
            ring = ImageOps.expand(self._disc.resize((d - 4, d - 4)), 2, 0)
            edge = Image.composite(Image.new("L", (d, d), 0), self._disc, ring)
            img.alpha_composite(_layer(edge, c["accent"]))
        color = c["accent"] if self.on else (c["dim"] if self.command is None else
                                             blend(c["dim"], c["fg"], self.h))
        size = max(4, int(self.g * (1 - 0.1 * self.press)))
        mask = self._mask if size == self.g else self._mask.resize((size, size), Image.LANCZOS)
        off = (d - size) // 2
        img.alpha_composite(_layer(mask, color), (off, off))
        if self.active:
            r = max(1.5, d * 0.045)
            mid = d / 2
            ImageDraw.Draw(img).ellipse((mid - r, d - 2 * r - 1, mid + r, d - 1),
                                        fill=c["accent"])
        self._painted.show(img)


class PlayButton(tk.Canvas):
    """Disco de acento cuyo icono se transforma de ▶ a ⏸; un arco gira mientras la canción abre.

    `configure(text="▶"/"⏸")` cambia el estado, igual que el botón de texto al que sustituye.
    """
    _PLAY = (((8, 5), (13.5, 8.5), (13.5, 15.5), (8, 19)),
             ((13.5, 8.5), (19, 12), (19, 12), (13.5, 15.5)))
    _PAUSE = (((7, 5), (10.5, 5), (10.5, 19), (7, 19)),
              ((13.5, 5), (17, 5), (17, 19), (13.5, 19)))

    def __init__(self, master, anim: Animator, k: float, command, busy=lambda: False,
                 size: int = 54) -> None:
        self.d = int(size * k)
        super().__init__(master, width=self.d, height=self.d, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=1)
        self.anim, self.k, self.command, self.busy = anim, k, command, busy
        self.morph = self.h = self.press = 0.0
        self.playing = self._spinning = False
        self.c = dict(bg="#000000", accent="#ff6a4d", on_accent="#000000")
        self._painted = _Painted(self, self.d // 2, self.d // 2, "center")
        self.bind("<Enter>", lambda e: self._animate("h", 1.0, 120))
        self.bind("<Leave>", lambda e: self._animate("h", 0.0, 180))
        self.bind("<ButtonPress-1>", lambda e: self._animate("press", 1.0, 60))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.command())
        anim.add_loop(self._frame)

    def configure(self, cnf=None, **kw):
        text = kw.pop("text", None)
        if text is not None:
            self.set_playing(text != "▶")
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def _animate(self, attr: str, to: float, ms: int) -> None:
        start = getattr(self, attr)

        def step(p: float) -> None:
            setattr(self, attr, start + (to - start) * p)
            self._render()

        self.anim.tween((attr, id(self)), ms, step)

    def _release(self, e) -> None:
        self._animate("press", 0.0, 180)
        if 0 <= e.x < self.d and 0 <= e.y < self.d:
            self.command()

    def set_playing(self, playing: bool) -> None:
        if playing != self.playing:
            self.playing = playing
            self._animate("morph", 1.0 if playing else 0.0, 200)
            self.anim.wake()

    def restyle(self, bg: str, accent: str, on_accent: str) -> None:
        self.c.update(bg=bg, accent=accent, on_accent=on_accent)
        self.configure(bg=bg)
        self._render()

    def _frame(self, _now: float) -> bool:
        busy = self.busy() and not self.anim.reduced
        if busy or self._spinning:
            self._spinning = busy
            self._render()
        return busy

    def _render(self) -> None:
        d, c = self.d * 3, self.c
        img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        mid = d / 2
        r = d * 0.40 * (1 + 0.04 * self.h - 0.06 * self.press)
        draw.ellipse((mid - r, mid - r, mid + r, mid + r), fill=c["accent"])
        unit = r * 2 * 0.62 / 24
        for a, b in zip(self._PLAY, self._PAUSE):
            pts = [(mid + (ax + (bx - ax) * self.morph - 12) * unit,
                    mid + (ay + (by - ay) * self.morph - 12) * unit)
                   for (ax, ay), (bx, by) in zip(a, b)]
            draw.polygon(pts, fill=c["on_accent"])
        if self._spinning:
            ring = d * 0.47
            start = time.monotonic() * 300 % 360
            draw.arc((mid - ring, mid - ring, mid + ring, mid + ring), start, start + 100,
                     fill=c["accent"], width=max(3, int(d * 0.022)))
        self._painted.show(img.resize((self.d, self.d), Image.LANCZOS))


class TextButton(tk.Label):
    """Acción contextual: solo texto, se aviva al pasar el ratón y aparece con un fundido."""

    def __init__(self, master, anim: Animator, text: str, font, command) -> None:
        super().__init__(master, text=text, font=font, cursor="hand2", padx=8, pady=4)
        self.anim, self.command = anim, command
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888")
        self.visible = True
        self.bind("<Enter>", lambda e: self.configure(fg=self.c["fg"]))
        self.bind("<Leave>", lambda e: self.configure(fg=self.c["dim"]))
        self.bind("<ButtonRelease-1>", lambda e: self.command())

    def restyle(self, bg: str, fg: str, dim: str) -> None:
        self.c.update(bg=bg, fg=fg, dim=dim)
        self.configure(bg=bg, fg=dim)

    def set_visible(self, on: bool) -> None:
        if on == self.visible:
            return
        self.visible = on
        if not on:
            self.grid_remove()
            return
        self.grid()
        c = self.c
        self.anim.tween(("show", id(self)), 180,
                        lambda p: self.configure(fg=blend(c["bg"], c["dim"], p)))


class PillButton(tk.Canvas):
    """Botón redondeado con estados suave, acento y desactivado.

    Sustituye a los `ttk.Button` de las páginas: comparte la paleta y la tipografía de la
    ventana y evita el rectángulo gris nativo. `state(["!disabled"])` y `configure(text=...)`
    funcionan como en un control de Tk para no cambiar el código que ya los usa.
    """

    def __init__(self, master, anim: Animator, k: float, text: str, command, font,
                 variant: str = "secondary", min_width: float = 0, height: float = 36) -> None:
        self.anim, self.k, self.command = anim, k, command
        self.font, self.text, self.variant = font, text, variant
        self.enabled = True
        self.h = self.press = 0.0
        self.focused = False
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555",
                      accent="#ff6a4d", danger="#ee6a66")
        self.pad = int(16 * k)
        self.h_ = int(height * k)
        self.min_w = int(min_width * k)
        self.w = self._measure()
        super().__init__(master, width=self.w, height=self.h_, highlightthickness=0, bd=0,
                         bg=self.c["bg"], cursor="hand2" if command else "arrow",
                         takefocus=1 if command else 0)
        self._painted = _Painted(self)
        self._text_item = self.create_text(self.w // 2, self.h_ // 2, text=text,
                                           font=self.font, anchor="center")
        if command:
            self.bind("<Enter>", lambda e: self._hover(1.0, 110))
            self.bind("<Leave>", self._leave)
            self.bind("<ButtonPress-1>", self._down)
            self.bind("<ButtonRelease-1>", self._release)
            self.bind("<Return>", lambda e: self._fire())
            self.bind("<space>", self._key_fire)
            self.bind("<FocusIn>", lambda e: self._focus(True))
            self.bind("<FocusOut>", lambda e: self._focus(False))
        self._render()

    # ---------- API parecida a ttk.Button ----------
    def configure(self, cnf=None, **kw):
        text = kw.pop("text", None)
        if text is not None:
            self.set_text(text)
        if cnf is not None or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def state(self, spec=None):
        if spec is None:
            return () if self.enabled else ("disabled",)
        if isinstance(spec, str):
            spec = (spec,)
        disabled = "disabled" in spec
        self.set_enabled(not disabled)

    def set_enabled(self, on: bool) -> None:
        if on == self.enabled:
            return
        self.enabled = on
        if not on:
            self.h = self.press = 0.0
        self.configure(cursor="hand2" if on and self.command else "arrow")
        self._render()

    def set_text(self, text: str) -> None:
        if text == self.text:
            return
        self.text = text
        width = self._measure()
        if width != self.w:
            self.w = width
            self.configure(width=width)
        self.itemconfigure(self._text_item, text=text)
        self._render()

    def restyle(self, **colors) -> None:
        self.c.update(colors)
        self.configure(bg=self.c["bg"])
        self._render()

    # ---------- medidas y dibujo ----------
    def _measure(self) -> int:
        return max(self.min_w, int(54 * self.k), self.font.measure(self.text) + 2 * self.pad)

    def _colors(self) -> tuple[str, str]:
        c = self.c
        if not self.enabled:
            if self.variant == "ghost":
                return c["bg"], c["dim"]
            return blend(c["bg"], c["fg"], 0.045), c["dim"]
        if self.variant == "primary":
            base = c["accent"]
            hover = blend(base, text_on(base), 0.12)
            text = text_on(base)
        elif self.variant == "danger":
            base = blend(c["bg"], c["danger"], 0.10)
            hover = blend(c["bg"], c["danger"], 0.17)
            text = c["danger"]
        elif self.variant == "ghost":
            base = c["bg"]
            hover = blend(c["bg"], c["fg"], 0.07)
            text = blend(c["dim"], c["fg"], self.h)
        else:  # secondary
            base = blend(c["bg"], c["fg"], 0.07)
            hover = blend(c["bg"], c["fg"], 0.12)
            text = c["fg"]
        fill = blend(base, hover, self.h)
        if self.press:
            fill = blend(fill, hover if self.variant != "primary" else c["fg"],
                         0.10 * self.press)
        return fill, text

    def _render(self) -> None:
        w, h = self.w, self.h_
        fill, text = self._colors()
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        radius = max(2, h // 2)
        img.alpha_composite(_layer(_rounded_mask(w, h, radius), fill))
        if self.enabled and self.focused and self.command:
            ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle(
                (1, 1, w - 2, h - 2), radius=max(2, radius - 1),
                outline=text if self.variant == "primary" else self.c["accent"],
                width=max(1, int(self.k)))
            img.alpha_composite(ring)
        self._painted.show(img)
        self.itemconfigure(self._text_item, fill=text)
        self.coords(self._text_item, w // 2, h // 2 + (1 if self.press > 0.5 else 0))

    # ---------- interacción ----------
    def _animate(self, attr: str, to: float, ms: int) -> None:
        start = getattr(self, attr)

        def step(p: float) -> None:
            setattr(self, attr, start + (to - start) * p)
            self._render()

        self.anim.tween((attr, id(self)), ms, step)

    def _hover(self, to: float, ms: int) -> None:
        if self.enabled:
            self._animate("h", to, ms)

    def _leave(self, _e=None) -> None:
        self._animate("press", 0.0, 120)
        self._hover(0.0, 170)

    def _down(self, _e=None) -> str | None:
        if not self.enabled:
            return None
        self.focus_set()
        self._animate("press", 1.0, 60)
        return "break"

    def _release(self, e) -> str | None:
        if not self.enabled:
            return None
        self._animate("press", 0.0, 170)
        if 0 <= e.x < self.w and 0 <= e.y < self.h_:
            self._fire()
        return "break"

    def _fire(self) -> str | None:
        if self.enabled and self.command:
            self.command()
            return "break"
        return None

    def _key_fire(self, _e=None) -> str | None:
        return self._fire()

    def _focus(self, on: bool) -> None:
        self.focused = on
        self._render()



class SwapText:
    """Un texto en un Canvas: al cambiar, el viejo sube y se apaga mientras el nuevo entra."""

    def __init__(self, canvas: tk.Canvas, anim: Animator, font, x: int, y: int, rise: int) -> None:
        self.c, self.anim, self.font = canvas, anim, font
        self.x, self.y, self.rise = x, y, rise
        self.fg, self.bg = "#ffffff", "#000000"
        self.text = ""
        self.max_w = 0
        self._old = None
        self.item = canvas.create_text(x, y, text="", font=font, anchor="w")

    def _fit(self, text: str) -> str:
        return elide(self.font, text, self.max_w) if self.max_w else text

    def set(self, text: str, animate: bool = True) -> None:
        if text == self.text:
            return
        self.text = text
        c, x, y, rise = self.c, self.x, self.y, self.rise
        if self._old is not None:
            c.delete(self._old)
            self._old = None
        if not animate:
            self.anim._tweens.pop(id(self), None)
            c.coords(self.item, x, y)
            c.itemconfigure(self.item, text=self._fit(text), fill=self.fg)
            return
        old = self._old = self.item
        new = self.item = c.create_text(x, y + rise, text=self._fit(text), font=self.font,
                                        anchor="w", fill=self.bg)

        def step(p: float) -> None:
            c.coords(new, x, y + rise * (1 - p))
            c.itemconfigure(new, fill=blend(self.bg, self.fg, p))
            c.coords(old, x, y - rise * p)
            c.itemconfigure(old, fill=blend(self.fg, self.bg, min(1.0, p * 1.7)))

        def done() -> None:
            c.delete(old)
            self._old = None

        self.anim.tween(id(self), 280, step, done=done)

    def refit(self) -> None:
        self.c.itemconfigure(self.item, text=self._fit(self.text))

    def recolor(self, fg: str, bg: str) -> None:
        self.fg, self.bg = fg, bg
        self.c.itemconfigure(self.item, fill=fg)


class NowPlaying(tk.Canvas):
    def __init__(self, master, anim: Animator, k: float, fonts: dict) -> None:
        h = int(44 * k)
        super().__init__(master, height=h, width=int(120 * k), highlightthickness=0, bd=0)
        self.title = SwapText(self, anim, fonts["strong"], 0, int(h * 0.30), int(8 * k))
        self.artist = SwapText(self, anim, fonts["small"], 0, int(h * 0.74), int(6 * k))
        self.bind("<Configure>", self._resized)

    def _resized(self, e) -> None:
        for line in (self.title, self.artist):
            line.max_w = e.width
            line.refit()

    def set(self, title: str, artist: str) -> None:
        self.title.set(title)
        self.artist.set(artist)

    def restyle(self, bg: str, fg: str, dim: str) -> None:
        self.configure(bg=bg)
        self.title.recolor(fg, bg)
        self.artist.recolor(dim, bg)


class Header(tk.Canvas):
    """Cabecera: título grande y línea de estado sobre un degradado teñido por la portada."""

    def __init__(self, master, anim: Animator, k: float, fonts: dict, x: int) -> None:
        self.h = int(92 * k)
        super().__init__(master, height=self.h, highlightthickness=0, bd=0)
        self.anim, self.pad = anim, x
        self.colors = ("#000000", "#000000")
        self._painted = _Painted(self)
        self.title = SwapText(self, anim, fonts["display"], x, int(30 * k), int(10 * k))
        self.sub = SwapText(self, anim, fonts["small"], x, int(66 * k), 0)
        self._dim = self._fg = "#888888"
        self.bind("<Configure>", self._resized)

    def _resized(self, e) -> None:
        self.title.max_w = self.sub.max_w = max(1, e.width - self.pad - 16)
        self.title.refit()
        self.sub.refit()
        self._paint()

    def _paint(self) -> None:
        w = max(1, self.winfo_width())
        top, bottom = self.colors
        tr, tg, tb = _rgb(top)
        br, bg, bb = _rgb(bottom)
        col = Image.new("RGB", (1, self.h))
        last = max(1, self.h - 1)
        col.putdata([(round(tr + (br - tr) * (y / last) ** 1.3),
                      round(tg + (bg - tg) * (y / last) ** 1.3),
                      round(tb + (bb - tb) * (y / last) ** 1.3)) for y in range(self.h)])
        self._painted.show(col.resize((w, self.h), Image.NEAREST))
        self.tag_lower(self._painted.item)

    def restyle(self, top: str, bottom: str, fg: str, dim: str) -> None:
        self.colors = (top, bottom)
        self._fg, self._dim = fg, dim
        self.configure(bg=bottom)
        mid = blend(top, bottom, 0.5)
        self.title.recolor(fg, mid)
        self.sub.recolor(dim, mid)
        self._paint()

    def set_title(self, text: str, animate: bool = True) -> None:
        self.title.set(text, animate)

    def set_status(self, text: str) -> None:
        if text == self.sub.text:
            return
        self.sub.set(text, animate=False)
        item, fg, dim = self.sub.item, self._fg, self._dim
        self.anim.tween(("status", id(self)), 700,
                        lambda p: self.itemconfigure(item, fill=blend(fg, dim, p)))


class StatusLine:
    """`status.configure(text=...)`, como la etiqueta a la que sustituye; se ve en la cabecera."""

    def __init__(self, header: Header) -> None:
        self.header = header

    def configure(self, text: str = "", **_kw) -> None:
        self.header.set_status(text)


class SearchBox(tk.Canvas):
    """Buscador en forma de píldora: al enfocarlo se ensancha y su borde toma el acento."""

    def __init__(self, master, anim: Animator, k: float, font, variable: tk.StringVar, hint: str,
                 rest: int = 300, focused: int = 420) -> None:
        self.h = int(36 * k)
        self.rest, self.wide = int(rest * k), int(focused * k)
        self.w = self.rest
        super().__init__(master, width=self.w, height=self.h, highlightthickness=0, bd=0,
                         cursor="xterm")
        self.anim, self.k, self.var = anim, k, variable
        self.p = 0.0
        self.c = dict(bg="#000000", field="#111111", fg="#ffffff", dim="#888888", faint="#555555",
                      line="#333333", accent="#ff6a4d")
        self._pill = _Painted(self)
        self._icon_mask = icon_mask("search", int(16 * k))
        self._icon = _Painted(self, int(20 * k), self.h // 2, "center")
        self.entry = tk.Entry(self, textvariable=variable, font=font, bd=0, relief="flat",
                              highlightthickness=0)
        self._win = self.create_window(int(36 * k), self.h // 2, anchor="w", window=self.entry,
                                       height=int(24 * k), width=self.w - int(66 * k))
        # dentro del Entry: un control incrustado siempre tapa el texto del Canvas
        self._hint = tk.Label(self.entry, text=hint, font=font, bd=0, padx=0, pady=0, cursor="xterm")
        self._hint.place(x=0, rely=0.5, anchor="w")
        self._hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._x = self.create_text(self.w - int(17 * k), self.h // 2, text="✕",
                                   font=("Segoe UI Symbol", 8), state="hidden")
        self.tag_bind(self._x, "<Button-1>", lambda e: self.clear())
        self.tag_bind(self._x, "<Enter>", lambda e: self.configure(cursor="hand2"))
        self.tag_bind(self._x, "<Leave>", lambda e: self.configure(cursor="xterm"))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: self._grow(1.0))
        self.entry.bind("<FocusOut>", lambda e: self._grow(0.0))
        self.entry.bind("<Escape>", lambda e: self.clear())
        variable.trace_add("write", lambda *a: self._sync())

    def clear(self) -> None:
        self.var.set("")
        self.entry.focus_set()

    def _sync(self) -> None:
        has = bool(self.var.get())
        if has:
            self._hint.place_forget()
        else:
            self._hint.place(x=0, rely=0.5, anchor="w")
        self.itemconfigure(self._x, state="normal" if has else "hidden")
        if has != (self.p > 0.5) or has:
            self._grow(1.0 if has or self.focus_get() is self.entry else 0.0)

    def _grow(self, to: float) -> None:
        if to == 0.0 and self.var.get():
            to = 1.0
        start = self.p
        if start == to:
            self._render()
            return

        def step(q: float) -> None:
            self.p = start + (to - start) * q
            self._render()

        self.anim.tween(("grow", id(self)), 220, step)

    def restyle(self, **colors) -> None:
        """La píldora no cambia con el tinte de la ventana salvo el borde cuando está enfocada."""
        before = dict(self.c)
        self.c.update(colors)
        c = self.c
        self.configure(bg=c["bg"])
        solid = ("field", "fg", "dim", "line")
        if any(before[key] != c[key] for key in solid) or self._pill.photo is None:
            self.entry.configure(bg=c["field"], fg=c["fg"], selectforeground=c["fg"],
                                 insertwidth=max(1, int(1.5 * self.k)))
            self._hint.configure(bg=c["field"], fg=c["dim"])
            self.itemconfigure(self._x, fill=c["dim"])
            self._icon.show(_layer(self._icon_mask, c["dim"]))
            self._render()
        elif self.p > 0.01 and before["accent"] != c["accent"]:
            self._render()
        self.entry.configure(insertbackground=c["accent"],
                             selectbackground=blend(c["field"], c["accent"], 0.35))

    def _render(self) -> None:
        c, h = self.c, self.h
        w = int(self.rest + (self.wide - self.rest) * self.p)
        if w != self.w:
            self.w = w
            self.configure(width=w)
            self.itemconfigure(self._win, width=w - int(66 * self.k))
            self.coords(self._x, w - int(17 * self.k), h // 2)
        bw = max(1, round(self.k))
        outer = _rounded_mask(w, h, h // 2)
        inner = _rounded_mask(w - 2 * bw, h - 2 * bw, h // 2 - bw)
        img = _layer(outer, blend(c["line"], c["accent"], self.p))
        img.alpha_composite(_layer(inner, c["field"]), (bw, bw))
        self._pill.show(img)


class Slider(tk.Canvas):
    """Carril fino para el volumen; rueda del ratón incluida. Misma interfaz que ttk.Scale."""

    def __init__(self, master, anim: Animator, k: float, command=None, length: int = 92,
                 to: float = 100.0) -> None:
        self.w, self.h = int(length * k), int(24 * k)
        super().__init__(master, width=self.w, height=self.h, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.anim, self.k, self.command, self.to = anim, k, command, to
        self.value = 0.0
        self.h_amt = 0.0
        self.c = dict(bg="#000000", rest="#333333", accent="#ff6a4d")
        self._painted = _Painted(self)
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", lambda e: self._hover(0.0))
        self.bind("<ButtonPress-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<MouseWheel>", lambda e: self.set(self.value + (5 if e.delta > 0 else -5)))

    def _hover(self, to: float) -> None:
        start = self.h_amt

        def step(p: float) -> None:
            self.h_amt = start + (to - start) * p
            self._render()

        self.anim.tween(("slider", id(self)), 120, step)

    def _margin(self) -> int:
        return int(7 * self.k)

    def _drag(self, e) -> None:
        m = self._margin()
        self.set((e.x - m) / max(1, self.w - 2 * m) * self.to)

    def get(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = max(0.0, min(self.to, float(value)))
        self._render()
        if self.command:
            self.command(self.value)

    def restyle(self, bg: str, rest: str, accent: str) -> None:
        self.c.update(bg=bg, rest=rest, accent=accent)
        self.configure(bg=bg)
        self._render()

    def _render(self) -> None:
        c = self.c
        w, h = self.w * SS, self.h * SS
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = self._margin() * SS
        cy = h / 2
        t = 1.5 * self.k * SS
        x = m + (w - 2 * m) * self.value / self.to
        d.rounded_rectangle((m, cy - t, w - m, cy + t), radius=t, fill=c["rest"])
        d.rounded_rectangle((m, cy - t, max(m + 1, x), cy + t), radius=t, fill=c["accent"])
        r = (4 + 2 * self.h_amt) * self.k * SS
        d.ellipse((x - r, cy - r, x + r, cy + r), fill=c["accent"])
        self._painted.show(img.resize((self.w, self.h), Image.LANCZOS))


class Cover(tk.Canvas):
    """Portada con esquinas redondeadas; al cambiar, funde la vieja con la nueva."""

    def __init__(self, master, anim: Animator, k: float, size: int = 60) -> None:
        self.d = int(size * k)
        super().__init__(master, width=self.d, height=self.d, highlightthickness=0, bd=0)
        self.anim = anim
        self._mask = _rounded_mask(self.d, self.d, int(10 * k))
        self._painted = _Painted(self)
        self._current: Image.Image | None = None
        self._source: tuple | None = None
        self.tile = "#333333"

    def _compose(self, img: Image.Image | None, seed: str | None) -> Image.Image:
        d = self.d
        if img is not None:
            out = ImageOps.fit(img.convert("RGB"), (d, d), Image.LANCZOS).convert("RGBA")
        elif seed:  # sin portada: un degradado propio de la canción
            base = seed_color(seed)
            ramp = Image.linear_gradient("L").rotate(35).crop((48, 48, 208, 208)).resize((d, d))
            out = ImageOps.colorize(ramp, blend(base, "#000000", 0.35),
                                    blend(base, "#ffffff", 0.18)).convert("RGBA")
        else:
            out = Image.new("RGBA", (d, d), self.tile)
        out.putalpha(self._mask)
        return out

    def show(self, img: Image.Image | None, seed: str | None) -> None:
        self._source = (img, seed)
        target = self._compose(img, seed)
        before = self._current
        self._current = target
        if before is None:
            self._painted.show(target)
            return
        self.anim.tween(("cover", id(self)), 420,
                        lambda p: self._painted.show(Image.blend(before, target, p)),
                        ease=ease_in_out)

    def restyle(self, bg: str, tile: str) -> None:
        self.configure(bg=bg)
        if tile != self.tile:
            self.tile = tile
            if self._source is None or self._source[0] is None and not self._source[1]:
                self._current = self._compose(None, None)
                self._painted.show(self._current)


class Equalizer(tk.Canvas):
    """Tres barras animadas que marcan la fila que suena. Decorativas, como la onda.

    Es un Canvas colocado sobre la tabla: repintar una imagen dentro del Treeview obliga a
    redibujar la tabla entera, demasiado lento para animarla.
    """

    def __init__(self, master, anim: Animator, k: float, playing) -> None:
        self.size = int(16 * k)
        super().__init__(master, width=self.size, height=self.size, highlightthickness=0, bd=0)
        self.anim, self.playing = anim, playing
        self.color = "#ff6a4d"
        self._painted = _Painted(self)
        self.on_paint = None  # para recolocarlo si la fila se movió
        self._last = 0.0
        self._idle = False
        anim.add_loop(self.frame)

    def frame(self, now: float) -> bool:
        moving = self.playing() and not self.anim.reduced
        if not moving and self._idle:
            return False
        if moving and now - self._last < 0.12:
            return True
        self._last = now
        self._idle = not moving
        self._paint(now if moving else None)
        return moving

    def set_color(self, color: str) -> None:
        self.color = color
        self._idle = False
        self.anim.wake()

    def _paint(self, now: float | None) -> None:
        s = self.size * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        bw = s / 5
        for i in range(3):
            level = 0.3 if now is None else 0.45 + 0.45 * math.sin(now * (6.3 + i * 1.7) + i * 2.1)
            x = bw * 0.5 + i * bw * 1.5
            d.rounded_rectangle((x, s * (0.92 - 0.8 * level), x + bw, s * 0.92), radius=bw / 2,
                                fill=self.color)
        self._painted.show(img.resize((self.size, self.size), Image.LANCZOS))
        if self.on_paint:
            self.on_paint()


class WaveSeek(tk.Canvas):
    """Barra de progreso en forma de onda: el borde superior del reproductor.

    Las alturas son el volumen real de la canción (`core.waveform`). Hasta tenerlo se ve una onda
    estándar (la misma para todas), que se transforma en la real al llegar los datos. Mientras
    suena, las barras se rizan alrededor del cabezal (eso sí es decorativo); en pausa se calman y
    el bucle se detiene.
    Solo se tocan las barras cercanas al cabezal: redibujar el ancho entero en cada fotograma
    quitaría tiempo al hilo de la interfaz, que también mueve los fundidos de audio.
    Misma interfaz que el ttk.Scale de 0 a 1000 al que sustituye.
    """

    def __init__(self, master, anim: Animator, k: float, font, playing, time_at,
                 on_press=None, on_release=None) -> None:
        self.bars_h = int(38 * k)
        super().__init__(master, height=self.bars_h + int(16 * k), highlightthickness=0, bd=0)
        self.anim, self.k, self.font = anim, k, font
        self._playing, self.time_at = playing, time_at
        self.on_press, self.on_release = on_press, on_release
        self.seed: str | None = None
        self.levels: list[float] | None = None
        self.value = self.shown = 0.0
        self.energy = 0.0
        self.morph, self._m0 = 1.0, 0.0
        self.bars: list[int] = []
        self.a: list[float] = []
        self.b: list[float] = []
        self.phase: list[float] = []
        self.last: list[float] = []
        self.hover_x: int | None = None
        self.dragging = False
        self._painted_key = (-1, -1)
        self._dirty = True
        self._full = True    # el próximo dibujo recorre todas las barras
        self._window = (0, 0)  # barras movidas en el dibujo anterior
        self.c = dict(bg="#000000", rest="#333333", ahead="#555555", accent="#ff6a4d", fg="#ffffff")
        self._label = self.create_text(0, int(7 * k), text="", font=font, state="hidden")
        self.bind("<Configure>", self._rebuild)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", self._leave)
        anim.add_loop(self._frame)

    # ---------- interfaz de ttk.Scale ----------
    def get(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = max(0.0, min(1000.0, float(value)))
        self.anim.wake()

    # ---------- canción ----------
    def set_track(self, seed: str, levels: list[float] | None = None) -> None:
        """Pasa a otra canción; `levels` son sus niveles reales, si ya se conocen."""
        if seed != self.seed:
            self.seed = seed
            self.configure(cursor="hand2")
            self._morph_to(levels)

    def set_levels(self, seed: str, levels: list[float]) -> None:
        """Llegaron los niveles de una canción: solo importan si sigue siendo la actual."""
        if seed == self.seed:
            self._morph_to(levels)

    def _target(self, n: int) -> list[float]:
        if self.levels:
            levels = resample(self.levels, n)
        elif self.seed:  # canción elegida pero aún sin medir: onda estándar, igual para todas
            levels = resample(STANDARD_LEVELS, n)
        else:
            levels = [FLAT_LEVEL] * n
        return [max(FLAT_LEVEL, v) for v in levels]

    def _morph_to(self, levels: list[float] | None) -> None:
        self.levels = levels
        self.a = self._current_levels()
        self.b = self._target(len(self.bars))
        self.morph, self._m0 = (1.0, 0.0) if self.anim.reduced else (0.0, time.monotonic())
        if self.anim.reduced:
            self.a = list(self.b)
        self._dirty = self._full = True
        self.anim.wake()

    def _current_levels(self) -> list[float]:
        n = len(self.bars)
        return [self._level(i, n) for i in range(n)]

    def _level(self, i: int, n: int) -> float:
        m = max(0.0, min(1.0, self.morph * 1.7 - i / max(1, n) * 0.7))
        return self.a[i] + (self.b[i] - self.a[i]) * ease_out(m)

    # ---------- geometría ----------
    def _rebuild(self, e=None) -> None:
        w = max(1, self.winfo_width())
        step = max(5 * self.k, w / MAX_BARS)
        n = max(1, int(w / step))
        for bar in self.bars:
            self.delete(bar)
        self.step, self.bw = w / n, max(2, round(3 * self.k))
        rng = random.Random(7)
        self.bars = [self.create_rectangle(0, 0, 0, 0, outline="", width=0) for _ in range(n)]
        self.phase = [rng.uniform(0, math.tau) for _ in range(n)]
        self.b = self._target(n)
        self.a = list(self.b)
        self.morph = 1.0
        self.last = [-1.0] * n
        self.tag_raise(self._label)
        self._painted_key = (-1, -1)
        self._dirty = self._full = True
        self.anim.wake()

    def restyle(self, bg: str, rest: str, ahead: str, accent: str, fg: str) -> None:
        self.c.update(bg=bg, rest=rest, ahead=ahead, accent=accent, fg=fg)
        self.configure(bg=bg)
        self.itemconfigure(self._label, fill=fg)
        self._painted_key = (-1, -1)
        self._dirty = True
        self.anim.wake()

    # ---------- ratón ----------
    def _value_at(self, x: int) -> float:
        return max(0.0, min(1000.0, x / max(1, self.winfo_width()) * 1000))

    def _press(self, e) -> None:
        if not self.seed:
            return
        self.dragging = True
        self.value = self._value_at(e.x)
        if self.on_press:
            self.on_press()
        self.anim.wake()

    def _drag(self, e) -> None:
        if self.dragging:
            self.value = self._value_at(e.x)
            self._hover(e)

    def _release(self, _e) -> None:
        if self.dragging:
            self.dragging = False
            if self.on_release:
                self.on_release()

    def _hover(self, e) -> None:
        if not self.seed:
            return
        self.hover_x = e.x
        text = self.time_at(self._value_at(e.x) / 1000)
        pad = int(18 * self.k)
        x = max(pad, min(self.winfo_width() - pad, e.x))
        self.coords(self._label, x, int(7 * self.k))
        self.itemconfigure(self._label, text=text, state="normal" if text else "hidden")
        self._dirty = True
        self.anim.wake()

    def _leave(self, _e) -> None:
        self.hover_x = None
        self.itemconfigure(self._label, state="hidden")
        self._dirty = True
        self.anim.wake()

    # ---------- fotograma ----------
    def _frame(self, now: float) -> bool:
        if not self.bars or self.winfo_toplevel().state() == "iconic":
            return False
        reduced = self.anim.reduced
        target = 1.0 if self._playing() and not reduced else 0.0
        moving = False
        if abs(self.energy - target) > 0.004:
            self.energy += (target - self.energy) * 0.09
            moving = True
        else:
            self.energy = target
        gap = self.value - self.shown
        if abs(gap) > 0.3 and not reduced:
            self.shown += gap * 0.3
            moving = True
        else:
            self.shown = self.value
        if self.morph < 1.0:
            self.morph = min(1.0, (now - self._m0) / 0.6)
            moving = self._full = True
        if not (moving or self.energy > 0 or self._dirty):
            return False
        self._dirty = False
        self._draw(now)
        return moving or self.energy > 0

    def _draw(self, now: float) -> None:
        n = len(self.bars)
        if len(self.a) != n or len(self.b) != n:
            return
        head = self.shown / 1000 * n
        energy, H, step, bw = self.energy, self.bars_h, self.step, self.bw
        floor = self.winfo_height()
        low = max(2.0, 2 * self.k)
        sin, exp = math.sin, math.exp
        full, self._full = self._full, False
        lo, hi = max(0, int(head) - 20), min(n, int(head) + 21)
        if full:
            indices = range(n)
        else:  # incluye la ventana anterior: esas barras vuelven a su altura al salir de ella
            indices = range(min(lo, self._window[0]), max(hi, self._window[1]))
        self._window = (lo, hi)
        for i in indices:
            level = self._level(i, n)
            if energy > 0:
                near = (i - head) / 8
                level *= 1 + energy * exp(-near * near) * (0.24 + 0.2 * sin(now * 6 + self.phase[i]))
            h = max(low, min(float(H), level * H))
            if abs(h - self.last[i]) >= 0.5:
                self.last[i] = h
                x = i * step
                self.coords(self.bars[i], x, floor - h, x + bw, floor)
        self._recolor(int(head), n)

    def _recolor(self, played: int, n: int) -> None:
        hover = -1 if self.hover_x is None else int(self.hover_x / max(1, self.winfo_width()) * n)
        key = (played, hover)
        old_p, old_h = self._painted_key
        if key == self._painted_key:
            return
        if old_p < 0:
            lo, hi = 0, n
        else:
            marks = [played, old_p, *(x for x in (hover, old_h) if x >= 0)]
            lo, hi = max(0, min(marks) - 1), min(n, max(marks) + 2)
        c = self.c
        for i in range(lo, hi):
            color = c["accent"] if i < played else (c["ahead"] if i < hover else c["rest"])
            self.itemconfigure(self.bars[i], fill=color)
        self._painted_key = key


class RowList(tk.Canvas):
    """Lista con hover, selección que se desliza y desplazamiento suave.

    Selección como un Listbox `extended` si `multi`: clic, Ctrl+clic y Mayús+clic.
    Cada fila es un dict: text, right (opcional), hdr (encabezado no seleccionable), color.
    """

    def __init__(self, master, anim: Animator, k: float, fonts: dict, on_select=None,
                 on_activate=None, multi: bool = True, row: int = 30) -> None:
        super().__init__(master, highlightthickness=0, bd=0, takefocus=1)
        self.anim, self.k, self.fonts = anim, k, fonts
        self.on_select, self.on_activate, self.multi = on_select, on_activate, multi
        self.rh, self.hh, self.gap, self.pad = int(row * k), int(24 * k), int(14 * k), int(12 * k)
        self.items: list[dict] = []
        self.pos: list[tuple[int, int]] = []
        self.text_ids: list[int] = []
        self.sel: set[int] = set()
        self.anchor: int | None = None
        self.hovered: int | None = None
        self.off = 0.0
        self.total = 1
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555", sel="#333333",
                      hover="#222222")
        self._pills: dict[tuple, ImageTk.PhotoImage] = {}
        self._sel_items: dict[int, int] = {}
        self._hover_item = None
        self._width = 0
        self.bind("<Configure>", self._resized)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Up>", lambda e: self._key(-1))
        self.bind("<Down>", lambda e: self._key(1))

    # ---------- interfaz de Listbox ----------
    def curselection(self) -> tuple[int, ...]:
        return tuple(sorted(self.sel))

    def selection_set(self, i: int) -> None:
        if 0 <= i < len(self.items) and not self.items[i].get("hdr"):
            self.sel.add(i)
            self._draw_selection()

    def selection_clear(self) -> None:
        self.sel.clear()
        self._draw_selection()

    def index_at(self, y: float) -> int | None:
        """Índice de la fila bajo una coordenada de widget (None en huecos y encabezados)."""
        return self._hit(self.canvasy(y))

    def set_items(self, items: list[dict]) -> None:
        self.items = items
        self.sel = {i for i in self.sel if i < len(items) and not items[i].get("hdr")}
        self._layout()

    def restyle(self, **colors) -> None:
        self.c.update(colors)
        self.configure(bg=self.c["bg"])
        self._pills.clear()
        self._layout()

    # ---------- dibujo ----------
    def _resized(self, e) -> None:
        if e.width != self._width:
            self._width = e.width
            self._pills.clear()
            self._layout()

    def _pill(self, h: int, color: str) -> ImageTk.PhotoImage:
        key = (h, color)
        if key not in self._pills:
            w = max(2, self._width)
            self._pills[key] = ImageTk.PhotoImage(
                _layer(_rounded_mask(w, h, int(8 * self.k)), color), master=self)
        return self._pills[key]

    def _text_color(self, i: int) -> str:
        it = self.items[i]
        if it.get("hdr"):
            return self.c["faint"]
        if it.get("color"):
            return it["color"]
        return self.c["fg"] if i in self.sel or i == self.hovered else self.c["dim"]

    def _layout(self) -> None:
        self.delete("all")
        self._sel_items.clear()
        self._hover_item = None
        self.pos, self.text_ids = [], []
        width = self._width or self.winfo_reqwidth()
        pad = self.pad
        y = int(2 * self.k)
        for i, it in enumerate(self.items):
            hdr = bool(it.get("hdr"))
            if hdr:
                y += self.gap
            h = self.hh if hdr else self.rh
            self.pos.append((y, h))
            font = self.fonts["small" if hdr else "body"]
            room = width - 2 * pad
            right = it.get("right")
            if right:
                room -= self.fonts["small"].measure(right) + pad
                self.create_text(width - pad, y + h // 2, anchor="e", text=right,
                                 font=self.fonts["small"], fill=self.c["faint"])
            self.text_ids.append(self.create_text(
                pad, y + h // 2, anchor="w", text=elide(font, it["text"], room), font=font,
                fill=self._text_color(i)))
            y += h
        self.total = y + int(8 * self.k)
        self.configure(scrollregion=(0, 0, width, self.total))
        self._draw_selection()
        self._apply_scroll(self.off)

    def _draw_selection(self, slide_from: int | None = None) -> None:
        for item in self._sel_items.values():
            self.delete(item)
        self._sel_items.clear()
        for i in sorted(self.sel):
            if i >= len(self.pos):
                continue
            y, h = self.pos[i]
            self._sel_items[i] = self.create_image(0, y, anchor="nw",
                                                   image=self._pill(h, self.c["sel"]))
            self.tag_lower(self._sel_items[i])
        for i, tid in enumerate(self.text_ids):
            self.itemconfigure(tid, fill=self._text_color(i))
        if slide_from is not None and len(self.sel) == 1 and slide_from < len(self.pos):
            i = next(iter(self.sel))
            item = self._sel_items[i]
            y0, y1 = self.pos[slide_from][0], self.pos[i][0]
            self.anim.tween(("pill", id(self)), 200,
                            lambda p: self.coords(item, 0, y0 + (y1 - y0) * p)
                            if self.type(item) else None)

    def _set_hover(self, i: int | None) -> None:
        if i == self.hovered:
            return
        old, self.hovered = self.hovered, i
        for j in (old, i):
            if j is not None and j < len(self.text_ids):
                self.itemconfigure(self.text_ids[j], fill=self._text_color(j))
        if self._hover_item is not None:
            self.delete(self._hover_item)
            self._hover_item = None
        if i is not None and i not in self.sel:
            y, h = self.pos[i]
            self._hover_item = self.create_image(0, y, anchor="nw",
                                                 image=self._pill(h, self.c["hover"]))
            self.tag_lower(self._hover_item)

    # ---------- interacción ----------
    def _hit(self, y: float) -> int | None:
        for i, (top, h) in enumerate(self.pos):
            if top <= y < top + h:
                return None if self.items[i].get("hdr") else i
        return None

    def _motion(self, e) -> None:
        i = self._hit(self.canvasy(e.y))
        self.configure(cursor="hand2" if i is not None else "arrow")
        self._set_hover(i)

    def _press(self, e) -> None:
        self.focus_set()
        i = self._hit(self.canvasy(e.y))
        if i is None:
            return
        ctrl, shift = bool(e.state & 0x4) and self.multi, bool(e.state & 0x1) and self.multi
        prev = set(self.sel)
        if ctrl:
            self.sel.symmetric_difference_update({i})
            self.anchor = i
        elif shift and self.anchor is not None:
            lo, hi = sorted((self.anchor, i))
            self.sel = {j for j in range(lo, hi + 1) if not self.items[j].get("hdr")}
        else:
            self.sel, self.anchor = {i}, i
        self._selection_changed(prev)

    def _selection_changed(self, prev: set[int]) -> None:
        if self.hovered in self.sel and self._hover_item is not None:
            self.delete(self._hover_item)
            self._hover_item = None
        self._draw_selection(next(iter(prev)) if len(prev) == 1 else None)
        if self.on_select and prev != self.sel:
            self.on_select()

    def _double(self, e) -> None:
        i = self._hit(self.canvasy(e.y))
        if i is not None and self.on_activate:
            self.on_activate(i)

    def _key(self, d: int) -> str:
        cur = max(self.sel) if d > 0 and self.sel else (min(self.sel) if self.sel else -1)
        j = cur + d
        while 0 <= j < len(self.items) and self.items[j].get("hdr"):
            j += d
        if 0 <= j < len(self.items):
            prev = set(self.sel)
            self.sel, self.anchor = {j}, j
            self._selection_changed(prev)
            self.see(j)
        return "break"

    def see(self, i: int) -> None:
        y, h = self.pos[i]
        view = self.winfo_height()
        if y < self.off:
            self._scroll_to(y - self.pad)
        elif y + h > self.off + view:
            self._scroll_to(y + h - view + self.pad)

    def _wheel(self, e) -> str:
        self._scroll_to(self._target() - e.delta / 120 * 3 * self.rh)
        return "break"

    def _target(self) -> float:
        return getattr(self, "_goal", self.off)

    def _scroll_to(self, y: float) -> None:
        limit = max(0, self.total - self.winfo_height())
        goal = self._goal = max(0.0, min(float(limit), y))
        start = self.off
        self.anim.tween(("scroll", id(self)), 180,
                        lambda p: self._apply_scroll(start + (goal - start) * p))

    def _apply_scroll(self, y: float) -> None:
        limit = max(0, self.total - self.winfo_height())
        self.off = max(0.0, min(float(limit), y))
        self.yview_moveto(self.off / max(1, self.total))
