"""Mini reproductor: una ventana pequeña y siempre encima con lo justo para escuchar.

Portada, título y artista (con su corazón), anterior / reproducir / siguiente, un botón para
volver a la ventana completa y una línea de progreso. No tiene estado propio: copia el de la
ventana principal en `sync()`, que esta llama en su tick y al cambiar de canción o de colores.
"""
import tkinter as tk

from .theme import blend
from .widgets import Cover, IconButton, NowPlaying, PlayButton

WIDTH, HEIGHT = 470, 84  # a 96 ppp


class MiniPlayer(tk.Toplevel):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        k = self.k = app.k
        self.withdraw()
        self.title("Sound")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", app.exit_mini)
        self.geometry(f"{int(WIDTH * k)}x{int(HEIGHT * k)}")
        self._colors: tuple | None = None
        self._progress = -1.0

        body = tk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        self.body = body
        self.cover = Cover(body, app.anim, k, size=56)
        self.cover.grid(row=0, column=0, padx=(int(12 * k), int(10 * k)), pady=int(10 * k))
        self.now = NowPlaying(body, app.anim, k, app.fonts, on_like=app.like_current)
        self.now.grid(row=0, column=1, sticky="ew")
        controls = tk.Frame(body)
        controls.grid(row=0, column=2, padx=(int(4 * k), int(8 * k)))
        self.controls = controls
        self.btn_prev = IconButton(controls, app.anim, k, "prev", lambda: app.step(-1),
                                   tip="Anterior", size=32, glyph=18)
        self.btn_play = PlayButton(controls, app.anim, k, app.play_pause, busy=app._opening,
                                   size=42)
        self.btn_next = IconButton(controls, app.anim, k, "next", lambda: app.step(1),
                                   tip="Siguiente", size=32, glyph=18)
        self.btn_expand = IconButton(controls, app.anim, k, "expand", app.exit_mini,
                                     tip="Volver a la ventana completa (Esc)", size=32, glyph=16)
        for w in (self.btn_prev, self.btn_play, self.btn_next):
            w.pack(side="left", padx=int(1 * k))
        self.btn_expand.pack(side="left", padx=(int(6 * k), 0))
        self.bar = tk.Canvas(self, height=max(2, int(3 * k)), highlightthickness=0, bd=0)
        self.bar.pack(fill="x", side="bottom")
        self._bar_item = self.bar.create_rectangle(0, 0, 0, 0, width=0)

        self.bind("<space>", lambda e: (app.play_pause(), "break")[1])
        self.bind("<Escape>", lambda e: app.exit_mini())
        self.bind("<Control-l>", lambda e: (app.like_current(), "break")[1])
        self.bind("<Control-M>", lambda e: (app.exit_mini(), "break")[1])  # Ctrl+Mayús+M

    # ---------- copia del estado de la ventana ----------
    def sync(self) -> None:
        app = self.app
        t = app.cur_track
        self.now.set(t["title"] if t else "Sound", (t.get("artist") or "") if t else "")
        self.now.set_liked(app.now.liked)
        self.btn_play.set_playing(app.btn_play.playing)
        self.restyle()
        try:
            progress = max(0.0, min(1.0, app.scale.get() / 1000))
        except (tk.TclError, ValueError, ZeroDivisionError):
            progress = 0.0
        if abs(progress - self._progress) > 0.001:
            self._progress = progress
            width = self.bar.winfo_width()
            self.bar.coords(self._bar_item, 0, 0, round(width * progress), self.bar.winfo_height())

    def show_cover(self, img, seed) -> None:
        self.cover.show(img, seed)

    def restyle(self) -> None:
        """Los colores de la barra del reproductor de la ventana (teñidos por la canción)."""
        app = self.app
        p, c = app.pal, app.scheme
        colors = (c.get("band"), c.get("accent"), c.get("on_accent"), p["fg"], p["dim"])
        if colors == self._colors or not c:
            return
        self._colors = colors
        band, accent, on_accent, fg, dim = colors
        for w in (self, self.body, self.controls):
            w.configure(bg=band)
        self.cover.restyle(band, blend(band, fg, 0.08))
        self.now.restyle(band, fg, dim)
        for b in (self.btn_prev, self.btn_next, self.btn_expand):
            b.restyle(band, fg, dim, accent)
        self.btn_play.restyle(band, accent, on_accent)
        self.bar.configure(bg=blend(band, fg, 0.10))
        self.bar.itemconfigure(self._bar_item, fill=accent)
