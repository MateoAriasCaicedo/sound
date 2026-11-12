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

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from ..core.waveform import level_at, resample
from .anim import Animator, ease_in_out, ease_out
from .icons import icon_mask
from .theme import _rgb, blend, readable_on, seed_color

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
        display=tkfont.Font(root, family=pick("Bahnschrift Light", "Segoe UI Light"), size=22),
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


def touchpad_deltas(event) -> tuple[int, int]:
    """Desplazamiento (x, y) de un evento `<TouchpadScroll>` de Tk 9.

    Tk guarda ambos ejes en el campo `delta` como dos enteros de 16 bits con signo:
    x en los 16 bits altos e y en los bajos. El gesto de dos dedos llega muchas veces por
    segundo, así que quien lo use debe desplazarse en píxeles, no de golpe por unidades.
    """
    value = int(getattr(event, "delta", 0)) & 0xFFFFFFFF
    dx, dy = value >> 16, value & 0xFFFF
    if dx >= 0x8000:
        dx -= 0x10000
    if dy >= 0x8000:
        dy -= 0x10000
    return dx, dy


def bind_touchpad(widget, handler, add: bool = False) -> None:
    """Enlaza el gesto de dos dedos si el Tk instalado lo soporta (8.7/9.0)."""
    try:
        widget.bind("<TouchpadScroll>", handler, add="+" if add else None)
    except tk.TclError:
        pass  # Tk antiguo: la rueda normal sigue funcionando


def _layer(mask: Image.Image, color: str) -> Image.Image:
    img = Image.new("RGBA", mask.size, color)
    img.putalpha(mask)
    return img


@lru_cache(maxsize=64)
def _rounded_mask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=r * SS, fill=255)
    return m.resize((w, h), Image.LANCZOS)


@lru_cache(maxsize=64)
def _soft_mask(w: int, h: int, r: int, blur: int) -> Image.Image:
    """Silueta redondeada desenfocada para sombras y halos.

    Se dibuja sin sobremuestreo: el desenfoque ya suaviza el borde y sale más barato en cada
    fotograma de las animaciones.
    """
    pad = max(1, blur * 2)
    m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=r, fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur))


def _tinted(mask: Image.Image, color: str, alpha: float) -> Image.Image:
    img = _layer(mask, color)
    img.putalpha(mask.point(lambda v: round(v * alpha)))
    return img


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


class PointerTip:
    """Texto completo de algo que se ve recortado, junto al puntero y tras una pausa.

    A diferencia de `Tooltip`, no pertenece a un control entero: quien lo usa llama a
    `set(texto)` al pasar sobre cada elemento (una tarjeta, una celda) y `set(None)` al salir.
    """

    def __init__(self, widget: tk.Misc, anim: Animator, colors, delay: int = 550) -> None:
        self.widget, self.anim, self.colors, self.delay = widget, anim, colors, delay
        self.text: str | None = None
        self.win = None
        self.job = None

    def set(self, text: str | None) -> None:
        if text == self.text:
            return
        self.text = text
        self._close()
        if text:
            self.job = self.widget.after(self.delay, self._show)

    def hide(self) -> None:
        self.text = None
        self._close()

    def _close(self) -> None:
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win is not None:
            self.win.destroy()
            self.win = None

    def _show(self) -> None:
        self.job = None
        if not self.text or not self.widget.winfo_ismapped():
            return
        bg, fg = self.colors()
        win = self.win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.0)
        tk.Label(win, text=self.text, bg=bg, fg=fg, padx=10, pady=5, justify="left",
                 font=("Segoe UI", 9), wraplength=420).pack()
        win.update_idletasks()
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 20
        x = min(x, self.widget.winfo_screenwidth() - win.winfo_reqwidth() - 8)
        if y + win.winfo_reqheight() > self.widget.winfo_screenheight() - 8:
            y = self.widget.winfo_pointery() - win.winfo_reqheight() - 12
        win.geometry(f"+{x}+{y}")
        self.anim.tween(("ptip", id(self)), 120, lambda p: win.winfo_exists()
                        and win.attributes("-alpha", p))


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
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", accent="#ffffff")
        self._disc = self._make_disc()
        self._mask = icon_mask(icon, self.g)
        self._painted = _Painted(self, self.d // 2, self.d // 2, "center")
        if command:
            self.bind("<Enter>", lambda e: self._hover(1.0, 110))
            self.bind("<Leave>", lambda e: self._hover(0.0, 170))
            self.bind("<ButtonPress-1>", lambda e: self._press(1.0, 60))
            self.bind("<ButtonRelease-1>", self._release)
            self.bind("<Return>", self._key_fire)
            self.bind("<space>", self._key_fire)
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

    def _key_fire(self, _e=None) -> str:
        """Activa el botón con Enter/Espacio y evita que el atajo global haga otra cosa."""
        if self.command:
            self.command()
        return "break"

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
        if self.active or self.on:  # el punto distingue «activado» aunque el color se parezca
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
        self.c = dict(bg="#000000", accent="#ffffff", on_accent="#000000")
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
                      accent="#ffffff", on_accent="#000000", danger="#ee6a66")
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
            hover = blend(base, c["on_accent"], 0.12)
            text = c["on_accent"]
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



