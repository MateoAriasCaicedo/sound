"""Aviso flotante con una acción opcional («Deshacer»), en lugar de un cuadro de diálogo.

Hay un solo aviso a la vez: el nuevo sustituye al anterior. Si el anterior dejaba algo pendiente
(por ejemplo, un borrado que espera por si se deshace), se confirma antes de mostrar el nuevo.
Desaparece solo al cabo de unos segundos; con el ratón encima, espera.
"""
import tkinter as tk

from PIL import Image

from .theme import blend, readable_on
from .widgets import _layer, _Painted, _rounded_mask, elide

SHOW_MS = 3200     # aviso sin acción
ACTION_MS = 6500   # con «Deshacer»: da tiempo a leerlo y reaccionar
LINGER_MS = 1800   # tras sacar el ratón de encima
SLIDE_MS = 200


class Toast(tk.Canvas):
    """`area` es el control sobre el que flota (por defecto, `master`) y `below`, el que debe
    quedar por encima de él: así sube desde detrás del reproductor y no lo tapa al entrar."""

    def __init__(self, master, anim, k: float, fonts: dict, area=None, below=None) -> None:
        self.k, self.anim, self.fonts = k, anim, fonts
        self.area = area if area is not None else master
        self.below = below
        self.h = int(44 * k)
        self.pad = int(18 * k)
        self.gap = int(22 * k)
        self.margin = int(16 * k)
        super().__init__(master, height=self.h, width=int(200 * k), highlightthickness=0, bd=0)
        self.c = dict(bg="#000000", surface="#ffffff", fg="#000000", accent="#000000")
        self._painted = _Painted(self)
        self._text = self.create_text(0, self.h // 2, anchor="w", font=fonts["body"])
        self._action = self.create_text(0, self.h // 2, anchor="w", font=fonts["strong"])
        self.message = ""
        self.action = ""
        self._on_action = None
        self._on_expire = None
        self._job = None
        self._width = 0
        self.shown = 0.0    # 0: fuera de la vista; 1: a la vista
        self.visible = False
        self.bind("<Enter>", self._pause)
        self.bind("<Leave>", self._resume)
        self.bind("<Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._click)

    # ---------- API ----------
    def show(self, text: str, action: str = "", on_action=None, on_expire=None) -> None:
        """Muestra `text`. `on_action` corre si se pulsa la acción; `on_expire`, si no."""
        self._settle(commit=True)  # lo pendiente del aviso anterior se da por bueno
        self.message, self.action = text, (action if on_action else "")
        self._on_action, self._on_expire = on_action, on_expire
        self._layout()
        self._slide(1.0)
        self._schedule(ACTION_MS if self.action else SHOW_MS)

    def has_action(self) -> bool:
        return self.visible and self._on_action is not None

    def act(self) -> bool:
        """Ejecuta la acción del aviso visible (Ctrl+Z). False si no había ninguna."""
        callback = self._on_action
        if callback is None or not self.visible:
            return False
        self._on_action = self._on_expire = None
        self._cancel()
        self._slide(0.0)
        callback()
        return True

    def hide(self) -> None:
        self._settle(commit=True)
        self._slide(0.0)

    def flush(self) -> None:
        """Confirma ya lo pendiente (al cerrar la ventana), sin animar nada."""
        self._settle(commit=True)

    def restyle(self, bg: str, surface: str, fg: str, accent: str) -> None:
        self.c.update(bg=bg, surface=surface, fg=fg, accent=accent)
        self.configure(bg=bg)
        if self._width:
            self._render()

    # ---------- dibujo ----------
    def restack(self) -> None:
        """Por encima de todo lo de la ventana salvo `below` (lo llama quien se le pone encima)."""
        if self.below is not None:
            self.tk.call("lower", self._w, self.below._w)
        else:
            self.tk.call("raise", self._w)  # Canvas.lift() eleva elementos, no el widget

    def _max_width(self) -> int:
        parent = self.area.winfo_width()
        return max(int(240 * self.k), min(int(620 * self.k), parent - 2 * self.margin))

    def _layout(self) -> None:
        body, strong = self.fonts["body"], self.fonts["strong"]
        action_w = strong.measure(self.action) if self.action else 0
        extra = self.gap + action_w if self.action else 0
        room = self._max_width() - 2 * self.pad - extra
        text = elide(body, self.message, room)
        self._width = 2 * self.pad + body.measure(text) + extra
        self.configure(width=self._width)
        self.itemconfigure(self._text, text=text)
        self.coords(self._text, self.pad, self.h // 2)
        self.itemconfigure(self._action, text=self.action)
        self.coords(self._action, self._width - self.pad - action_w, self.h // 2)
        self._render()

    def _render(self, hot: bool = False) -> None:
        c, w, h = self.c, self._width, self.h
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # radio pequeño: Tk no tiene transparencia entre controles y unas esquinas muy redondas
        # dejarían ver el fondo de la ventana encima de las portadas
        img.alpha_composite(_layer(_rounded_mask(w, h, max(2, int(5 * self.k))), c["surface"]))
        self._painted.show(img)
        self.tag_lower(self._painted.item)
        self.itemconfigure(self._text, fill=c["fg"])
        accent = readable_on(c["accent"], c["surface"])
        self.itemconfigure(self._action, fill=blend(accent, c["fg"], 0.35) if hot else accent)

    # ---------- tiempo y animación ----------
    def _schedule(self, ms: int) -> None:
        self._cancel()
        self._job = self.after(ms, self.hide)

    def _cancel(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _settle(self, commit: bool) -> None:
        self._cancel()
        expire = self._on_expire
        self._on_action = self._on_expire = None
        if commit and expire is not None:
            expire()

    def _slide(self, to: float) -> None:
        if to > 0 and not self.visible:
            self.visible = True
            self.place(in_=self.area, relx=0.5, rely=1.0, anchor="n", y=0)
            self.restack()
        start = self.shown

        def step(p: float) -> None:
            self.shown = start + (to - start) * p
            self.place_configure(y=-round((self.h + self.margin) * self.shown))

        def done() -> None:
            if to <= 0:
                self.visible = False
                self.place_forget()

        self.anim.tween(("toast", id(self)), SLIDE_MS, step, done=done)

    # ---------- ratón ----------
    def _on_action_text(self, x: int) -> bool:
        if not self.action:
            return False
        x0, _y0, x1, _y1 = self.bbox(self._action) or (0, 0, 0, 0)
        return x0 - self.gap // 2 <= x <= x1 + self.pad // 2

    def _motion(self, e) -> None:
        hot = self._on_action_text(e.x)
        self.configure(cursor="hand2" if hot else "arrow")
        self._render(hot)

    def _pause(self, _e=None) -> None:
        self._cancel()

    def _resume(self, _e=None) -> None:
        self._render()
        if self.visible and self.shown > 0:
            self._schedule(LINGER_MS)

    def _click(self, e) -> None:
        if self._on_action_text(e.x):
            self.act()
        else:  # un clic en el texto lo despide
            self.hide()