class ChipBar(tk.Canvas):
    """Opciones excluyentes en forma de pastilla (las décadas de la vista, por ejemplo).

    `on_pick(clave)` recibe la clave elegida; volver a pulsar la marcada la desmarca (None).
    """

    def __init__(self, master, anim: Animator, k: float, font, on_pick) -> None:
        self.h = int(28 * k)
        super().__init__(master, height=self.h, width=1, highlightthickness=0, bd=0)
        self.anim, self.k, self.font, self.on_pick = anim, k, font, on_pick
        self.options: list[tuple] = []
        self.selected = None
        self.hover: int | None = None
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888")
        self._spans: list[tuple[int, int]] = []
        self._photos: list = []
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)

    def set_options(self, options: list[tuple], selected=None) -> None:
        if options == self.options and selected == self.selected:
            return
        self.options, self.selected = list(options), selected
        self.hover = None
        self._render()

    def restyle(self, bg: str, fg: str, dim: str) -> None:
        self.c.update(bg=bg, fg=fg, dim=dim)
        self.configure(bg=bg)
        self._render()

    def _render(self) -> None:
        self.delete("all")
        self._spans, self._photos = [], []
        c, h = self.c, self.h
        pad, gap, x = int(11 * self.k), int(4 * self.k), 0
        for i, (key, label) in enumerate(self.options):
            w = self.font.measure(label) + 2 * pad
            chosen = key == self.selected
            fill = blend(c["bg"], c["fg"], 0.15 if chosen else 0.07 if i == self.hover else 0.0)
            if fill != c["bg"]:
                photo = ImageTk.PhotoImage(_layer(_rounded_mask(w, h, h // 2), fill), master=self)
                self._photos.append(photo)
                self.create_image(x, 0, anchor="nw", image=photo)
            self.create_text(x + w // 2, h // 2, text=label, font=self.font,
                             fill=c["fg"] if chosen or i == self.hover else c["dim"])
            self._spans.append((x, x + w))
            x += w + gap
        self.configure(width=max(1, x - gap), cursor="hand2" if self.options else "")

    def _at(self, x: int) -> int | None:
        return next((i for i, (a, b) in enumerate(self._spans) if a <= x <= b), None)

    def _set_hover(self, i: int | None) -> None:
        if i != self.hover:
            self.hover = i
            self._render()

    def _motion(self, e) -> None:
        self._set_hover(self._at(e.x))

    def _click(self, e) -> None:
        i = self._at(e.x)
        if i is None:
            return
        key = self.options[i][0]
        self.on_pick(None if key == self.selected else key)


class SwapText:
    """Un texto en un Canvas: al cambiar, el viejo sube y se apaga mientras el nuevo entra."""

    def __init__(self, canvas: tk.Canvas, anim: Animator, font, x: int, y: int, rise: int,
                 tags: tuple = ()) -> None:
        self.c, self.anim, self.font = canvas, anim, font
        self.x, self.y, self.rise = x, y, rise
        self.tags = tags  # para enlazar eventos al texto aunque se sustituya al cambiar
        self.fg, self.bg = "#ffffff", "#000000"
        self.text = ""
        self.max_w = 0
        self._old = None
        self.item = canvas.create_text(x, y, text="", font=font, anchor="w", tags=tags)

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
                                        anchor="w", fill=self.bg, tags=self.tags)

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


HEART = "#ff7a9a"  # «Me gusta» marcado: el mismo rosado que en las portadas


class NowPlaying(tk.Canvas):
    """Título y artista de la canción que suena, con su corazón de «Me gusta» al lado."""

    def __init__(self, master, anim: Animator, k: float, fonts: dict, on_like=None,
                 on_artist=None) -> None:
        h = int(44 * k)
        super().__init__(master, height=h, width=int(120 * k), highlightthickness=0, bd=0)
        self.anim, self.k, self.on_like, self.on_artist = anim, k, on_like, on_artist
        self.title = SwapText(self, anim, fonts["strong"], 0, int(h * 0.30), int(8 * k))
        self.artist = SwapText(self, anim, fonts["small"], 0, int(h * 0.74), int(6 * k),
                               tags=("artist",))
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888")
        self.artist_link = False  # el artista abre su página si tienes canciones suyas
        self.tip = PointerTip(self, anim, lambda: (blend(self.c["bg"], self.c["fg"], 0.14),
                                                   self.c["fg"]))
        self.tag_bind("artist", "<Enter>", lambda e: self._artist_hover(True))
        self.tag_bind("artist", "<Leave>", lambda e: self._artist_hover(False))
        self.tag_bind("artist", "<ButtonRelease-1>", self._artist_click)
        self.bind("<Enter>", self._show_full, add="+")
        self.bind("<Leave>", lambda e: self.tip.set(None), add="+")
        self.liked: bool | None = None  # None: sin corazón (la canción no está en la biblioteca)
        self.hot = self.pop = 0.0
        self.heart_size = int(18 * k)
        self.heart_box = int(self.heart_size * 1.5)  # hueco para el «latido» al marcarla
        self.heart_gap = int(10 * k)
        self._masks = {name: icon_mask(name, self.heart_size) for name in ("heart", "heart_fill")}
        self._heart = _Painted(self, 0, h // 2, "w")
        self.itemconfigure(self._heart.item, state="hidden")
        self.tag_bind(self._heart.item, "<Enter>", lambda e: self._hover(1.0))
        self.tag_bind(self._heart.item, "<Leave>", lambda e: self._hover(0.0))
        self.tag_bind(self._heart.item, "<ButtonRelease-1>", self._click)
        self.bind("<Configure>", self._resized)

    def _resized(self, e) -> None:
        for line in (self.title, self.artist):
            line.max_w = max(1, e.width - self.heart_gap - self.heart_box)
            line.refit()
        self._place_heart()

    def set(self, title: str, artist: str) -> None:
        self.title.set(title)
        self.artist.set(artist)
        self._place_heart()

    def set_liked(self, liked: bool | None, pop: bool = False) -> None:
        """True/False: corazón lleno o vacío; None: sin corazón. `pop` lo hace latir."""
        changed = liked != self.liked
        self.liked = liked
        if liked is None:
            self.itemconfigure(self._heart.item, state="hidden")
            return
        self.itemconfigure(self._heart.item, state="normal")
        self._place_heart()
        if pop and liked:
            self.anim.tween(("pop", id(self)), 360, self._set_pop, done=lambda: self._set_pop(0.0))
        elif changed or self._heart.photo is None:
            self._render_heart()

    def restyle(self, bg: str, fg: str, dim: str) -> None:
        self.c.update(bg=bg, fg=fg, dim=dim)
        self.configure(bg=bg)
        self.title.recolor(fg, bg)
        self.artist.recolor(dim, bg)
        self._render_heart()

    # ---------- corazón ----------
    def _text_width(self) -> int:
        widths = [line.font.measure(line._fit(line.text)) for line in (self.title, self.artist)]
        return max(widths)

    def _place_heart(self) -> None:
        room = max(0, self.winfo_width() - self.heart_box)
        x = min(room, self._text_width() + self.heart_gap)
        self.coords(self._heart.item, x, self.winfo_reqheight() // 2)

    def _render_heart(self) -> None:
        if self.liked is None:
            return
        c, box = self.c, self.heart_box
        size = max(4, round(self.heart_size * (1 + 0.3 * math.sin(math.pi * self.pop))))
        mask = self._masks["heart_fill" if self.liked else "heart"]
        if size != self.heart_size:
            mask = mask.resize((size, size), Image.LANCZOS)
        if self.liked:
            color = readable_on(HEART, c["bg"], 3.0)  # gráfico, no texto: basta 3:1
        else:
            color = blend(c["dim"], c["fg"], self.hot)
        img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        img.alpha_composite(_layer(mask, color), ((box - size) // 2, (box - size) // 2))
        self._heart.show(img)

    def _set_pop(self, p: float) -> None:
        self.pop = p
        self._render_heart()

    def _hover(self, to: float) -> None:
        self.configure(cursor="hand2" if to else "")
        start = self.hot

        def step(p: float) -> None:
            self.hot = start + (to - start) * p
            self._render_heart()

        self.anim.tween(("hot", id(self)), 120, step)

    def _click(self, _e=None) -> None:
        if self.on_like is not None and self.liked is not None:
            self.on_like()

    # ---------- artista y texto completo ----------
    def set_artist_link(self, on: bool) -> None:
        self.artist_link = on

    def _artist_hover(self, on: bool) -> None:
        on = on and self.artist_link
        self.configure(cursor="hand2" if on else "")
        self.artist.recolor(self.c["fg"] if on else self.c["dim"], self.c["bg"])

    def _artist_click(self, _e=None) -> None:
        if self.artist_link and self.on_artist is not None:
            self.tip.hide()
            self.on_artist()

    def _show_full(self, _e=None) -> None:
        cut = any(line._fit(line.text) != line.text for line in (self.title, self.artist))
        self.tip.set(self.title.text + "\n" + self.artist.text if cut else None)


class Header(tk.Canvas):
    """Cabecera: título grande y línea de estado sobre un degradado teñido por la portada."""

    def __init__(self, master, anim: Animator, k: float, fonts: dict, x: int) -> None:
        # compacta: el estado va en la misma línea que el título, sobre su línea base
        self.h = int(58 * k)
        super().__init__(master, height=self.h, highlightthickness=0, bd=0)
        self.anim, self.pad, self.gap = anim, x, int(16 * k)
        self.colors = ("#000000", "#000000")
        self._painted = _Painted(self)
        display, small = fonts["display"], fonts["small"]
        y = int(26 * k)
        # centro vertical del texto pequeño para que comparta línea base con el título
        baseline = y + (display.metrics("ascent") - display.metrics("descent")) / 2
        sub_y = round(baseline - (small.metrics("ascent") - small.metrics("descent")) / 2)
        self.title = SwapText(self, anim, display, x, y, int(10 * k))
        self.sub = SwapText(self, anim, small, x, sub_y, 0)
        self._dim = self._fg = "#888888"
        self.bind("<Configure>", self._resized)

    def _resized(self, e) -> None:
        self.title.max_w = max(1, int((e.width - self.pad) * 0.6))
        self.title.refit()
        self._place_sub()
        self._paint()

    def _place_sub(self) -> None:
        """El estado empieza donde acaba el título y ocupa el resto de la línea."""
        x = self.pad + self.title.font.measure(self.title._fit(self.title.text)) + self.gap
        self.sub.x = x
        self.coords(self.sub.item, x, self.sub.y)
        self.sub.max_w = max(1, self.winfo_width() - x - 16)
        self.sub.refit()

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
        self._place_sub()

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
    """Buscador con sombra suave, icono y atajo visible.

    Al enfocarlo se ensancha, el borde toma el acento y aparece un halo; el icono se tiñe. Vacío y
    sin foco muestra «Ctrl F»; con texto, un botón para borrar.
    """

    def __init__(self, master, anim: Animator, k: float, font, variable: tk.StringVar, hint: str,
                 rest: int = 340, focused: int = 450) -> None:
        self.fh = int(38 * k)  # alto de la píldora; el resto del lienzo es aire para la sombra
        self.px = self.py = int(6 * k)
        self.rest, self.wide = int(rest * k), int(focused * k)
        self.w = self.rest
        self.h = self.fh + 2 * self.py
        super().__init__(master, width=self.w, height=self.h, highlightthickness=0, bd=0,
                         cursor="xterm")
        self.anim, self.k, self.var, self.font = anim, k, variable, font
        self.p = self.hover = 0.0
        self._focused = False
        self._clear_hover = False
        self.c = dict(bg="#000000", field="#111111", fg="#ffffff", dim="#888888", faint="#555555",
                      line="#333333", accent="#ffffff")
        self._pill = _Painted(self)
        self._icon_mask = icon_mask("search", int(16 * k))
        self._icon = _Painted(self, int(21 * k), self.h // 2, "center")
        # atajo de teclado, como una tecla dibujada a la derecha
        self._key_font = tkfont.Font(self, family=font.cget("family"), size=8)
        self._key_w = self._key_font.measure("Ctrl F") + int(10 * k)
        self._key_h = int(17 * k)
        self._key_bg = _Painted(self, 0, self.h // 2, "center")
        self._key_label = self.create_text(0, self.h // 2, text="Ctrl F", font=self._key_font,
                                           state="hidden")
        self.entry = tk.Entry(self, textvariable=variable, font=font, bd=0, relief="flat",
                              highlightthickness=0)
        self._win = self.create_window(int(36 * k), self.h // 2, anchor="w", window=self.entry,
                                       height=int(24 * k))
        # dentro del Entry: un control incrustado siempre tapa el texto del Canvas
        self._hint = tk.Label(self.entry, text=hint, font=font, bd=0, padx=0, pady=0, cursor="xterm")
        self._hint.place(x=0, rely=0.5, anchor="w")
        self._hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        # borrar: disco invisible en reposo y resaltado al pasar el ratón
        self._clear_bg = self.create_oval(0, 0, 0, 0, fill=self.c["field"], outline="",
                                          tags="clear")
        self._clear_lines = [self.create_line(0, 0, 0, 0, fill=self.c["dim"],
                                              width=max(1, round(1.5 * k)), capstyle="round",
                                              tags="clear") for _ in range(2)]
        self.tag_bind("clear", "<Button-1>", lambda e: self.clear())
        self.tag_bind("clear", "<Enter>", lambda e: self._clear_in(True))
        self.tag_bind("clear", "<Leave>", lambda e: self._clear_in(False))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", lambda e: self._hover(0.0))
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        self.entry.bind("<Enter>", lambda e: self._hover(1.0))
        self.entry.bind("<Leave>", lambda e: self._hover(0.0))
        self._hint.bind("<Enter>", lambda e: self._hover(1.0))
        self._hint.bind("<Leave>", lambda e: self._hover(0.0))
        self.entry.bind("<Escape>", lambda e: self.clear())
        variable.trace_add("write", lambda *a: self._sync())
        self._sync()

    def clear(self) -> None:
        self.var.set("")
        self.entry.focus_set()

    def _sync(self) -> None:
        has = bool(self.var.get())
        if has:
            self._hint.place_forget()
        else:
            self._hint.place(x=0, rely=0.5, anchor="w")
        self._grow(1.0 if has or self._focused else 0.0)

    def _grow(self, to: float) -> None:
        if to == 0.0 and (self.var.get() or self._focused):
            to = 1.0
        start = self.p
        if start == to:
            self._render()
            return

        def step(q: float) -> None:
            self.p = start + (to - start) * q
            self._render()

        self.anim.tween(("grow", id(self)), 220, step)

    def _hover(self, to: float) -> None:
        start = self.hover
        if start == to:
            return

        def step(q: float) -> None:
            self.hover = start + (to - start) * q
            self._render()

        self.anim.tween(("hover", id(self)), 120 if to else 180, step)

    def _focus(self, active: bool) -> None:
        self._focused = active
        self._grow(1.0 if active or self.var.get() else 0.0)

    def _clear_in(self, inside: bool) -> None:
        self._clear_hover = inside
        self.configure(cursor="hand2" if inside else "xterm")
        self._render()

    def _paint_keycap(self) -> None:
        c = self.c
        w, h = max(2, self._key_w), max(2, self._key_h)
        r, bw = int(5 * self.k), max(1, round(self.k))
        outer = _rounded_mask(w, h, r)
        inner = _rounded_mask(max(1, w - 2 * bw), max(1, h - 2 * bw), max(1, r - bw))
        img = _layer(outer, c["line"])
        img.alpha_composite(_layer(inner, blend(c["field"], c["fg"], 0.03)), (bw, bw))
        self._key_bg.show(img)
        self.itemconfigure(self._key_label, fill=c["dim"])

    def _layout(self, show_key: bool) -> None:
        cy = self.h // 2
        pill_w = self.w - 2 * self.px
        right = self._key_w + int(20 * self.k) if show_key else int(34 * self.k)
        self.itemconfigure(self._win, width=max(1, self.w - 2 * self.px - int(36 * self.k) - right))
        # borrar
        cx, r = self.px + pill_w - int(20 * self.k), int(10 * self.k)
        self.coords(self._clear_bg, cx - r, cy - r, cx + r, cy + r)
        g = int(4 * self.k)
        self.coords(self._clear_lines[0], cx - g, cy - g, cx + g, cy + g)
        self.coords(self._clear_lines[1], cx - g, cy + g, cx + g, cy - g)
        # atajo
        kx = self.px + pill_w - int(10 * self.k) - self._key_w / 2
        self.coords(self._key_bg.item, kx, cy)
        self.coords(self._key_label, kx, cy)

    def restyle(self, **colors) -> None:
        """La píldora no cambia con el tinte de la ventana salvo el borde cuando está enfocada."""
        before = dict(self.c)
        self.c.update(colors)
        c = self.c
        self.configure(bg=c["bg"])
        solid = ("field", "fg", "dim", "faint", "line")
        if any(before[key] != c[key] for key in solid) or self._pill.photo is None:
            self.entry.configure(bg=c["field"], fg=c["fg"], selectforeground=c["fg"],
                                 insertwidth=max(1, int(1.5 * self.k)))
            self._hint.configure(bg=c["field"], fg=c["dim"])
            self._paint_keycap()
            self._render()
        elif self.p > 0.01 and before["accent"] != c["accent"]:
            self._render()
        self.entry.configure(insertbackground=c["accent"],
                             selectbackground=blend(c["field"], c["accent"], 0.30))

    def _render(self) -> None:
        c, k = self.c, self.k
        w = int(self.rest + (self.wide - self.rest) * self.p)
        if w != self.w:
            self.w = w
            self.configure(width=w)
        if self._key_bg.photo is None:
            self._paint_keycap()

        has = bool(self.var.get())
        show_key = not has and not self._focused
        for item in (self._key_bg.item, self._key_label):
            self.itemconfigure(item, state="normal" if show_key else "hidden")
        for item in (self._clear_bg, *self._clear_lines):
            self.itemconfigure(item, state="normal" if has else "hidden")
        self._layout(show_key)

        pill_w, fh, r = w - 2 * self.px, self.fh, self.fh // 2
        bw = max(1, round(k))
        img = Image.new("RGBA", (w, self.h), (0, 0, 0, 0))
        # sombra suave, un par de píxeles por debajo
        blur = max(1, int(2 * k))
        shadow = _soft_mask(pill_w, fh, r, blur)
        pad = blur * 2
        img.alpha_composite(_tinted(shadow, "#000000", 0.16),
                            (self.px - pad, self.py - pad + max(1, int(1.5 * k))))
        # halo del acento al enfocar
        if self.p > 0.02:
            gp = max(2, int(3 * k))
            glow = _soft_mask(pill_w, fh, r, gp)
            img.alpha_composite(_tinted(glow, c["accent"], 0.18 * self.p),
                                (self.px - gp * 2, self.py - gp * 2))
        # cuerpo: borde fino y relleno sólido
        outer = _rounded_mask(pill_w, fh, r)
        inner = _rounded_mask(max(1, pill_w - 2 * bw), max(1, fh - 2 * bw), max(1, r - bw))
        edge = blend(blend(c["line"], c["fg"], 0.16 * self.hover), c["accent"], self.p)
        body = _layer(outer, edge)
        body.alpha_composite(_layer(inner, c["field"]), (bw, bw))
        img.alpha_composite(body, (self.px, self.py))
        self._pill.show(img)

        icon = blend(blend(c["dim"], c["fg"], 0.45 * self.hover), c["accent"], self.p)
        self._icon.show(_layer(self._icon_mask, icon))
        self.itemconfigure(self._clear_bg,
                           fill=blend(c["field"], c["fg"], 0.10 if self._clear_hover else 0.0))
        xfg = blend(c["dim"], c["fg"], 0.70 if self._clear_hover else 0.10)
        for line in self._clear_lines:
            self.itemconfigure(line, fill=xfg)


class Field(tk.Canvas):
    """Campo redondeado con borde fino, pista y acento al enfocar.

    Sustituye a `tk.Entry`/`tk.Spinbox`: no dibuja las flechas nativas y comparte la paleta y la
    tipografía de la ventana. `get`/`set`, `configure(state=...)` y `.entry` funcionan como en el
    control de Tk al que sustituye.
    """

    def __init__(self, master, anim: Animator, k: float, font, variable: tk.StringVar,
                 hint: str = "", width: int = 120, height: int = 40, align: str = "left",
                 digits: int | None = None) -> None:
        self.h = int(height * k)
        self.w = int(width * k)
        self.radius = int(10 * k)
        self.pad = int(12 * k)
        super().__init__(master, width=self.w, height=self.h, highlightthickness=0, bd=0,
                         cursor="xterm")
        self.anim, self.k, self.var, self.font = anim, k, variable, font
        self.hint, self.align = hint, align
        self.enabled = True
        self.p = self.hover = 0.0
        self.c = dict(bg="#000000", field="#111111", panel="#181818", fg="#ffffff", dim="#888888",
                      line="#333333", accent="#ffffff")
        self._painted = _Painted(self)
        valid = None
        if digits:
            valid = (self.register(lambda text: text == ""
                                   or (text.isdigit() and len(text) <= digits)), "%P")
        self.entry = tk.Entry(self, textvariable=variable, font=font, bd=0, relief="flat",
                              highlightthickness=0, justify=align,
                              validate="key" if valid else "none", validatecommand=valid)
        self._win = self.create_window(self.pad, self.h // 2, anchor="w", window=self.entry,
                                       height=int(24 * k), width=self.w - 2 * self.pad)
        # dentro del Entry: un control incrustado siempre tapa el texto del Canvas
        self._hint = tk.Label(self.entry, text=hint, font=font, bd=0, padx=0, pady=0,
                              cursor="xterm")
        self._hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.bind("<Configure>", self._resized)
        self.bind("<Enter>", lambda e: self._animate("hover", 1.0, 110))
        self.bind("<Leave>", lambda e: self._animate("hover", 0.0, 170))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: self._animate("p", 1.0, 140))
        self.entry.bind("<FocusOut>", lambda e: self._animate("p", 0.0, 200))
        self.entry.bind("<Enter>", lambda e: self._animate("hover", 1.0, 110))
        self.entry.bind("<Leave>", lambda e: self._animate("hover", 0.0, 170))
        variable.trace_add("write", lambda *a: self._sync())
        self._sync()

    # ---------- API parecida a tk.Entry ----------
    def configure(self, cnf=None, **kw):
        state = kw.pop("state", None)
        if state is not None:
            self.set_enabled(state != "disabled")
        if cnf is not None or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def get(self) -> str:
        return self.var.get()

    def set(self, value) -> None:
        self.var.set(str(value))

    def focus_set(self) -> None:
        self.entry.focus_set()

    def set_enabled(self, on: bool) -> None:
        if on == self.enabled:
            return
        self.enabled = on
        self.entry.configure(state="normal" if on else "disabled",
                             cursor="xterm" if on else "arrow")
        self.configure(cursor="xterm" if on else "arrow")
        self._hint.configure(bg=self.c["field"] if on else self.c["panel"])
        self._paint()

    # ---------- dibujo ----------
    def _resized(self, e) -> None:
        self.itemconfigure(self._win, width=max(1, e.width - 2 * self.pad))
        self._paint()

    def _sync(self) -> None:
        if self.var.get():
            self._hint.place_forget()
        elif self.align == "center":
            self._hint.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self._hint.place(x=0, rely=0.5, anchor="w")

    def _animate(self, attr: str, to: float, ms: int) -> None:
        start = getattr(self, attr)
        if start == to:
            return

        def step(q: float) -> None:
            setattr(self, attr, start + (to - start) * q)
            self._paint()

        self.anim.tween((attr, id(self)), ms, step)

    def _paint(self) -> None:
        c = self.c
        w, h = max(2, self.winfo_width()), self.h
        r = max(1, min(self.radius, w // 2, h // 2))
        bw = max(1, round(self.k))
        edge = blend(blend(c["line"], c["fg"], 0.10 * self.hover), c["accent"], self.p)
        outer = _rounded_mask(w, h, r)
        inner = _rounded_mask(max(1, w - 2 * bw), max(1, h - 2 * bw), max(1, r - bw))
        img = _layer(outer, edge)
        img.alpha_composite(_layer(inner, c["field"] if self.enabled else c["panel"]), (bw, bw))
        self._painted.show(img)

    def restyle(self, **colors) -> None:
        before = dict(self.c)
        self.c.update(colors)
        c = self.c
        self.configure(bg=c["bg"])
        if any(before[key] != c[key] for key in ("field", "panel", "fg", "dim", "line")) \
                or self._painted.photo is None:
            self.entry.configure(bg=c["field"], fg=c["fg"], disabledbackground=c["panel"],
                                 disabledforeground=c["dim"], selectforeground=c["fg"],
                                 insertwidth=max(1, int(1.5 * self.k)))
            self._hint.configure(bg=c["field"] if self.enabled else c["panel"], fg=c["dim"])
        self.entry.configure(insertbackground=c["accent"],
                             selectbackground=blend(c["field"], c["accent"], 0.32))
        self._paint()


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
        self.c = dict(bg="#000000", rest="#333333", accent="#ffffff")
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

    def set_length(self, length: int) -> None:
        """Otro largo de carril (con la ventana estrecha se acorta)."""
        w = int(length * self.k)
        if w != self.w:
            self.w = w
            self.configure(width=w)
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

    def __init__(self, master, anim: Animator, k: float, playing, level=None) -> None:
        self.size = int(16 * k)
        super().__init__(master, width=self.size, height=self.size, highlightthickness=0, bd=0)
        self.anim, self.playing, self.level = anim, playing, level
        self.color = "#ffffff"
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
        pump = 0.0
        if now is not None and self.level is not None:
            try:
                pump = max(0.0, min(1.0, float(self.level())))
            except Exception:  # noqa: BLE001 - una lectura rota no debe parar la animación
                pump = 0.0
        for i in range(3):
            level = (0.15 + (0.35 + 0.45 * pump) * (0.5 + 0.5 * math.sin(
                now * (6.3 + i * 1.7) + i * 2.1))) if now is not None else 0.3
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
        self.pulse = 0.0  # nivel real bajo el cabezal, con subida rápida y bajada lenta
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
        self.c = dict(bg="#000000", rest="#333333", ahead="#555555", accent="#ffffff", fg="#ffffff")
        # cabezal: una línea fina con un punto arriba; deja claro dónde va la canción
        self._head = self.create_line(0, 0, 0, 0, width=max(2, round(1.5 * k)), state="hidden",
                                      capstyle="round")
        self._knob = self.create_oval(0, 0, 0, 0, width=0, state="hidden")
        self.knob = 0.0  # 0: punto pequeño; 1: grande (ratón encima o arrastrando)
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
            for item in (self._head, self._knob):
                self.itemconfigure(item, state="normal")
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
        for item in (self._head, self._knob, self._label):  # por encima de las barras nuevas
            self.tag_raise(item)
        self._painted_key = (-1, -1)
        self._dirty = self._full = True
        self.anim.wake()

    def restyle(self, bg: str, rest: str, ahead: str, accent: str, fg: str) -> None:
        self.c.update(bg=bg, rest=rest, ahead=ahead, accent=accent, fg=fg)
        self.configure(bg=bg)
        self.itemconfigure(self._label, fill=fg)
        self.itemconfigure(self._head, fill=fg)
        self.itemconfigure(self._knob, fill=fg)
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
        pulse_target = level_at(self.levels, self.shown / 1000) if self.energy > 0 else 0.0
        if abs(self.pulse - pulse_target) > 0.002:
            rate = 0.35 if pulse_target > self.pulse else 0.07  # ataque rápido, caída lenta
            self.pulse += (pulse_target - self.pulse) * rate
            moving = True
        else:
            self.pulse = pulse_target
        gap = self.value - self.shown
        if abs(gap) > 0.3 and not reduced:
            self.shown += gap * 0.3
            moving = True
        else:
            self.shown = self.value
        knob = 1.0 if self.hover_x is not None or self.dragging else 0.0
        if abs(self.knob - knob) > 0.02 and not reduced:
            self.knob += (knob - self.knob) * 0.35
            moving = True
        elif self.knob != knob:
            self.knob = knob
            self._dirty = True
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
                # sin niveles reales se mantiene el rizo decorativo de siempre; con ellos, el
                # cabezal crece en los pasajes fuertes y se calma en los suaves
                body = 0.10 + 0.36 * self.pulse if self.levels else 0.24
                level *= 1 + energy * exp(-near * near) * (
                    body + 0.12 * sin(now * 6 + self.phase[i]))
            h = max(low, min(float(H), level * H))
            if abs(h - self.last[i]) >= 0.5:
                self.last[i] = h
                x = i * step
                self.coords(self.bars[i], x, floor - h, x + bw, floor)
        self._recolor(int(head), n)
        x = self.shown / 1000 * max(1, self.winfo_width())
        top = floor - H - 3 * self.k
        self.coords(self._head, x, top, x, floor)
        r = (2.5 + 2.5 * self.knob) * self.k
        self.coords(self._knob, x - r, top - r, x + r, top + r)

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
                 on_activate=None, multi: bool = True, row: int = 30, on_move=None,
                 on_context=None) -> None:
        super().__init__(master, highlightthickness=0, bd=0, takefocus=1)
        self.anim, self.k, self.fonts = anim, k, fonts
        self.on_select, self.on_activate, self.multi = on_select, on_activate, multi
        self.on_move, self.on_context = on_move, on_context
        self.rh, self.hh, self.gap, self.pad = int(row * k), int(24 * k), int(14 * k), int(12 * k)
        self.items: list[dict] = []
        self.pos: list[tuple[int, int]] = []
        self.text_ids: list[int] = []
        self.sub_ids: list[int | None] = []
        self.sel: set[int] = set()
        self.anchor: int | None = None
        self.hovered: int | None = None
        self.off = 0.0
        self.total = 1
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555", sel="#333333",
                      hover="#222222", accent="#ffffff")
        self._pills: dict[tuple, ImageTk.PhotoImage] = {}
        self._sel_items: dict[int, int] = {}
        self._hover_item = None
        self._width = 0
        self._drag_from: int | None = None  # fila que se está arrastrando
        self._drag_sec = None                # sección de esa fila (solo se mueve dentro de ella)
        self._press_y = 0
        self._dragging = False
        self._drop_line = None
        self._full: dict[int, str] = {}  # filas recortadas: su texto completo se ve al pasar
        self.tip = PointerTip(self, anim, lambda: (blend(self.c["bg"], self.c["fg"], 0.14),
                                                   self.c["fg"]))
        self.bind("<Configure>", self._resized)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<MouseWheel>", self._wheel)
        bind_touchpad(self, self._touchpad)
        self.bind("<Up>", lambda e: self._key(-1))
        self.bind("<Down>", lambda e: self._key(1))
        if on_move is not None:
            self.bind("<B1-Motion>", self._drag_motion)
            self.bind("<ButtonRelease-1>", self._drag_release)
        if on_context is not None:
            self.bind("<Button-3>", self._context)

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

    def _sub_color(self, i: int) -> str:
        return self.c["dim"] if i in self.sel or i == self.hovered else self.c["faint"]

    def _layout(self) -> None:
        self.delete("all")
        self._sel_items.clear()
        self._hover_item = None
        self.pos, self.text_ids, self.sub_ids = [], [], []
        self._full = {}
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
            sub_text = None if hdr else it.get("sub")
            if elide(font, it["text"], room) != it["text"] or (
                    sub_text and elide(self.fonts["small"], sub_text, room) != sub_text):
                self._full[i] = it["text"] + ("\n" + sub_text if sub_text else "")
            if sub_text:
                self.text_ids.append(self.create_text(
                    pad, y + h * 0.32, anchor="w", text=elide(font, it["text"], room),
                    font=font, fill=self._text_color(i)))
                self.sub_ids.append(self.create_text(
                    pad, y + h * 0.70, anchor="w",
                    text=elide(self.fonts["small"], sub_text, room),
                    font=self.fonts["small"], fill=self._sub_color(i)))
            else:
                self.text_ids.append(self.create_text(
                    pad, y + h // 2, anchor="w", text=elide(font, it["text"], room), font=font,
                    fill=self._text_color(i)))
                self.sub_ids.append(None)
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
            if i < len(self.sub_ids) and self.sub_ids[i] is not None:
                self.itemconfigure(self.sub_ids[i], fill=self._sub_color(i))
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
                if j < len(self.sub_ids) and self.sub_ids[j] is not None:
                    self.itemconfigure(self.sub_ids[j], fill=self._sub_color(j))
        if self._hover_item is not None:
            self.delete(self._hover_item)
            self._hover_item = None
        if i is not None and i not in self.sel:
            y, h = self.pos[i]
            self._hover_item = self.create_image(0, y, anchor="nw",
                                                 image=self._pill(h, self.c["hover"]))
            self.tag_lower(self._hover_item)
        self.tip.set(self._full.get(i) if i is not None else None)

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
        self.tip.hide()
        self.focus_set()
        i = self._hit(self.canvasy(e.y))
        self._press_y = e.y
        self._drag_from = i if i is not None and self.items[i].get("move") else None
        self._drag_sec = self.items[i].get("move") if self._drag_from is not None else None
        self._dragging = False
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

    # ---------- arrastrar para ordenar ----------
    def _movers(self) -> list[int]:
        """Filas de la misma sección que la que se arrastra (sin encabezados ni la actual)."""
        if self._drag_sec is None:
            return []
        return [i for i, it in enumerate(self.items) if it.get("move") == self._drag_sec]

    def _move_slot(self, y: float) -> int:
        """Dónde se soltaría: índice de inserción dentro de la sección que se arrastra."""
        cy = self.canvasy(y)
        movers = self._movers()
        for k, i in enumerate(movers):
            top, h = self.pos[i]
            if cy < top + h / 2:
                return k
        return len(movers)

    def _drag_motion(self, e) -> None:
        if self._drag_from is None:
            return
        if not self._dragging and abs(e.y - self._press_y) < int(5 * self.k):
            return
        self._dragging = True
        self._show_drop_line(self._move_slot(e.y))
        if e.y < int(22 * self.k):  # arrastrar cerca del borde desplaza la lista
            self._scroll_to(self._target() - self.rh)
        elif e.y > self.winfo_height() - int(22 * self.k):
            self._scroll_to(self._target() + self.rh)

    def _show_drop_line(self, k: int) -> None:
        if self._drop_line is not None:
            self.delete(self._drop_line)
            self._drop_line = None
        movers = self._movers()
        if not movers:
            return
        if k < len(movers):
            y = self.pos[movers[k]][0]
        else:
            top, h = self.pos[movers[-1]]
            y = top + h
        width = self._width or self.winfo_reqwidth()
        self._drop_line = self.create_line(
            self.pad, y, max(self.pad + 1, width - self.pad), y,
            fill=self.c["accent"], width=max(1, int(2 * self.k)))
        self.tag_raise(self._drop_line)

    def _drag_release(self, e) -> None:
        if self._dragging and self._drag_from is not None:
            src, k = self._drag_from, self._move_slot(e.y)
            self._end_drag()
            if self.on_move is not None:
                self.on_move(src, k)
        else:
            self._end_drag()

    def _end_drag(self) -> None:
        if self._drop_line is not None:
            self.delete(self._drop_line)
            self._drop_line = None
        self._drag_from = None
        self._drag_sec = None
        self._dragging = False

    # ---------- menú contextual ----------
    def _context(self, e) -> str:
        i = self._hit(self.canvasy(e.y))
        if i is None:
            return "break"
        prev = set(self.sel)
        self.sel, self.anchor = {i}, i
        self._selection_changed(prev)
        if self.on_context is not None:
            self.on_context(i, e.x_root, e.y_root)
        return "break"

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

    def _touchpad(self, e) -> str:
        """Gesto de dos dedos: en Tk 9 llega aparte y en píxeles (no en «clics» de rueda)."""
        _dx, dy = touchpad_deltas(e)
        if dy:
            limit = max(0, self.total - self.winfo_height())
            self.off = self._goal = max(0.0, min(float(limit), self.off - dy))
            self.yview_moveto(self.off / max(1, self.total))
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
