"""Ventana principal: biblioteca local + YT Music (por género) + reproductor."""
import io
import itertools
import math
import queue
import threading
import time
import tkinter as tk
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import config, duplicates, favorites, health, history, library, loudness, waveform
from ..core import log as _log
from ..core.filters import fold
from . import prefs, session
from .anim import Animator, ease_in_out
from .downloads import DownloadsController
from .player import Player
from .playqueue import PlayQueue
from .theme import (
    FONT_FAMILY,
    blend,
    dominant_color,
    enable_dpi_awareness,
    palette,
    readable_on,
    seed_color,
    style_titlebar,
    system_is_dark,
    vivid,
)
from .widgets import (
    Cover,
    Equalizer,
    Header,
    IconButton,
    NowPlaying,
    PlayButton,
    RowList,
    SearchBox,
    Slider,
    StatusLine,
    TextButton,
    WaveSeek,
    ui_fonts,
)

log = _log.get("ui")
FONT = (FONT_FAMILY, 10)
SIDE_W = 200  # ancho de la barra de géneros, en píxeles a 96 ppp
DRAWER_W = 340  # ancho del panel «A continuación»
XFADE_CHOICES = (0, 2000, 4000, 6000, 8000, 12000)  # ms; 0 = gapless
FADE_TICK_MS = 40          # resolución de los fundidos y del disparo de la mezcla
MANUAL_FADE_MS = 280       # fundido al saltar de canción a mano
INTRO_FADE_MS = 150        # entrada al empezar desde silencio
PREPARE_WINDOW_MS = 12_000  # cuánto antes del final se deja abierta la siguiente
LEAD_MS = 60               # la mezcla arranca un poco antes: abrir/arrancar tarda unos ms
GAPLESS_LEAD_MS = 100
TRANSITION_TIMEOUT_S = 25
NET_POLL_S = 6  # cada cuánto se comprueba si hay internet
STALL_S = 8      # segundos sin avanzar (sin internet) antes de saltar a la cola de respaldo
BACKUP_AFTER_MS = 10_000  # una canción en streaming se guarda tras escucharla este tiempo
AHEAD_RESULTS = 12  # resultados de YT Music cuya URL se resuelve y se deja abierta en el reproductor
COLS = (("title", "Título", 280), ("artist", "Artista", 170), ("album", "Álbum", 170),
        ("dur", "Dur.", 50), ("state", "", 84))


def fmt(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def load_library() -> list[dict]:
    """La biblioteca descargada, sin las canciones cuyo archivo ya no existe en disco."""
    return [t for t in library.all_tracks() if Path(t["path"]).exists()]


def _sort_key(col: str):
    if col == "dur":
        return lambda t: t.get("duration") or 0
    return lambda t: (t.get(col) or "").lower()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.pq = PlayQueue(self._is_remote, lambda: self.online)
        self.dl = DownloadsController(self)
        self.title("Sound")
        self.attributes("-alpha", 0.0)  # entra con un fundido al terminar de construirse
        self.k = max(1.0, self.winfo_fpixels("1i") / 96)  # escala de la pantalla
        self.geometry(f"{int(1120 * self.k)}x{int(720 * self.k)}")
        self.minsize(int(880 * self.k), int(560 * self.k))
        self.player = Player()
        self.q: queue.Queue = queue.Queue()
        # hilos fijos para reproducir: conservan su yt-dlp ya preparado y no esperan tras las precargas
        self.play_pool = ThreadPoolExecutor(2)
        self.library: list[dict] = []
        self.lib_by_id: dict[str, dict] = {}
        self.raw: list[dict] = []   # filas sin ordenar
        self.rows: list[dict] = []  # lo que muestra la tabla ahora
        self.vid_iid: dict[str, str] = {}
        self.genre_filter: set[str] = set()
        self.side_items: list[tuple[str, str | None, str]] = []  # (tipo, nombre, texto)
        self.sel_keys: set[tuple[str, str | None]] = set()
        self.view_kind = "all"   # all | lib | liked | recent | top
        self.liked: set[str] = set()  # ids marcados con «Me gusta» (se recarga con la biblioteca)
        self.yt_genres: list[str] = []
        self.yt_failed = False
        self.yt_sel: list[str] = []
        self.remote: list[dict] = []   # resultados de YT Music mostrados junto a la biblioteca
        self.remote_src: str | None = None  # "songs" | "genre" | "genres"
        self.remote_q = ""
        self.searching = False
        self._qdebounce = None
        self.sort: tuple[str, bool] | None = None
        self.pages: dict[str, object] = {}  # páginas integradas creadas al abrirlas
        self.active_page = None  # página que cubre el contenido, si hay alguna
        self.search_id = 0
        self._debounce = None
        # reproducción
        self.playing_vid: str | None = None
        self.stream_t: dict | None = None  # pista que suena por streaming (para la vía de reserva)
        self.stream_backed = False
        self.play_token = 0
        self.seeking = False
        self._cover_data: bytes | None = None
        self._ambient: str | None = None  # color de la canción que suena: tiñe la ventana
        self.scheme: dict[str, str] = {}
        self.dl_paused = False
        self.prefs = prefs.load()
        self.cookies = str(self.prefs.get("cookies") or config.COOKIES_FROM_BROWSER or "")
        self._cookies_dialog_shown = False
        self._crash_shown = False
        self.dark = self.prefs.get("dark", system_is_dark())
        self.pq.shuffle = bool(self.prefs.get("shuffle", False))
        self.pq.repeat = int(self.prefs.get("repeat", 0))
        # cola automática: con internet, canciones similares a la primera; sin él, respaldo local
        self.autoplay = bool(self.prefs.get("auto", True))
        self.online = True          # lo actualiza un monitor de conexión en segundo plano
        self.seed_genre: str | None = None
        self.seed_token = 0
        self.topping = False        # hay una petición de canciones similares en curso
        self.queue_win: tk.Frame | None = None  # el panel «A continuación», si está abierto
        self.queue_lb: RowList | None = None
        self.queue_map: list[tuple[str, int]] = []
        # transiciones entre canciones
        self.xfade_ms = int(self.prefs.get("xfade", 4000))  # 0 = sin crossfade (gapless)
        self.normalize = bool(self.prefs.get("normalize", False))  # volumen por canción
        self.var_normalize = tk.BooleanVar(value=self.normalize)
        self._gain_pending: set[str] = set()
        self._gain_warned = False
        self.fade_ms = 0                       # fundido con el que arrancará la canción en preparación
        self.overlap = False                   # la que acaba se deja terminar (paso sin hueco)
        self.cur_track: dict | None = None
        self.transitioning_since: float | None = None  # se pidió otra canción y aún no suena
        self.auto_fired: str | None = None     # canción cuya mezcla anticipada ya se lanzó
        self.last_pos = (0, time.monotonic())
        self.resume_track: dict | None = None  # sesión anterior: suena al pulsar play
        self.resume_position = 0
        self.pending_seek: tuple[str, int] | None = None  # (video_id, ms) tras reanudar
        # historial: segundos escuchados de la canción actual, para contarla una sola vez
        self._listen_s = 0.0
        self._listen_vid: str | None = None
        self._listen_counted = True
        self._tick_at = time.monotonic()

        self.waves = waveform.Analyzer(lambda vid, levels: self.q.put(("wave", vid, levels)))
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._build()
        self.apply_theme()
        self.refresh_library()
        self._restore_session()
        self.waves.sweep((t["video_id"], t["path"]) for t in self.library)
        self._load_yt_genres()
        threading.Thread(target=self.dl.start_service, daemon=True).start()
        self.bind("<Control-g>", lambda e: (self.open_prompt(), "break")[1])  # «Música por frase»
        self.bind("<Control-l>", lambda e: (self.toggle_like(), "break")[1])  # Me gusta
        self.bind("<Control-f>", lambda e: (self.search_box.entry.focus_set(), "break")[1])
        self.bind("<Control-a>", self._select_all)  # todas las canciones de la vista
        self.bind("<Escape>", self._escape)
        self.play_pool.submit(self._warm_up)
        threading.Thread(target=self._net_loop, daemon=True).start()
        self.after(FADE_TICK_MS, self._fade_tick)
        self.after(100, self._pump)
        self.after(400, self._tick)
        self.after(1200, self._check_environment)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.anim.tween("window", 260, lambda p: self.attributes("-alpha", p),
                        done=lambda: self.attributes("-alpha", 1.0))

    # ---------- construcción ----------
    def _build(self) -> None:
        k = self.k
        self.anim = Animator(self)
        self.fonts = f = ui_fonts(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        list_x = int((SIDE_W + 20) * k)  # donde empieza la columna principal
        self._hover_row: str | None = None
        self._eq_spot: tuple[int, int] | None = None
        self._drawer_x = int(DRAWER_W * k)

        # arriba: marca, buscador y, a la derecha, solo lo que hace falta ahora
        top = tk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, minsize=list_x)
        top.columnconfigure(2, weight=1)
        self.top = top
        self.mark = tk.Label(top, text="Sound", font=f["mark"], anchor="w")
        self.mark.grid(row=0, column=0, sticky="w", padx=(int(24 * k), 0))
        self.query = tk.StringVar()
        self.search_box = SearchBox(top, self.anim, k, f["body"], self.query,
                                    "Buscar canciones, artistas o álbumes…")
        self.search_box.grid(row=0, column=1, sticky="w", pady=(int(6 * k), 0))
        self.entry = self.search_box.entry
        self.entry.bind("<Return>", lambda e: self.search("songs"))
        self.query.trace_add("write", lambda *a: self._on_query())
        self.btn_dl = TextButton(top, self.anim, "Descargar", f["body"], self.download_selected)
        self.btn_dl.grid(row=0, column=4)
        self.btn_dl.set_visible(False)
        # «Descargas» (la cola de descargas), distinta de la cola de reproducción (btn_queue)
        self.btn_downloads = IconButton(
            top, self.anim, k, "downloads", self.open_queue,
            tip=lambda: "Descargas (en pausa)" if self.dl_paused else "Descargas")
        self.btn_downloads.grid(row=0, column=7, padx=(int(2 * k), 0))
        self.btn_import = IconButton(top, self.anim, k, "link", self.open_import,
                                     tip="Importar álbum, playlist o artista (URL)")
        self.btn_import.grid(row=0, column=5, padx=(int(6 * k), 0))
        self.btn_prompt = IconButton(top, self.anim, k, "note", self.open_prompt,
                                     tip="Música por frase (Ctrl+G)")
        self.btn_prompt.grid(row=0, column=6, padx=(int(2 * k), 0))
        self.btn_theme = IconButton(top, self.anim, k, "theme", self.toggle_theme,
                                    tip="Tema claro u oscuro")
        self.btn_theme.grid(row=0, column=8, padx=(int(2 * k), int(14 * k)))

        # título de la vista y línea de estado, sobre un degradado teñido por la canción
        self.header = Header(self, self.anim, k, f, list_x)
        self.header.grid(row=1, column=0, sticky="ew")
        self.status = StatusLine(self.header)

        content = tk.Frame(self)
        content.grid(row=2, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        self.content = content
        side = tk.Frame(content)
        side.grid(row=0, column=0, sticky="ns", padx=(int(12 * k), int(8 * k)))
        side.grid_propagate(False)
        side.configure(width=int(SIDE_W * k))
        side.rowconfigure(0, weight=1)
        side.columnconfigure(0, weight=1)
        self.side = side
        self.genres = RowList(side, self.anim, k, f, on_select=self._pick_genre)
        self.genres.configure(width=int((SIDE_W - 14) * k))
        self.genres.grid(row=0, column=0, sticky="nsew")
        self.sb_genres = ttk.Scrollbar(side, orient="vertical",
                                       command=self._sidebar_scrollbar)
        self.genres.configure(yscrollcommand=self._sidebar_scrolled)
        self.sb_genres.grid(row=0, column=1, sticky="ns")
        self.sb_genres.grid_remove()
        # la rueda también desplaza la lista si el puntero cae en el margen o en la barra
        for w in (side, self.sb_genres):
            w.bind("<MouseWheel>", self.genres._wheel)

        mid = tk.Frame(content)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, int(10 * k)))
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        self.mid = mid
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLS], selectmode="extended")
        # la columna inicial solo lleva el ecualizador de la fila que suena
        self.tree.column("#0", width=int(30 * k), minwidth=int(30 * k), stretch=False)
        for c, text, w in COLS:
            self.tree.heading(c, text=text, anchor="e" if c == "dur" else "w",
                              command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=int(w * k), anchor="e" if c == "dur" else "w",
                             stretch=c in ("title", "artist", "album"))
        self.sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self._tree_scrolled)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)
        self.tree.bind("<Button-3>", self._context)
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Motion>", self._row_hover)
        self.tree.bind("<Leave>", lambda e: self._set_hover_row(None))
        self.empty = tk.Label(mid, font=f["body"])

        # panel «A continuación»: vive fuera de la vista y entra deslizándose
        w = int(DRAWER_W * k)
        self.drawer = tk.Frame(content)
        self.drawer.place(relx=1.0, x=w, rely=0, relheight=1.0, anchor="ne", width=w)
        self.drawer_line = tk.Frame(self.drawer, width=max(1, int(k)))
        self.drawer_line.pack(side="left", fill="y")
        self.drawer_title = tk.Label(self.drawer, text="A continuación", font=f["strong"], anchor="w")
        self.drawer_title.pack(fill="x", padx=int(16 * k), pady=(int(14 * k), 0))
        self.drawer_sub = tk.Label(self.drawer, font=f["small"], anchor="w")
        self.drawer_sub.pack(fill="x", padx=int(16 * k), pady=(0, int(8 * k)))
        self.drawer_hint = tk.Label(
            self.drawer, text="Arrastra para ordenar · clic derecho o Supr para quitar",
            font=f["small"], anchor="w")
        self.drawer_hint.pack(fill="x", padx=int(16 * k), pady=(0, int(8 * k)))
        self.drawer_list = RowList(self.drawer, self.anim, k, f, multi=False,
                                   on_activate=lambda i: self._queue_jump(),
                                   on_move=self._queue_move, on_context=self._queue_context)
        self.drawer_list.pack(fill="both", expand=True, padx=int(4 * k))
        for seq in ("<Delete>", "<BackSpace>"):
            self.drawer_list.bind(seq, lambda e: self._queue_remove())

        # la onda es la barra de progreso y a la vez el borde superior del reproductor
        playing = lambda: self.player.loaded and not self.player.paused  # noqa: E731
        self.scale = WaveSeek(
            self, self.anim, k, f["digits"], playing,
            time_at=lambda x: fmt(x * self.player.length / 1000) if self.player.length else "",
            on_press=lambda: setattr(self, "seeking", True),
            on_release=lambda: self._seek_release(None))
        self.scale.grid(row=3, column=0, sticky="ew")
        self.eq = Equalizer(mid, self.anim, k, playing)
        self.eq.on_paint = self._place_eq

        band = tk.Frame(self)
        band.grid(row=4, column=0, sticky="ew")
        bar = tk.Frame(band)
        bar.pack(fill="x", padx=int(16 * k), pady=int(10 * k))
        bar.columnconfigure(0, weight=1, uniform="lados")
        bar.columnconfigure(2, weight=1, uniform="lados")

        left = tk.Frame(bar)
        left.grid(row=0, column=0, sticky="ew")
        self.cover = Cover(left, self.anim, k)
        self.cover.pack(side="left")
        self.now = NowPlaying(left, self.anim, k, f)
        self.now.pack(side="left", fill="x", expand=True, padx=(int(12 * k), int(12 * k)))

        mid_bar = tk.Frame(bar)
        mid_bar.grid(row=0, column=1)
        self.btn_shuffle = IconButton(mid_bar, self.anim, k, "shuffle", self.toggle_shuffle,
                                      tip="Orden aleatorio")
        self.btn_prev = IconButton(mid_bar, self.anim, k, "prev", lambda: self.step(-1),
                                   tip="Anterior")
        self.btn_play = PlayButton(mid_bar, self.anim, k, self.play_pause, busy=self._opening)
        self.btn_next = IconButton(mid_bar, self.anim, k, "next", lambda: self.step(1),
                                   tip="Siguiente")
        self.btn_repeat = IconButton(mid_bar, self.anim, k, "repeat", self.cycle_repeat,
                                     tip=lambda: ("Sin repetir", "Repetir la lista",
                                                  "Repetir esta canción")[self.pq.repeat])
        for w_ in (self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat):
            w_.pack(side="left", padx=int(2 * k))

        right = tk.Frame(bar)
        right.grid(row=0, column=2, sticky="e")
        self.vol = Slider(right, self.anim, k, command=self.player.set_volume)
        self.vol.pack(side="right")
        self.vol_icon = IconButton(right, self.anim, k, "volume", size=26, glyph=17)
        self.vol_icon.pack(side="right", padx=(int(6 * k), 0))
        self.btn_queue = IconButton(right, self.anim, k, "queue", self._show_queue,
                                    tip="A continuación")
        self.btn_queue.pack(side="right")
        self.btn_xfade = IconButton(
            right, self.anim, k, "xfade", self.cycle_xfade,
            tip=lambda: f"Crossfade de {self.xfade_ms // 1000} s" if self.xfade_ms
            else "Sin crossfade")
        self.btn_xfade.pack(side="right")
        self.btn_auto = IconButton(right, self.anim, k, "infinity", self.toggle_auto,
                                   tip="Cola automática con canciones similares")
        self.btn_auto.pack(side="right")
        self.time = tk.Label(right, text="0:00 / 0:00", font=f["digits"])
        self.time.pack(side="right", padx=(0, int(10 * k)))

        self.band_frames = (band, bar, left, mid_bar, right)
        self.top_icons = (self.btn_import, self.btn_prompt, self.btn_downloads, self.btn_theme)
        self.bar_icons = (self.btn_shuffle, self.btn_prev, self.btn_next, self.btn_repeat,
                          self.vol_icon, self.btn_queue, self.btn_xfade, self.btn_auto)
        self.vol.set(self.prefs.get("vol", 80))

        self.bind("<space>", self._space)
        self.bind("<Control-f>", lambda e: (self.entry.focus_set(), "break"))
        self._refresh_toggles()

    def _place_eq(self) -> None:
        """Coloca el ecualizador sobre la fila que suena, con el fondo que tenga esa fila."""
        iid = self.vid_iid.get(self.playing_vid) if self.playing_vid else None
        box = self.tree.bbox(iid, "#0") if iid is not None and self.tree.exists(iid) else ""
        if not box:
            self.eq.place_forget()
            self._eq_spot = None
            return
        x, y, w, h = box
        p = self.pal
        bg = p["sel"] if iid in self.tree.selection() else (
            p["hover"] if iid == self._hover_row else p["bg"])
        self.eq.configure(bg=bg)
        spot = (x + (w - self.eq.size) // 2, y + (h - self.eq.size) // 2)
        self.eq.place(in_=self.tree, x=spot[0], y=spot[1])
        if spot != self._eq_spot:  # que la tabla repinte el hueco que deja: evita un rastro
            self._eq_spot = spot
            self.tree.tag_configure("hover", background=p["hover"])

    def _opening(self) -> bool:
        since = self.transitioning_since
        return since is not None and time.monotonic() - since < TRANSITION_TIMEOUT_S

    def _space(self, e) -> str | None:
        if self.focus_get() is self.entry:
            return None
        self.play_pause()
        return "break"

    # ---------- tema ----------
    def apply_theme(self) -> None:
        p = palette(self.dark)
        self.pal = p
        s = self.style
        k = self.k
        self.configure(bg=p["bg"])
        for w in (self.content, self.mid):
            w.configure(bg=p["bg"])
        self.empty.configure(bg=p["bg"], fg=p["dim"])
        self.genres.restyle(bg=p["bg"], fg=p["fg"], dim=p["dim"], faint=p["faint"], sel=p["sel"],
                            hover=p["hover"])
        self.drawer.configure(bg=p["panel"])
        self.drawer_line.configure(bg=p["line"])
        self.drawer_title.configure(bg=p["panel"], fg=p["fg"])
        self.drawer_sub.configure(bg=p["panel"], fg=p["dim"])
        self.drawer_hint.configure(bg=p["panel"], fg=p["faint"])
        self.drawer_list.restyle(bg=p["panel"], fg=p["fg"], dim=p["dim"], faint=p["faint"],
                                 sel=blend(p["panel"], p["fg"], 0.08),
                                 hover=blend(p["panel"], p["fg"], 0.04),
                                 accent=p["accent"])
        s.configure(".", background=p["bg"], foreground=p["fg"], font=FONT,
                    bordercolor=p["line"], lightcolor=p["bg"], darkcolor=p["bg"],
                    troughcolor=p["panel"], focuscolor=p["bg"])
        # la tabla, sin bordes ni rectángulo de foco: solo texto y aire
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""})]})])
        s.configure("Treeview", background=p["bg"], fieldbackground=p["bg"], foreground=p["fg"],
                    rowheight=int(36 * k), borderwidth=0, font=self.fonts["body"])
        s.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["fg"])])
        s.configure("Treeview.Heading", background=p["bg"], foreground=p["faint"], relief="flat",
                    borderwidth=0, font=self.fonts["small"], padding=(int(4 * k), int(6 * k)))
        s.map("Treeview.Heading", background=[("active", p["bg"])],
              foreground=[("active", p["fg"])])
        # botones de texto del panel de descargas
        s.configure("TButton", background=p["panel"], foreground=p["fg"], borderwidth=0,
                    relief="flat", padding=(int(12 * k), int(6 * k)))
        s.map("TButton", background=[("pressed", p["line"]), ("active", p["sel"])],
              foreground=[("disabled", p["faint"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                    borderwidth=0, relief="flat", padding=(int(12 * k), int(6 * k)))
        s.map("Accent.TButton",
              background=[("pressed", blend(p["accent"], p["fg"], 0.14)),
                          ("active", blend(p["accent"], p["fg"], 0.08))],
              foreground=[("disabled", p["faint"])])
        # campos de los diálogos (el buscador principal va dibujado aparte)
        for name in ("TEntry", "TSpinbox"):
            s.configure(name, fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"],
                        bordercolor=p["line"], lightcolor=p["line"], darkcolor=p["line"],
                        arrowcolor=p["dim"], background=p["panel"], padding=(int(6 * k), int(3 * k)))
            s.map(name, bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
                  darkcolor=[("focus", p["accent"])], fieldbackground=[("disabled", p["panel"])],
                  foreground=[("disabled", p["faint"])])
        # barra de desplazamiento: solo el pulgar, sin flechas ni carril visible
        s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure("Vertical.TScrollbar", background=p["line"], troughcolor=p["bg"],
                    bordercolor=p["bg"], lightcolor=p["line"], darkcolor=p["line"], relief="flat",
                    gripcount=0, arrowsize=int(7 * k), borderwidth=0)
        s.map("Vertical.TScrollbar", background=[("active", p["faint"])])
        self.tree.tag_configure("local", foreground=p["fg"])
        self.tree.tag_configure("remote", foreground=p["dim"])
        self.tree.tag_configure("hover", background=p["hover"])
        scheme = self._scheme(self._ambient)
        self._paint(scheme)
        self._ambient_settled(scheme)
        for page in self.pages.values():
            page.restyle()

    def _scheme(self, color: str | None) -> dict[str, str]:
        """Colores de la ventana para el color de la canción que suena (None: ninguna)."""
        p = self.pal
        color = vivid(color) if color else None
        tint = color or p["accent"]
        strength = (1.0 if color else 0.4) * (1.0 if self.dark else 0.7)
        band = blend(p["panel"], tint, 0.11 * strength)
        accent = readable_on(color, band) if color else p["accent"]
        return dict(accent=accent, on_accent=p["accent_fg"], band=band,
                    top=blend(p["bg"], tint, 0.16 * strength))

    def _paint(self, c: dict[str, str]) -> None:
        self.scheme = c
        p = self.pal
        top, band, accent = c["top"], c["band"], c["accent"]
        self.top.configure(bg=top)
        self.mark.configure(bg=top, fg=p["fg"])
        self.search_box.restyle(bg=top, field=p["raised"], fg=p["fg"], dim=p["dim"],
                                faint=p["faint"], line=blend(p["bg"], p["fg"], 0.18),
                                accent=accent)
        self.btn_dl.restyle(top, p["fg"], p["dim"])
        for b in self.top_icons:
            b.restyle(top, p["fg"], p["dim"], accent)
        self.header.restyle(top, p["bg"], p["fg"], p["dim"])
        self.scale.restyle(p["bg"], blend(p["bg"], p["fg"], 0.15), blend(p["bg"], p["fg"], 0.36),
                           accent, p["fg"])
        for w in self.band_frames:
            w.configure(bg=band)
        self.cover.restyle(band, blend(band, p["fg"], 0.08))
        self.now.restyle(band, p["fg"], p["dim"])
        self.time.configure(bg=band, fg=p["dim"])
        for b in self.bar_icons:
            b.restyle(band, p["fg"], p["dim"], accent)
        self.btn_play.restyle(band, accent, c["on_accent"])
        self.vol.restyle(band, blend(band, p["fg"], 0.2), accent)
        self.eq.set_color(accent)

    def _ambient_settled(self, c: dict[str, str]) -> None:
        self.tree.tag_configure("playing", foreground=c["accent"])
        style_titlebar(self, self.dark, c["top"])

    def _set_ambient(self, color: str | None) -> None:
        """La ventana cambia poco a poco al color de la canción nueva."""
        if color == self._ambient:
            return
        self._ambient = color
        old, new = dict(self.scheme), self._scheme(color)
        self.anim.tween("ambient", 640,
                        lambda q: self._paint({key: blend(old[key], new[key], q) for key in new}),
                        ease=ease_in_out, done=lambda: self._ambient_settled(new))

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self.apply_theme()
        self.anim.tween("window", 200, lambda q: self.attributes("-alpha", 0.82 + 0.18 * q))

    # ---------- vista unificada: biblioteca + YT Music ----------
    def _reload_library(self) -> None:
        self.library = load_library()
        self.lib_by_id = {t["video_id"]: t for t in self.library}
        self.liked = favorites.liked_ids()

    def refresh_library(self) -> None:
        self._reload_library()
        self._build_sidebar()
        self.refresh_view()

    def _build_sidebar(self) -> None:
        counts: dict[str, int] = {}
        for t in self.library:
            g = t["genre"] or "Sin género"
            counts[g] = counts.get(g, 0) + 1
        items = [("all", None, "Todo"), ("liked", None, "Me gusta"),
                 ("recent", None, "Recientes"), ("top", None, "Más escuchado")]
        rights = [str(len(self.library)), str(len(self.liked)), "", ""]
        for g, n in sorted(counts.items()):
            items.append(("lib", g, g))
            rights.append(str(n))
        if self.yt_genres:
            items.append(("hdr", None, "YouTube"))
            rights.append("")
            for g in self.yt_genres:
                items.append(("yt", g, g))
                rights.append("")
        else:
            items.append(("hdr", None, "YouTube · sin conexión" if self.yt_failed
                          else "YouTube · cargando…"))
            rights.append("")
        self.side_items = items
        self.genres.sel.clear()
        self.genres.set_items([dict(text=label, right=right, hdr=k == "hdr")
                               for (k, _n, label), right in zip(items, rights)])
        for i, (k, n, _l) in enumerate(items):
            if (k, n) in self.sel_keys:
                self.genres.selection_set(i)
        if not self.genres.curselection():
            self.genres.selection_set(0)

    def _pick_genre(self, _e=None) -> None:
        if self.active_page is not None:
            self.close_page()
        sel = [self.side_items[i] for i in self.genres.curselection()]
        sel = [s for s in sel if s[0] != "hdr"]
        if not sel:
            return
        had_query = bool(self.query.get())
        special = next((s for s in sel if s[0] in ("liked", "recent", "top")), None)
        if special:
            self.view_kind = special[0]
            self.genre_filter = set()
            self.yt_sel = []
            self.sel_keys = {(special[0], special[1])}
            self.sort = None  # cada vista especial trae su propio orden
            if self._debounce:
                self.after_cancel(self._debounce)
                self._debounce = None
        else:
            self.sel_keys = {(k, n) for k, n, _l in sel}
            lib_names = {n for k, n, _l in sel if k == "lib"}
            wants_all = any(k == "all" for k, _n, _l in sel)
            self.view_kind = "all" if wants_all or not lib_names else "lib"
            self.genre_filter = set() if wants_all else lib_names
            self.yt_sel = [n for k, n, _l in sel if k == "yt"]
            if self._debounce:
                self.after_cancel(self._debounce)
            if self.yt_sel:
                # Lo que hubiera de otra búsqueda no es de este género: no debe quedarse en la lista.
                self.remote, self.remote_src, self.remote_q = [], None, ""
                # espera un poco: permite marcar varios géneros con Ctrl antes de buscar
                self._debounce = self.after(500, self._search_genres)
            elif self.remote_src == "genres":
                self.remote, self.remote_src = [], None
        if had_query:  # al explorar se deja la búsqueda de texto (y _on_query refresca)
            self.query.set("")
        else:
            self.refresh_view()
        self._fade_rows()

    def _on_query(self) -> None:
        q = self.query.get().strip()
        if q and self.view_kind in ("liked", "recent", "top"):
            # buscar desde una vista especial busca en toda la biblioteca
            self.view_kind = "all"
            self.genre_filter = set()
            self.yt_sel = []
            self.sel_keys = {("all", None)}
            self._build_sidebar()
        if self.remote_src in ("songs", "genre") and q != self.remote_q:
            self.remote, self.remote_src = [], None  # resultados de YT de otra búsqueda
        self.refresh_view()
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
        if len(q) >= 3:  # búsqueda automática en YT Music cuando dejas de escribir
            self._qdebounce = self.after(800, lambda: self.search("songs", auto=True))

    def refresh_view(self) -> None:
        q = self.query.get().strip()
        wanted = fold(q)

        def match(t: dict) -> bool:
            return wanted in fold(f"{t['title']} {t['artist']} {t.get('album') or ''}")

        if self.view_kind == "liked":
            local, remote = favorites.liked_tracks(), []
        elif self.view_kind == "recent":
            local, remote = self._history_rows(history.recent(200)), []
        elif self.view_kind == "top":
            local, remote = self._history_rows(history.most_played(200)), []
        else:
            local = self.library
            if self.genre_filter:
                local = [t for t in local if (t["genre"] or "Sin género") in self.genre_filter]
            elif self.yt_sel:
                # Género de YouTube: de la biblioteca solo lo que ya tenga esa etiqueta, no todo.
                wanted = {fold(g) for g in self.yt_sel}
                local = [t for t in local if t["genre"] and fold(t["genre"]) in wanted]
            remote = list({t["video_id"]: t for t in self.remote
                           if t["video_id"] not in self.lib_by_id}.values())
            if q and self.remote_src in ("genres", "import"):
                remote = [t for t in remote if match(t)]
        if q:
            local = [t for t in local if match(t)]
        self._fill(local + remote)
        if self.active_page is not None:
            return  # la cabecera pertenece a la página abierta
        self.header.set_title(self._view_title(), animate=not q)
        if not self.searching:
            msg = f"{len(local)} en biblioteca"
            if remote:
                msg += f"  ·  {len(remote)} de YouTube (☁)"
            self.status.configure(text=msg)

    def _history_rows(self, rows: list[dict]) -> list[dict]:
        """Completa las filas del historial con los datos de la biblioteca, si siguen descargadas."""
        out = []
        for row in rows:
            base = self.lib_by_id.get(row["video_id"])
            track = dict(base) if base else {
                "video_id": row["video_id"], "title": row["title"], "artist": row["artist"],
                "album": None, "genre": None, "duration": None,
            }
            if not base and row.get("path") and Path(row["path"]).exists():
                track["path"] = row["path"]
            track["_plays"] = row.get("plays") or 1
            out.append(track)
        return out

    # ---------- sesión anterior ----------
    def _restore_session(self) -> None:
        """Recupera cola y canción anterior; no suena nada hasta que pulses play."""
        data = session.load()
        if not data:
            return

        def fresh(track: dict) -> dict:
            base = self.lib_by_id.get(track["video_id"])
            if not base:
                return track
            merged = dict(base)
            if track.get("_genre"):
                merged["_genre"] = track["_genre"]
            return merged

        self.pq.ctx = [fresh(t) for t in data["ctx"]]
        self.pq.order = data["order"]
        self.pq.opos = min(data["opos"], max(0, len(self.pq.order) - 1))
        self.pq.up_next = [fresh(t) for t in data["up_next"]]
        self.pq.auto_ctx = data["auto_ctx"]
        self.pq.played = set(data["played"])

        current = fresh(data["current"]) if data["current"] else None
        if current is None:
            return
        self.cur_track = current
        self.resume_track = current
        self.resume_position = data["position_ms"]
        self.now.set(current["title"], current.get("artist") or "")
        self._show_wave(current)
        local = current.get("path")
        self._load_cover(current, local if local and Path(local).exists() else None,
                         self.play_token)
        duration = current.get("duration")
        if duration and self.resume_position:
            self.scale.set(min(1000, self.resume_position / (duration * 1000) * 1000))
            self.time.configure(text=f"{fmt(self.resume_position / 1000)} / {fmt(duration)}")
        self.status.configure(text="Continúa donde lo dejaste: pulsa play")

    def _save_session(self) -> None:
        position = self.resume_position
        if self.player.loaded:
            try:
                position = self.player.position()
            except Exception:  # noqa: BLE001 - cerrar no debe fallar por leer la posición
                pass
        session.save({
            "current": self.cur_track,
            "position_ms": position,
            "ctx": self.pq.ctx,
            "order": self.pq.order,
            "opos": self.pq.opos,
            "up_next": self.pq.up_next,
            "auto_ctx": self.pq.auto_ctx,
            "played": self.pq.played,
        })

    def _view_title(self) -> str:
        q = self.query.get().strip()
        if q:
            return f"«{q}»"
        if self.view_kind == "liked":
            return "Me gusta"
        if self.view_kind == "recent":
            return "Recientes"
        if self.view_kind == "top":
            return "Más escuchado"
        names = sorted(self.genre_filter or set(self.yt_sel))
        if not names:
            return "Tu biblioteca"
        return names[0] if len(names) == 1 else f"{names[0]} y {len(names) - 1} más"

    def _row_tags(self, t: dict) -> tuple:
        if t["video_id"] == self.playing_vid:
            return ("playing",)
        return ("local",) if "path" in t else ("remote",)

    def _fade_rows(self) -> None:
        """Las filas aparecen con un fundido al cambiar de vista (no al escribir: sería ruido)."""
        p = self.pal
        targets = {"local": p["fg"], "remote": p["dim"], "playing": self.scheme["accent"]}

        def step(q: float) -> None:
            for tag, color in targets.items():
                self.tree.tag_configure(tag, foreground=blend(p["bg"], color, q))

        self.anim.tween("rows", 220, step)

    def _row_hover(self, e) -> None:
        self._set_hover_row(self.tree.identify_row(e.y) or None)

    def _set_hover_row(self, iid: str | None) -> None:
        if iid == self._hover_row:
            return
        for row, change in ((self._hover_row, False), (iid, True)):
            if row and self.tree.exists(row):
                tags = [x for x in self.tree.item(row, "tags") if x != "hover"]
                self.tree.item(row, tags=[*tags, "hover"] if change else tags)
        self._hover_row = iid
        self._place_eq()

    def _tree_scrolled(self, first: str, last: str) -> None:
        """La barra de desplazamiento solo aparece si hay algo que desplazar."""
        self.sb.set(first, last)
        self._place_eq()
        if float(first) <= 0 and float(last) >= 1:
            self.sb.grid_remove()
        else:
            self.sb.grid()

    def _sidebar_scrolled(self, first: str, last: str) -> None:
        """La barra de la lista de géneros solo aparece si hay algo que desplazar."""
        self.sb_genres.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.sb_genres.grid_remove()
        else:
            self.sb_genres.grid()

    def _sidebar_scrollbar(self, *args) -> None:
        """Mueve la lista desde su barra y deja su desplazamiento interno al día."""
        self.genres.yview(*args)
        self.genres.off = self.genres._goal = self.genres.yview()[0] * self.genres.total

    def _row_state(self, t: dict) -> str:
        if self.view_kind in ("recent", "top"):
            plays = t.get("_plays")
            if plays:
                return f"×{plays}"
        return self.dl.state.get(t["video_id"]) or ("" if "path" in t else "☁")

    def _fill(self, rows: list[dict]) -> None:
        self.raw = rows
        if self.sort:
            col, rev = self.sort
            rows = sorted(rows, key=_sort_key(col), reverse=rev)
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        self.vid_iid = {}
        self._hover_row = None
        for i, t in enumerate(rows):
            self.vid_iid[t["video_id"]] = str(i)
            heart = "♥  " if t["video_id"] in self.liked else ""
            self.tree.insert("", "end", iid=str(i), tags=self._row_tags(t), values=(
                heart + t["title"], t["artist"], t.get("album") or "", fmt(t.get("duration")),
                self._row_state(t)))
        self._sort_headings()
        self._update_empty()
        self._place_eq()

    def _update_empty(self) -> None:
        if self.rows:
            self.empty.place_forget()
            return
        if self.searching:
            text = "Buscando…"
        elif self.query.get().strip():
            text = "Nada en tu biblioteca. Pulsa Intro para buscar en YouTube."
        elif self.view_kind == "liked":
            text = "Aún no has marcado ninguna canción. Botón derecho → Me gusta."
        elif self.view_kind in ("recent", "top"):
            text = "Todavía no hay reproducciones registradas."
        else:
            text = "Aún no hay nada aquí. Busca una canción arriba para empezar."
        self.empty.configure(text=text)
        self.empty.place(relx=0.5, rely=0.42, anchor="center")

    def _sort_headings(self) -> None:
        for c, text, _w in COLS:
            arrow = ""
            if self.sort and self.sort[0] == c:
                arrow = " ▼" if self.sort[1] else " ▲"
            self.tree.heading(c, text=text + arrow)

    def sort_by(self, col: str) -> None:
        if col == "state":
            return
        if self.sort and self.sort[0] == col:
            self.sort = (col, True) if not self.sort[1] else None  # asc → desc → sin orden
        else:
            self.sort = (col, False)
        self._fill(self.raw)

    def _retag(self, vid: str | None) -> None:
        iid = self.vid_iid.get(vid) if vid else None
        if iid is not None and self.tree.exists(iid):
            self.tree.item(iid, tags=self._row_tags(self.rows[int(iid)]))
        self._place_eq()

    def _set_state(self, vid: str, text: str) -> None:
        self.dl.state[vid] = text
        iid = self.vid_iid.get(vid)
        if iid is not None and self.tree.exists(iid):
            self.tree.set(iid, "state", text)

    def _selected(self) -> list[dict]:
        return [self.rows[i] for i in sorted(int(i) for i in self.tree.selection())]

    # ---------- menús ----------
    def _menu(self, master=None) -> tk.Menu:
        p = self.pal
        return tk.Menu(master or self, tearoff=0, bg=p["panel"], fg=p["fg"],
                       activebackground=p["sel"], activeforeground=p["fg"], borderwidth=0)

    def toggle_like(self) -> None:
        """Marca o desmarca con «Me gusta» las canciones seleccionadas de la biblioteca."""
        picks = [t for t in self._selected() if t["video_id"] in self.lib_by_id]
        if not picks:
            self.status.configure(text="Solo se pueden marcar canciones descargadas")
            return
        want = not all(t["video_id"] in self.liked for t in picks)
        for t in picks:
            favorites.set_liked(t["video_id"], want)
        self.refresh_library()  # contador del lateral, corazones y vista «Me gusta»
        keep = [self.vid_iid[t["video_id"]] for t in picks if t["video_id"] in self.vid_iid]
        if keep:  # la selección sobrevive: se pueden marcar varias seguidas
            self.tree.selection_set(keep)
        self.status.configure(text=f"{len(picks)} en Me gusta" if want
                              else f"{len(picks)} fuera de Me gusta")

    # ---------- duplicados y etiquetas ----------
    def find_duplicates(self) -> None:
        groups = duplicates.find()
        if not groups:
            messagebox.showinfo("Canciones repetidas", "No hay canciones repetidas.", parent=self)
            return
        extras = sum(len(group.extras) for group in groups)
        sample = "\n".join(f"• {group.keep['artist']} — {group.keep['title']}"
                           f" ({len(group.extras) + 1} versiones)" for group in groups[:10])
        more = f"\n… y {len(groups) - 10} grupos más" if len(groups) > 10 else ""
        if not messagebox.askyesno(
                "Canciones repetidas",
                f"Hay {extras} archivos repetidos en {len(groups)} grupos.\n\n"
                f"{sample}{more}\n\n¿Borrar las repetidas y dejar la mejor de cada grupo?",
                parent=self):
            return
        removed, failed = duplicates.remove_extras(groups)
        self.refresh_library()
        if self.playing_vid and self.playing_vid not in self.lib_by_id:
            self.player.stop()
            self.playing_vid = None
            self.cur_track = None
            self.stream_t = None
            self.btn_play.configure(text="▶")
        self.status.configure(text=f"{removed} canciones repetidas borradas")
        if failed:
            messagebox.showwarning(
                "Canciones repetidas",
                f"{len(failed)} no se pudieron borrar (puede que estén en uso).",
                parent=self)

    def edit_tags(self) -> None:
        picks = [t for t in self._selected()
                 if "path" in t or t["video_id"] in self.lib_by_id]
        if len(picks) != 1:
            self.status.configure(text="Selecciona una canción descargada para editar sus etiquetas")
            return
        from .tag_dialog import TagDialog
        TagDialog(self, picks[0])

    def export_m3u(self) -> None:
        dest = filedialog.asksaveasfilename(
            parent=self, title="Exportar biblioteca", defaultextension=".m3u",
            initialfile="biblioteca.m3u",
            filetypes=[("Lista M3U", "*.m3u"), ("Todos los archivos", "*.*")])
        if not dest:
            return
        try:
            count = library.export_m3u(Path(dest))
        except OSError as exc:
            messagebox.showerror("Exportar", f"No se pudo escribir el archivo:\n{exc}", parent=self)
            return
        self.status.configure(text=f"{count} canciones exportadas a {dest}")

    def clean_missing(self) -> None:
        missing = library.missing_files()
        if not missing:
            messagebox.showinfo("Archivos que faltan",
                                "Todas las canciones de la biblioteca tienen su archivo.",
                                parent=self)
            return
        sample = "\n".join(f"• {t['artist']} — {t['title']}" for t in missing[:10])
        more = f"\n… y {len(missing) - 10} más" if len(missing) > 10 else ""
        if not messagebox.askyesno(
                "Archivos que faltan",
                f"{len(missing)} canciones apuntan a archivos que ya no están en disco.\n\n"
                f"{sample}{more}\n\n¿Quitarlas de la biblioteca?",
                parent=self):
            return
        count = library.remove_missing()
        self.refresh_library()
        self.status.configure(text=f"{count} canciones quitadas de la biblioteca")

    def update_components(self) -> None:
        """Actualiza yt-dlp y ytmusicapi sin cerrar la ventana."""
        if not messagebox.askyesno(
                "Actualizar componentes",
                "Se buscará la última versión de yt-dlp (y, en desarrollo, también ytmusicapi).\n\n"
                "Conviene hacerlo sin descargas en curso. Al terminar habrá que reiniciar "
                "la ventana para usar la versión nueva.\n\n¿Continuar?",
                parent=self):
            return
        self.status.configure(text="Actualizando yt-dlp y ytmusicapi…")

        def work() -> None:
            try:
                code = health.update_components()
                self.q.put(("update_done", code, ""))
            except Exception as exc:  # noqa: BLE001 - pip puede fallar de mil formas
                self.q.put(("update_done", 1, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def after_tag_edit(self, updated: dict) -> None:
        vid = updated["video_id"]
        self.refresh_library()
        if self.cur_track and self.cur_track.get("video_id") == vid:
            self.cur_track = {**self.cur_track, **updated}
            self.now.set(updated.get("title") or "", updated.get("artist") or "")
        self._retag(vid)
        self.status.configure(
            text=f"Etiquetas actualizadas: {updated.get('artist')} - {updated.get('title')}")

    # ---------- búsqueda en YT Music ----------
    def _load_yt_genres(self) -> None:
        def work() -> None:
            try:
                from ..core import genres
                # géneros fijos del sidebar
                self.q.put(("genres", genres.sidebar_genres()))
            except Exception as exc:  # noqa: BLE001 - sin internet
                _log.failure(log, "no se pudieron cargar los géneros de YT Music", exc)
                self.q.put(("genres", None))

        threading.Thread(target=work, daemon=True).start()

    def _tag_results(self, tracks: list[dict], genre: str | None) -> list[dict]:
        for t in tracks:
            t["_genre"] = genre
        return tracks

    def search(self, kind: str = "songs", auto: bool = False) -> None:
        q = self.query.get().strip()
        if not q:
            return
        if self.active_page is not None:
            self.close_page()
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
        self.searching = True
        self.status.configure(text="Buscando en YouTube…")
        self.search_id += 1
        sid = self.search_id

        def work() -> None:
            try:
                from ..core import genres, ytsearch
                if kind == "genre":
                    # solo canciones nuevas: lo que ya está descargado no ocupa sitio en los 100
                    skip_ids, skip_songs = genres.known_from_library(include_queue=False)
                    label, tracks = genres.select_tracks(
                        q, 100, skip_ids=skip_ids, skip_songs=skip_songs)
                    self._tag_results(tracks, label)
                else:
                    tracks = self._tag_results(ytsearch.search_songs(q, 40), None)
                self.q.put(("results", sid, tracks, kind, q))
            except Exception as exc:  # noqa: BLE001 - sin internet, etc.
                _log.failure(log, f"búsqueda fallida ({kind}: {q!r})", exc)
                self.q.put(("remote_error", sid, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _search_genres(self) -> None:
        names = list(self.yt_sel)
        if not names:
            return
        self.searching = True
        self.status.configure(text=f"Buscando {', '.join(names)} en YouTube…")
        self.search_id += 1
        sid = self.search_id

        def work() -> None:
            try:
                from ..core import genres
                per = max(10, math.ceil(100 / len(names)))
                skip_ids, skip_songs = genres.known_from_library(include_queue=False)
                with ThreadPoolExecutor(3) as ex:
                    results = list(ex.map(lambda g: (g, genres.select_tracks(
                        g, per, skip_ids=skip_ids, skip_songs=skip_songs)[1]), names))
                lists = [self._tag_results(tr, g) for g, tr in results]
                mixed, seen = [], set()
                for batch in itertools.zip_longest(*lists):  # intercala los géneros
                    for t in batch:
                        if t and t["video_id"] not in seen:
                            seen.add(t["video_id"])
                            mixed.append(t)
                self.q.put(("results", sid, mixed, "genres", ""))
            except Exception as exc:  # noqa: BLE001
                _log.failure(log, f"búsqueda por géneros fallida ({names})", exc)
                self.q.put(("remote_error", sid, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ---------- descargas / borrado ----------
    def download_selected(self) -> None:
        picks = [t for t in self._selected()
                 if "path" not in t and t["video_id"] not in self.lib_by_id
                 and self.dl.state.get(t["video_id"], "") in ("", "✗")]
        if not picks:
            self.status.configure(text="Selecciona canciones de YouTube (☁) para descargar")
            return
        by_genre: dict[str | None, list[dict]] = {}
        for t in picks:
            by_genre.setdefault(t.get("_genre"), []).append(t)
        for genre, group in by_genre.items():  # una escritura en la base por género, no una por canción
            self.dl.submit(group, genre)
        self.status.configure(text=f"Descargando {len(picks)}…")






    def open_queue(self) -> None:
        """Abre la página de la cola de descargas."""
        self.show_page("downloads")

    def open_prompt(self) -> None:
        """Abre «Música por frase»: pides lo que quieres oír y cuántas canciones."""
        self.show_page("prompt")

    def open_import(self) -> None:
        """Abre «Importar de YouTube Music»: pega un enlace y encola lo que aún no tengas."""
        self.show_page("import")

    def open_health(self) -> None:
        """Abre el diagnóstico del entorno (Node, ffmpeg, cookies, carpetas…)."""
        self.show_page("health")

    def _check_environment(self) -> None:
        """Avisa al arrancar de lo que impedirá descargar, sin bloquear la ventana."""
        try:
            problems = health.problems()
        except Exception:  # noqa: BLE001 - una comprobación rota no debe impedir usar la app
            return
        if problems:
            self.status.configure(text=problems[0])

    def set_cookies(self, browser: str | None) -> None:
        """Guarda el navegador de cookies y lo aplica a las próximas descargas."""
        browser = (browser or "").strip()
        if browser == self.cookies:
            return
        self.cookies = browser
        self.dl.configure_cookies(browser)
        prefs.save(self._prefs_state())
        self.status.configure(
            text=(f"Cookies de YouTube: {browser}. Se usarán en las próximas descargas."
                  if browser else "Cookies de YouTube desactivadas."))
        if browser:
            self._cookies_dialog_shown = True

    def import_loaded(self, label: str, tracks: list[dict]) -> None:
        """Muestra lo importado en la biblioteca y encola solo las canciones nuevas."""
        new = [t for t in tracks if t["video_id"] not in self.lib_by_id]
        self.view_kind = "all"
        self.genre_filter = set()
        self.yt_sel = []
        self.sel_keys = {("all", None)}
        known = {t["video_id"] for t in tracks}
        self.remote = tracks + [t for t in self.remote if t["video_id"] not in known]
        self.remote_src, self.remote_q = "import", ""
        self._build_sidebar()
        self.refresh_view()
        self._fade_rows()
        if new:
            self.dl.submit(new, None)
            self.status.configure(text=f"{label}: {len(new)} canciones en descarga")
        else:
            self.status.configure(text=f"{label}: ya estaban todas en tu biblioteca")

    # ---------- páginas integradas ----------
    def _ensure_page(self, name: str):
        page = self.pages.get(name)
        if page is not None:
            return page
        if name == "prompt":
            from .prompt_dialog import PromptPage
            page = PromptPage(self)
        elif name == "import":
            from .import_page import ImportPage
            page = ImportPage(self)
        elif name == "downloads":
            from .queue_panel import DownloadsPage
            page = DownloadsPage(self)
        elif name == "health":
            from .health_page import HealthPage
            page = HealthPage(self)
        else:
            raise KeyError(name)
        self.pages[name] = page
        return page

    def show_page(self, name: str) -> None:
        """Trae una página al frente dentro de la ventana; nunca abre otra ventana."""
        page = self._ensure_page(name)
        if self.active_page is page:
            page.activate()
            return
        if self._qdebounce:  # una búsqueda a medio escribir no debe cerrar la página sola
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        if self.queue_win is not None:  # el panel «A continuación» no debe quedar debajo
            self._show_queue()
        if self.active_page is not None:
            self.active_page.deactivate()
            self.active_page.place_forget()  # si no, al cerrar la nueva reaparecería la anterior
        self.active_page = page
        page.restyle()
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        page.lift()
        self.header.set_title(page.page_title)
        page.activate()
        self._refresh_nav()

    def close_page(self, *_args) -> None:
        """Vuelve a la biblioteca desde cualquier página integrada."""
        page = self.active_page
        if page is None:
            return
        page.deactivate()
        page.place_forget()
        self.active_page = None
        self._refresh_nav()
        self.refresh_view()
        self.tree.focus_set()

    def _escape(self, _e=None) -> str | None:
        if self.active_page is None:
            return None
        self.close_page()
        return "break"

    def _refresh_nav(self) -> None:
        for name, button in (("prompt", self.btn_prompt), ("import", self.btn_import),
                             ("downloads", self.btn_downloads)):
            button.set_active(self.active_page is not None and self.active_page is self.pages.get(name))

    def retry_failed_downloads(self) -> int:
        """Devuelve las descargas fallidas a la cola. Devuelve cuántas eran."""
        return self.dl.retry_failed()

    @property
    def service(self):
        """El servicio de descargas (None mientras arranca); lo usa el panel de la cola."""
        return self.dl.service


    def _queue_paused_changed(self, paused: bool) -> None:
        # visible aunque la página esté cerrada
        self.dl_paused = paused
        self.btn_downloads.set_on(paused)
        self.status.configure(
            text="Descargas en pausa: lo que ya empezó termina. Reanuda desde «Descargas»." if paused
            else "Descargas reanudadas")
        page = self.pages.get("downloads")
        if page is not None and self.active_page is page:
            page.refresh()

    # ---------- streaming: URLs resueltas y reproductor preparado por adelantado ----------
    @staticmethod
    def _warm_up() -> None:
        from ..core import streaming
        streaming.warm_up()

    def _is_remote(self, t: dict) -> bool:
        return "path" not in t and t["video_id"] not in self.lib_by_id

    def _resolve_ahead(self, vids: list[str]) -> None:
        from ..core import streaming
        for vid in vids:
            fut = streaming.resolve_ahead(vid)
            if fut is not None:
                fut.add_done_callback(lambda f, v=vid: f.exception() or self.q.put(("resolved", v)))
            else:
                self._prepare(vid)



    def _prepare_targets(self) -> set[str]:
        """Canciones que probablemente se reproducirán ya: la fila elegida y las dos siguientes."""
        vids = {t["video_id"] for t in self.pq.next_tracks() if self._is_remote(t)}
        top = [t for t in self.rows if self._is_remote(t)][:AHEAD_RESULTS]
        vids |= {t["video_id"] for t in top}
        sel = self.tree.selection()
        if len(sel) == 1 and int(sel[0]) < len(self.rows) and self._is_remote(self.rows[int(sel[0])]):
            vids.add(self.rows[int(sel[0])]["video_id"])
        return vids

    def _prepare(self, vid: str) -> None:
        """Abre la canción en el reproductor (sin sonar) si su URL ya está resuelta."""
        from ..core import streaming
        url = streaming.cached(vid)
        if url and vid in self._prepare_targets() and vid != self.playing_vid:
            try:
                self.player.prepare(vid, url)
            except Exception as exc:  # noqa: BLE001 - la preparación es solo una optimización
                _log.failure(log, f"no se pudo preparar {vid}", exc)

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        self._place_eq()
        self.btn_dl.set_visible(any(int(i) < len(self.rows) and self._is_remote(self.rows[int(i)])
                                    for i in sel))
        if len(sel) == 1 and int(sel[0]) < len(self.rows):
            t = self.rows[int(sel[0])]
            if self._is_remote(t):
                self._resolve_ahead([t["video_id"]])

    def _select_all(self, _e=None) -> str | None:
        """Selecciona todas las canciones de la vista actual (Ctrl+A)."""
        # En un campo de texto, Ctrl+A debe seguir seleccionando el texto.
        if self.active_page is not None or isinstance(
                self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None
        rows = self.tree.get_children()
        if rows:
            focus = self.tree.focus()
            self.tree.selection_set(rows)
            if focus not in rows:
                self.tree.focus(rows[0])
        return "break"

    def _prefetch_next(self) -> None:
        self._resolve_ahead([t["video_id"] for t in self.pq.next_tracks() if self._is_remote(t)])

    def delete_selected(self) -> None:
        picks = [t for t in self._selected() if "path" in t]
        if not picks:
            return
        if not messagebox.askyesno(
                "Eliminar", f"¿Eliminar {len(picks)} canción(es) de la biblioteca y del disco?\n"
                            "No se puede deshacer."):
            return
        from ..core import library
        ids = {t["video_id"] for t in picks}
        if self.playing_vid in ids:
            self.player.stop()
            self.btn_play.configure(text="▶")
            self.playing_vid = None
        for t in picks:
            library.remove_track(t["video_id"])
            waveform.forget(t["video_id"])
            try:
                Path(t["path"]).unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar %s", t["path"], exc_info=True)
            self.dl.state.pop(t["video_id"], None)
        self.refresh_library()
        self.status.configure(text=f"{len(picks)} eliminadas")

    # ---------- menú contextual ----------
    def _context(self, e) -> None:
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        m = self._menu()
        m.add_command(label="Reproducir", command=lambda: self.play_index(int(iid)))
        m.add_command(label="Reproducir a continuación",
                      command=lambda: self.enqueue(self._selected(), front=True))
        m.add_command(label="Añadir a la cola", command=lambda: self.enqueue(self._selected()))
        if self.pq.up_next:
            m.add_command(label=f"Vaciar cola ({len(self.pq.up_next)})", command=self.clear_queue)
        picks = self._selected()
        local = [t for t in picks if "path" in t or t["video_id"] in self.lib_by_id]
        m.add_separator()
        if local:
            all_liked = all(t["video_id"] in self.liked for t in local)
            m.add_command(label="Quitar de Me gusta" if all_liked else "Me gusta",
                          command=self.toggle_like)
        if any(t["video_id"] not in self.lib_by_id for t in picks):
            m.add_command(label="Descargar", command=self.download_selected)
        if len(local) == 1:
            m.add_command(label="Editar etiquetas…", command=self.edit_tags)
        if local:
            m.add_command(label="Eliminar de la biblioteca…", command=self.delete_selected)
        m.add_separator()
        m.add_command(label="Buscar canciones repetidas…", command=self.find_duplicates)
        m.add_command(label="Exportar biblioteca a M3U…", command=self.export_m3u)
        m.add_command(label="Quitar archivos que faltan…", command=self.clean_missing)
        m.add_checkbutton(label="Normalizar volumen (ReplayGain)",
                          variable=self.var_normalize, command=self.toggle_normalize)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def enqueue(self, tracks: list[dict], front: bool = False) -> None:
        if front:
            self.pq.up_next[0:0] = tracks
        else:
            self.pq.up_next.extend(tracks)
        self.status.configure(text=f"En cola: {len(self.pq.up_next)}")

    def clear_queue(self) -> None:
        self.pq.up_next.clear()
        self.status.configure(text="Cola vacía")

    # ---------- reproducción ----------
    def _on_double(self, _e=None) -> None:
        sel = self.tree.selection()
        if sel:
            self.play_index(int(sel[0]))


    def play_index(self, i: int) -> None:
        """Empieza a reproducir la lista visible desde la fila i."""
        if not 0 <= i < len(self.rows):
            return
        t = self.rows[i]
        if self.autoplay:  # cola automática inspirada en esta primera canción
            self.pq.ctx, self.pq.auto_ctx = [t], True
            self.pq.make_order(0)
            self._seed(t)
        else:
            self.pq.ctx, self.pq.auto_ctx = list(self.rows), False
            self.pq.make_order(i)
        self._play_track(t)

    # ---------- cola automática ----------
    def _genre_of(self, t: dict) -> str | None:
        return t.get("genre") or t.get("_genre") or (self.lib_by_id.get(t["video_id"]) or {}).get("genre")

    def _seed(self, t: dict) -> None:
        """Empieza una cola automática a partir de `t`."""
        self.pq.played = {t["video_id"]}
        self.seed_token += 1
        self.seed_genre = self._genre_of(t)
        self.topping = False
        if self.online:
            self._fetch_radio(t)
        else:
            self._fill_backup()

    def _fetch_radio(self, t: dict) -> None:
        """Pide a YT Music canciones similares a `t` y las añade al final de la cola."""
        if self.topping:
            return
        self.topping, token = True, self.seed_token

        def work() -> None:
            try:
                from ..core import ytsearch
                self.q.put(("radio", token, ytsearch.radio_tracks(t["video_id"], 25)))
            except Exception as exc:  # noqa: BLE001 - sin internet, corte de red, API cambiada
                _log.failure(log, f"no se pudieron pedir canciones similares a {t['video_id']}", exc)
                self.q.put(("radio_err", token, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _append_ctx(self, tracks: list[dict]) -> None:
        self.pq.append(tracks)
        self._refresh_queue()

    def _on_radio(self, token: int, tracks: list[dict]) -> None:
        self.topping = False
        if token != self.seed_token or not self.pq.auto_ctx:
            return
        have = {x["video_id"] for x in self.pq.ctx} | self.pq.played
        new = []
        for x in tracks:
            if x["video_id"] in have:
                continue
            x["_genre"] = self.seed_genre  # lo que se guarde por el respaldo cae en el mismo género
            new.append(self.lib_by_id.get(x["video_id"], x))  # si ya la tienes, suena desde el disco
        if new:
            self._append_ctx(new)
            self.status.configure(text=f"Cola automática: {len(new)} canciones similares")
            self._prefetch_next()
            self.dl.download_ahead()
        else:
            self._fill_backup()

    def _on_radio_error(self, token: int, error: str) -> None:
        self.topping = False
        if token == self.seed_token and self.pq.auto_ctx:
            self._fill_backup()
            self.status.configure(text="No se pudo pedir canciones similares: cola de respaldo")

    def _fill_backup(self, n: int = 15) -> None:
        """Añade canciones de tu biblioteca: mismo género primero, otros géneros solo si no alcanzan."""
        if not (self.autoplay and self.pq.auto_ctx):
            return
        from ..core import autoqueue
        cur = self.pq.current(self.playing_vid)
        genre = (self._genre_of(cur) if cur else None) or self.seed_genre
        exclude = {x["video_id"] for x in self.pq.ctx} | self.pq.played
        tracks = autoqueue.backup_queue(self.library, genre, exclude, n)
        if tracks:
            self._append_ctx(tracks)
            same = sum(1 for x in tracks if (x.get("genre") or "").lower() == (genre or "").lower())
            self.status.configure(text=f"Cola de respaldo: {len(tracks)} canciones "
                                       f"({same} de {genre or 'sin género'})")


    def _top_up(self) -> None:
        """Mantiene unas cuantas canciones por delante: similares con internet, respaldo sin él."""
        if not (self.autoplay and self.pq.auto_ctx) or self.pq.playable_ahead() >= 6:
            return
        cur = self.pq.current(self.playing_vid)
        if self.online and cur:
            self._fetch_radio(cur)
        elif not self.online:
            self._fill_backup()

    def toggle_auto(self) -> None:
        self.autoplay = not self.autoplay
        if self.autoplay and not self.pq.auto_ctx and self.playing_vid:
            cur = self.pq.current(self.playing_vid)
            if cur:  # convierte lo que suena en la semilla de una cola automática
                self.pq.ctx, self.pq.auto_ctx = [cur], True
                self.pq.make_order(0)
                self._seed(cur)
        elif not self.autoplay:
            self.pq.auto_ctx = False
        self._refresh_toggles()
        self.status.configure(text="Cola automática " + ("activada" if self.autoplay else "desactivada"))

    # ---------- conexión ----------
    def _net_loop(self) -> None:
        from ..core import downloader
        while True:
            self.q.put(("online", downloader.is_online(timeout=2.0)))
            time.sleep(NET_POLL_S)

    @staticmethod
    def _check_online() -> bool:
        from ..core import downloader
        return downloader.is_online(timeout=1.5)

    def _on_online(self, online: bool) -> None:
        was, self.online = self.online, online
        if was == online:
            return
        if not online:
            self.status.configure(text="Sin conexión: seguirá la cola de respaldo de tu biblioteca")
            if self.pq.auto_ctx:
                self._fill_backup()
        else:
            self.status.configure(text="Conexión recuperada")
            self._top_up()
            self.dl.download_ahead()
        self._refresh_queue()

    # ---------- panel «A continuación» ----------
    def _show_queue(self) -> None:
        """Abre o cierra el panel, que entra deslizándose por la derecha."""
        opening = self.queue_win is None
        if opening and self.active_page is not None:
            self.close_page()
        self.queue_win = self.drawer if opening else None
        self.queue_lb = self.drawer_list if opening else None
        self.btn_queue.set_on(opening)
        start, to = self._drawer_x, 0 if opening else int(DRAWER_W * self.k)
        self.drawer.lift()
        if opening:
            self._refresh_queue()
            self.drawer_list.focus_set()

        def step(p: float) -> None:
            self._drawer_x = int(start + (to - start) * p)
            self.drawer.place_configure(x=self._drawer_x)

        self.anim.tween("drawer", 260 if opening else 190, step)

    def _refresh_queue(self) -> None:
        lb = self.queue_lb
        if self.queue_win is None or lb is None:
            return
        p = self.pal
        items: list[dict] = []
        self.queue_map = []

        def header(text: str) -> None:
            items.append(dict(text=text, hdr=True))
            self.queue_map.append(("hdr", 0))

        def add(t: dict, kind: str, idx: int, **extra) -> None:
            item = dict(text=f"{t['title']} — {t['artist']}", **extra)
            if kind in ("up", "ctx"):  # solo lo que queda por delante se puede arrastrar
                item["move"] = kind
            items.append(item)
            self.queue_map.append((kind, idx))

        cur = self.pq.current(self.playing_vid)
        if cur:
            header("Sonando ahora")
            add(cur, "cur", 0, color=self.scheme["accent"])
        # lo que pusiste tú va aparte de lo que llega solo (similares o respaldo)
        if self.pq.up_next:
            header(f"Añadidas por ti · {len(self.pq.up_next)}")
            for i, t in enumerate(self.pq.up_next):
                add(t, "up", i, right="＋")
        upcoming = range(self.pq.opos + 1, len(self.pq.order))
        if upcoming:
            header("Siguen automáticamente" if self.pq.auto_ctx else "Siguen en la lista")
            for j in upcoming:
                t = self.pq.ctx[self.pq.order[j]]
                remote = self._is_remote(t)
                add(t, "ctx", j, right="☁" if remote else "",
                    color=p["faint"] if remote and not self.online else None)
        lb.sel.clear()
        lb.set_items(items)
        kind = "Cola automática" if self.pq.auto_ctx else "De la lista"
        self.drawer_sub.configure(text=f"{kind} · {'en línea' if self.online else 'sin conexión'}")

    def _queue_jump(self, _e=None) -> None:
        sel = self.queue_lb.curselection() if self.queue_lb else ()
        if sel:
            self._queue_jump_at(sel[0])

    def _queue_jump_at(self, item: int) -> None:
        if not 0 <= item < len(self.queue_map):
            return
        kind, idx = self.queue_map[item]
        if kind == "up":
            track = self.pq.take_up_next(idx)
            if track is not None:
                self._play_track(track)
        elif kind == "ctx":
            self.pq.opos = idx
            self._play_track(self.pq.ctx[self.pq.order[idx]])

    # ---------- edición de la cola ----------
    def _queue_move(self, src: int, dst: int) -> None:
        """Mueve una fila dentro de su sección; `dst` es la posición de inserción."""
        if not 0 <= src < len(self.queue_map):
            return
        kind, idx = self.queue_map[src]
        if kind == "up":
            if dst in (idx, idx + 1):
                return  # se suelta donde estaba
            track = self.pq.take_up_next(idx)
            if track is None:
                return
            self.pq.insert_up_next(dst - 1 if dst > idx else dst, track)
        elif kind == "ctx":
            positions = [v for k, v in self.queue_map if k == "ctx"]
            target = positions[dst] if dst < len(positions) else len(self.pq.order)
            if target in (idx, idx + 1):
                return
            track = self.pq.take_upcoming(idx)
            if track is None:
                return
            self.pq.insert_upcoming(target - 1 if target > idx else target, track)
        else:
            return
        self._refresh_queue()
        self._prefetch_next()
        self.dl.download_ahead()

    def _queue_remove(self, item: int | None = None) -> str | None:
        """Quita de la cola la fila elegida (o la seleccionada, con Supr)."""
        if item is None:
            sel = self.queue_lb.curselection() if self.queue_lb else ()
            if not sel:
                return None
            item = sel[0]
        if not 0 <= item < len(self.queue_map):
            return None
        kind, idx = self.queue_map[item]
        if kind == "up":
            track = self.pq.take_up_next(idx)
        elif kind == "ctx":
            track = self.pq.take_upcoming(idx)
        else:
            return None
        if track is None:
            return None
        self.status.configure(text=f"Quitada de la cola: {track['title']} — {track['artist']}")
        self._refresh_queue()
        self._prefetch_next()
        return "break"

    def _queue_context(self, item: int, x: int, y: int) -> None:
        if not 0 <= item < len(self.queue_map):
            return
        kind, _idx = self.queue_map[item]
        if kind not in ("up", "ctx"):
            return
        m = self._menu(self.drawer_list)
        m.add_command(label="Reproducir ahora",
                      command=lambda: self._queue_jump_at(item))
        m.add_command(label="Quitar de la cola",
                      command=lambda: self._queue_remove(item))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _play_track(self, t: dict, how: str = "manual") -> None:
        """`how`: "auto" = fin natural (mezcla con la que suena), "manual" = la pediste tú
        (fundido corto), "cut" = sin fundido de salida."""
        self.play_token += 1
        token = self.play_token
        vid = t["video_id"]
        self.resume_track = None
        self.resume_position = 0
        self.overlap = False
        if how == "auto" and self.player.loaded:
            self.fade_ms = self._xfade_for(self.cur_track, t)
            self.overlap = self.fade_ms == 0  # gapless: la actual termina sola, sin cortarla
        elif how == "manual" and self.player.loaded:
            self.fade_ms = MANUAL_FADE_MS
            self.player.stop(MANUAL_FADE_MS)  # sale ya, aunque la nueva tarde en abrirse
        else:
            self.fade_ms = 0 if self.player.loaded else INTRO_FADE_MS
            self.player.stop()
        self.transitioning_since = time.monotonic()
        self.cur_track = t
        old, self.playing_vid = self.playing_vid, vid
        self.pq.played.add(vid)
        self.last_pos = (0, time.monotonic())
        self._listen_s = 0.0
        self._listen_vid = vid
        self._listen_counted = False
        self._tick_at = time.monotonic()
        self._retag(old)
        self._retag(vid)
        self._refresh_queue()
        iid = self.vid_iid.get(vid)
        if iid is not None and self.tree.exists(iid):
            self.tree.see(iid)
        self.now.set(t["title"], t.get("artist") or "")
        self._show_wave(t)
        self.scale.set(0)
        self.time.configure(text="0:00 / 0:00")
        local = t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")
        if local and Path(local).exists():
            self._start(t, token, path=local)
            return
        self.btn_play.configure(text="⏸")
        self._load_cover(t, None, token)
        from ..core import streaming
        url = streaming.cached(vid)
        if url:  # ya resuelta por adelantado: empieza al instante
            self._start(t, token, url=url, cover=False)
            return
        self.status.configure(text="Abriendo…")
        self.play_pool.submit(self._resolve_and_play, t, token)

    def _show_wave(self, t: dict) -> None:
        """La onda real de la canción, si está en disco; si no, la pide (la que suena va primero)."""
        vid = t["video_id"]
        levels = waveform.load(vid)
        self.scale.set_track(vid, levels)
        local = t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")
        if levels is None and local:
            self.waves.want(vid, local, urgent=True)

    def _resolve_and_play(self, t: dict, token: int) -> None:
        from ..core import streaming
        try:
            self.q.put(("stream", token, t, streaming.resolve(t["video_id"]), True))
        except Exception as exc:  # noqa: BLE001 - el cliente de streaming falló: se baja y se reproduce
            _log.failure(log, f"streaming de {t['video_id']} falló, se descargará", exc)
            self.q.put(("fallback", token, t))

    def _download_and_play(self, t: dict, token: int) -> None:
        """Vía lenta de reserva (descarga completa, ~10 s) si el streaming directo no funciona.
        Se llama desde el hilo de la interfaz."""
        vid = t["video_id"]
        self.status.configure(text="Streaming no disponible: descargando la canción…")
        self.dl.inflight.add(vid)
        self._set_state(vid, "en cola")

        def work() -> None:
            # fetch_now baja la canción ya, sin esperar turno detrás de la cola, y con reintentos;
            # si otro trabajador ya la está bajando, espera a que termine. El avance y el fin llegan
            # como eventos del servicio (la canción queda guardada en la biblioteca).
            try:
                if self.service is None:
                    raise RuntimeError("el servicio de descargas aún no está listo")
                path = self.service.fetch_now(t, t.get("_genre"))
                self.q.put(("stream", token, t, str(path)))
            except Exception as exc:  # noqa: BLE001 - sin internet, vídeo no disponible
                _log.failure(log, f"no se pudo reproducir {vid}", exc)
                self.q.put(("status", f"No se pudo reproducir {t['title']}: {exc}"))
            finally:
                self.dl.inflight.discard(vid)

        self.play_pool.submit(work)

    def _start(self, t: dict, token: int, path: str | None = None, url: str | None = None,
               cover: bool = True) -> None:
        vid = t["video_id"]
        if path:
            self._set_track_gain(t, path)
        else:
            self.player.set_next_gain_db(0)  # streaming: no hay archivo que medir
        try:
            if url:
                self.player.load_url(url, key=vid, fade_ms=self.fade_ms, overlap=self.overlap)
            else:
                self.player.load(path, key=vid, fade_ms=self.fade_ms, overlap=self.overlap)
        except RuntimeError as exc:
            self.transitioning_since = None
            self.status.configure(text=f"No se pudo reproducir: {exc}")
            return
        self.transitioning_since = None
        self.auto_fired = None
        self.player.set_volume(self.vol.get())
        if self.pending_seek:
            seek_vid, ms = self.pending_seek
            self.pending_seek = None
            if seek_vid == vid and ms > 0:
                try:
                    self.player.position()  # lee la duración antes de decidir si cabe
                    if self.player.length and ms > self.player.length - 5000:
                        ms = 0  # estaba prácticamente al final: mejor empezar de nuevo
                    self.player.seek(ms)
                except Exception as exc:  # noqa: BLE001 - reanudar es una mejora, no un requisito
                    _log.failure(log, f"no se pudo reanudar en {ms} ms", exc)
        self.btn_play.configure(text="⏸")
        self.stream_t = t if url else None
        self.stream_backed = False
        if url:
            self.status.configure(text="Streaming")
        elif cover:
            self.status.configure(text="")
            self._load_cover(t, path, token)
        self._prefetch_next()
        self.dl.download_ahead()
        self._top_up()

    def _set_track_gain(self, t: dict, path: str, current: bool = False) -> None:
        """Aplica la ganancia medida; si no está en caché, la mide en segundo plano.

        `current=True` cambia la canción que ya suena (al activar la opción o al terminar de
        medir); si no, deja preparada la ganancia de la siguiente.
        """
        apply = self.player.set_gain_db if current else self.player.set_next_gain_db
        if not self.normalize:
            apply(0)
            return
        if not path or not Path(path).exists():
            apply(0)
            return
        vid = t["video_id"]
        gain = loudness.cached_gain(vid, Path(path))
        if gain is not None:
            apply(gain)
            return
        apply(0)  # hasta tener la medición, sin cambios de volumen
        if not loudness.available():
            if not self._gain_warned:
                self._gain_warned = True
                self.status.configure(text="Normalización: instala ffmpeg en el PATH para usarla")
            return
        if vid in self._gain_pending:
            return
        self._gain_pending.add(vid)

        def work() -> None:
            try:
                result = loudness.measure_and_store(vid, Path(path))
            except Exception as exc:  # noqa: BLE001 - medir es opcional
                _log.failure(log, f"no se pudo medir el volumen de {vid}", exc)
                result = None
            self.q.put(("gain", vid, result))

        threading.Thread(target=work, daemon=True).start()

    def toggle_normalize(self) -> None:
        self.normalize = bool(self.var_normalize.get())
        if not self.normalize:
            self.player.set_gain_db(0)
            self.status.configure(text="Normalización de volumen desactivada")
            return
        self.status.configure(text="Normalización de volumen activada (ReplayGain)")
        if self.cur_track:
            local = self.cur_track.get("path") or (
                self.lib_by_id.get(self.cur_track["video_id"]) or {}).get("path")
            if local:
                self._set_track_gain(self.cur_track, local, current=True)

    def play_pause(self) -> None:
        if not self.player.loaded:
            if self.resume_track is not None:
                track = self.resume_track
                position = self.resume_position
                self.resume_track = None
                self.resume_position = 0
                self.pending_seek = (track["video_id"], position)
                self._play_track(track)
                return
            sel = self.tree.selection()
            self.play_index(int(sel[0]) if sel else 0)
            return
        self.player.toggle()
        self.btn_play.configure(text="▶" if self.player.paused else "⏸")

    def step(self, d: int, auto: bool = False) -> None:
        how = "auto" if auto else "manual"
        if auto and self.pq.repeat == 2 and self.playing_vid:
            cur = self.pq.current(self.playing_vid)
            if cur:
                self._play_track(cur, how)
                return
        if d > 0 and self.pq.up_next:
            self._play_track(self.pq.up_next.pop(0), how)
            return
        npos = self.pq.skip_remote(self.pq.opos + d, d)
        if d > 0 and self.pq.auto_ctx and self.autoplay and not 0 <= npos < len(self.pq.order):
            self._fill_backup()  # se acabó la cola antes de que llegaran más similares
            npos = self.pq.skip_remote(self.pq.opos + d, d)
        if not self.pq.order or not 0 <= npos < len(self.pq.order):
            if self.pq.order and self.pq.repeat >= 1:
                npos %= len(self.pq.order)
            else:
                if auto:  # fin de la lista
                    self.player.stop()
                    self.btn_play.configure(text="▶")
                return
        self.pq.opos = npos
        self._play_track(self.pq.ctx[self.pq.order[npos]], how)

    # ---------- transiciones ----------
    def _peek_next(self) -> dict | None:
        """La canción que `step(1)` elegiría ahora, sin cambiar nada."""
        return self.pq.peek_next(self.cur_track)

    def _xfade_for(self, cur: dict | None, nxt: dict) -> int:
        """Duración del crossfade entre dos canciones. 0 = paso sin hueco (gapless)."""
        x = self.xfade_ms
        if x <= 0 or cur is None:
            return 0
        if cur.get("album") and cur.get("album") == nxt.get("album"):
            return 0  # pistas del mismo álbum: suelen enlazar entre sí, no se pisan
        for ms in (self.player.length, (nxt.get("duration") or 0) * 1000):
            if ms:
                x = min(x, int(ms * 0.3))  # una canción corta no se pierde en la mezcla
        return max(x, 0)

    def _ensure_ready(self, t: dict) -> bool:
        """Deja la siguiente abierta en el reproductor para que arranque sin demora."""
        vid = t["video_id"]
        if self.player.is_prepared(vid):
            return True
        local = t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")
        try:
            if local and Path(local).exists():
                self.player.prepare_path(vid, local)
                return True
            from ..core import streaming
            url = streaming.cached(vid)
            if url:
                self.player.prepare(vid, url)
                return True
        except Exception as exc:  # noqa: BLE001 - sin preparar, la transición simplemente no se anticipa
            _log.failure(log, f"no se pudo dejar lista {vid}", exc)
            return False
        self._resolve_ahead([vid])
        return False

    def _fade_tick(self) -> None:
        """Cada ~40 ms: mueve los fundidos y lanza la mezcla cuando toca (antes del final real)."""
        try:
            self.player.update_fades()
            self._maybe_transition()
        finally:
            self.after(FADE_TICK_MS, self._fade_tick)

    def _maybe_transition(self) -> None:
        p = self.player
        if not p.loaded or p.paused or self.seeking:
            return
        if self.transitioning_since is not None or self.auto_fired == self.playing_vid:
            return
        pos = p.position()  # también lee la duración
        if not p.length:
            return
        rem = p.length - pos
        if rem > PREPARE_WINDOW_MS:
            return
        nxt = self._peek_next()
        if nxt is None:
            return
        ready = self._ensure_ready(nxt)
        x = self._xfade_for(self.cur_track, nxt)
        if ready and rem <= max(x, 0) + (LEAD_MS if x else GAPLESS_LEAD_MS):
            self.auto_fired = self.playing_vid
            self.step(1, auto=True)

    def cycle_xfade(self) -> None:
        i = XFADE_CHOICES.index(self.xfade_ms) if self.xfade_ms in XFADE_CHOICES else 0
        self.xfade_ms = XFADE_CHOICES[(i + 1) % len(XFADE_CHOICES)]
        self._refresh_toggles()
        self.status.configure(text="Transición: " + (
            f"crossfade de {self.xfade_ms // 1000} s (sin hueco entre pistas del mismo álbum)"
            if self.xfade_ms else "sin crossfade, paso sin hueco (gapless)"))

    def toggle_shuffle(self) -> None:
        self.pq.shuffle = not self.pq.shuffle
        if self.pq.ctx and self.pq.order:
            self.pq.make_order(self.pq.order[self.pq.opos])
        self._refresh_toggles()

    def cycle_repeat(self) -> None:
        self.pq.repeat = (self.pq.repeat + 1) % 3
        self._refresh_toggles()

    def _refresh_toggles(self) -> None:
        self.btn_shuffle.set_on(self.pq.shuffle)
        self.btn_repeat.set_icon("repeat1" if self.pq.repeat == 2 else "repeat")
        self.btn_repeat.set_on(bool(self.pq.repeat))
        self.btn_auto.set_on(self.autoplay)
        self.btn_xfade.set_on(bool(self.xfade_ms))

    def _seek_release(self, _e) -> None:
        self.seeking = False
        if self.player.loaded and self.player.length:
            self.player.seek(self.scale.get() / 1000 * self.player.length)

    def _tick(self) -> None:
        now = time.monotonic()
        delta, self._tick_at = now - self._tick_at, now
        if self.player.loaded:
            try:
                self.player.check()
                self.player.position()  # también lee la duración, que la barra de progreso necesita
                if self.player.finished():
                    since = self.transitioning_since
                    if since is None or time.monotonic() - since > TRANSITION_TIMEOUT_S:
                        self.step(1, auto=True)  # fin natural sin mezcla anticipada
                elif not self.seeking and self.player.length:
                    pos = self.player.position()
                    t = self.stream_t
                    if (self.playing_vid and not self.player.paused and not self._listen_counted
                            and self.cur_track and self.cur_track["video_id"] == self._listen_vid):
                        self._listen_s += min(max(delta, 0.0), 1.0)
                        duration = (self.player.length / 1000 if self.player.length
                                    else self.cur_track.get("duration"))
                        if history.counts_as_play(self._listen_s, duration):
                            self._listen_counted = True
                            try:
                                history.record(self.cur_track)
                            except Exception as exc:  # noqa: BLE001 - el historial no para la música
                                _log.failure(log, "no se pudo registrar la reproducción", exc)
                            if self.view_kind in ("recent", "top"):
                                self.refresh_view()  # la vista abierta refleja la escucha ya
                    if t and not self.stream_backed and pos > BACKUP_AFTER_MS:
                        # respaldo automático: solo de lo que llevas un rato escuchando
                        self.stream_backed = True
                        if self._is_remote(t) and t["video_id"] not in self.dl.inflight:
                            self.dl.start_download(t, quiet=True)
                            self.status.configure(text="Streaming · guardándola en tu biblioteca…")
                    self.scale.set(pos / self.player.length * 1000)
                    self.time.configure(
                        text=f"{fmt(pos / 1000)} / {fmt(self.player.length / 1000)}")
                    if pos != self.last_pos[0] or self.player.paused:
                        self.last_pos = (pos, now)
                    elif t and not self.online and now - self.last_pos[1] > STALL_S:
                        # el streaming se quedó sin datos y no hay internet: sigue la cola de respaldo
                        self.last_pos = (0, now)
                        self.status.configure(text="Sin conexión: pasando a la cola de respaldo")
                        self.step(1, auto=True)
            except RuntimeError as exc:
                self.player.stop()
                t, self.stream_t = self.stream_t, None
                if t and t["video_id"] == self.playing_vid and not self._check_online():
                    self.status.configure(text="Sin conexión: pasando a la cola de respaldo")
                    self.step(1, auto=True)
                elif t and t["video_id"] == self.playing_vid:  # falló el streaming: vía lenta de reserva
                    self._download_and_play(t, self.play_token)
                else:
                    self.btn_play.configure(text="▶")
                    self.status.configure(text=f"No se pudo reproducir: {exc}")
        self.after(400, self._tick)

    # ---------- portada ----------
    def _load_cover(self, t: dict, path: str | None, token: int) -> None:
        def work() -> None:
            data = None
            try:
                if path:
                    from mutagen.mp4 import MP4
                    covr = (MP4(path).tags or {}).get("covr")
                    data = bytes(covr[0]) if covr else None
                elif t.get("cover"):
                    with urllib.request.urlopen(t["cover"], timeout=10) as resp:
                        data = resp.read()
            except Exception as exc:  # noqa: BLE001 - sin portada no pasa nada
                _log.failure(log, f"sin portada para {t['video_id']}", exc)
                data = None
            self.q.put(("cover", token, data))

        threading.Thread(target=work, daemon=True).start()

    def _set_cover(self, data: bytes | None) -> None:
        """Muestra la portada y tiñe la ventana con su color (sin portada, uno propio de la canción)."""
        from PIL import Image
        self._cover_data = data
        img = None
        if data:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as exc:  # noqa: BLE001 - imagen corrupta: se usa el degradado
                _log.failure(log, "portada ilegible", exc)
        seed = self.cur_track["video_id"] if self.cur_track else None
        self.cover.show(img, seed)
        self._set_ambient((dominant_color(img) if img else None)
                          or (seed_color(seed) if seed else None))

    # ---------- errores ----------
    def report_callback_exception(self, exc, val, tb) -> None:
        """Captura los fallos de callbacks de Tk: sin esto, en el .exe no se ve nada."""
        _log.failure(_log.get("ui"), "fallo en la interfaz", val)
        details = "".join(traceback.format_exception(exc, val, tb))
        self._show_crash("La ventana encontró un error, pero puedes seguir usándola.", details)

    def _show_crash(self, message: str, details: str = "") -> None:
        """Muestra el fallo y enseña dónde está el registro. Como mucho, una vez cada 5 s."""
        if self._crash_shown:
            return
        self._crash_shown = True
        try:
            messagebox.showerror(
                "Sound",
                f"{message}\n\nEl detalle completo está en:\n{config.LOG_PATH}",
                parent=self)
            if messagebox.askyesno("Sound", "¿Abrir la carpeta del registro?", parent=self):
                health.open_path(config.LOG_PATH.parent)
        except Exception:  # noqa: BLE001 - si ni el diálogo funciona, al menos queda el log
            pass
        finally:
            self.after(5000, lambda: setattr(self, "_crash_shown", False))

    # ---------- cola de eventos de hilos ----------
    def _pump(self) -> None:
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "results":
                    if ev[1] == self.search_id:
                        self.searching = False
                        self.remote, self.remote_src, self.remote_q = ev[2], ev[3], ev[4]
                        self.refresh_view()
                        self._fade_rows()
                        if not self.yt_genres:  # la conexión volvió: carga los géneros
                            self._load_yt_genres()
                        # resuelve ya las URLs de los primeros resultados: al dar play, sin espera
                        self._resolve_ahead(
                            [t["video_id"] for t in self.rows if self._is_remote(t)][:AHEAD_RESULTS])
                elif kind == "remote_error":
                    if ev[1] == self.search_id:
                        self.searching = False
                        self.refresh_view()
                        self.status.configure(
                            text=f"Sin conexión con YouTube: solo biblioteca  ({ev[2][:60]})")
                elif kind == "status":
                    self.status.configure(text=ev[1])
                elif kind == "genres":
                    self.yt_failed = ev[1] is None
                    self.yt_genres = ev[1] or []
                    self._build_sidebar()
                elif kind == "stream":
                    if ev[1] == self.play_token:
                        if len(ev) > 4:  # URL de streaming
                            self._start(ev[2], ev[1], url=ev[3], cover=False)
                        else:  # archivo local (vía de reserva)
                            self._start(ev[2], ev[1], path=ev[3], cover=False)
                            self.status.configure(text="Reproduciendo · guardada en tu biblioteca")
                elif kind == "radio":
                    self._on_radio(ev[1], ev[2])
                elif kind == "radio_err":
                    self._on_radio_error(ev[1], ev[2])
                elif kind == "online":
                    self._on_online(ev[1])
                elif kind == "fallback":
                    if ev[1] == self.play_token:
                        self._download_and_play(ev[2], ev[1])
                elif kind == "resolved":
                    self._prepare(ev[1])
                elif kind == "wave":
                    self.scale.set_levels(ev[1], ev[2])
                elif kind == "cover":
                    if ev[1] == self.play_token:
                        self._set_cover(ev[2])
                elif kind == "queue_paused":
                    self._queue_paused_changed(ev[1])
                elif kind == "needs_cookies":
                    self.status.configure(text=ev[1])
                    if not self._cookies_dialog_shown:
                        self._cookies_dialog_shown = True
                        messagebox.showinfo(
                            "YouTube pide iniciar sesión",
                            "YouTube está pidiendo iniciar sesión o verificar que no eres un "
                            "bot.\n\nAbre «Descargas» y elige, en «Cookies de YouTube», el "
                            "navegador donde tengas la sesión de YouTube iniciada. Después "
                            "pulsa «Reintentar».\n\nEl detalle queda en el registro.",
                            parent=self)
                elif kind == "crash":
                    self._show_crash(ev[1], ev[2])
                elif kind == "health":
                    page = self.pages.get("health")
                    if page is not None:
                        page.set_checks(ev[1])
                elif kind == "update_done":
                    if ev[1] == 0:
                        self.status.configure(text="Componentes actualizados. Reinicia la ventana "
                                                    "para usar la versión nueva.")
                    else:
                        self.status.configure(
                            text=f"No se pudo actualizar: {ev[2] or 'falló la descarga'}")
                elif kind == "gain":
                    self._gain_pending.discard(ev[1])
                    if self.normalize and self.playing_vid == ev[1] and ev[2] is not None:
                        self.player.set_gain_db(ev[2])
                elif kind == "dl_state":
                    self._set_state(ev[1], ev[2])
                elif kind == "dl_progress":
                    self._set_state(ev[1], f"{ev[2]}%")
                elif kind in ("downloaded", "dl_error"):
                    self.dl.inflight.discard(ev[1])
                    self.dl.quiet.discard(ev[1])
                    if kind == "downloaded":
                        self._reload_library()
                        iid = self.vid_iid.get(ev[1])
                        if iid is not None and ev[1] in self.lib_by_id:  # la fila pasa a ser local
                            self.rows[int(iid)]["path"] = self.lib_by_id[ev[1]]["path"]
                        if ev[1] in self.lib_by_id:  # ya hay audio que medir: su onda crece aquí
                            self.waves.want(ev[1], self.lib_by_id[ev[1]]["path"],
                                            urgent=ev[1] == self.playing_vid)
                        self._set_state(ev[1], "✓")
                        self._retag(ev[1])
                        msg = f"Descargada: {ev[2]}"
                    elif len(ev) > 3 and ev[3]:  # precarga fallida: sin ruido, se reintenta al reproducir
                        self.dl.state.pop(ev[1], None)
                        iid = self.vid_iid.get(ev[1])
                        if iid is not None and self.tree.exists(iid):
                            self.tree.set(iid, "state", "☁")
                        continue
                    else:
                        self._set_state(ev[1], "✗")
                        msg = f"Error — {ev[2]}"
                    if self.dl.queue_left() == 0:
                        msg += "  ·  listo"
                        self._build_sidebar()  # actualiza los contadores por género
                    self.status.configure(text=msg)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    # ---------- preferencias ----------

    def _prefs_state(self) -> dict:
        return {"dark": self.dark, "vol": self.vol.get(), "shuffle": self.pq.shuffle,
                "repeat": self.pq.repeat, "auto": self.autoplay, "xfade": self.xfade_ms,
                "normalize": self.normalize, "cookies": self.cookies}

    def _close(self) -> None:
        self._save_session()
        prefs.save(self._prefs_state())
        for page in self.pages.values():
            page.deactivate()
        self.player.close()
        self.dl.close()
        self.play_pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def run() -> None:
    try:
        config.prepare()
    except OSError as exc:
        from tkinter import messagebox

        messagebox.showerror(
            "Sound", f"No se pudo crear la carpeta de datos:\n{config.DATA_DIR}\n\n{exc}")
        return
    _log.setup()  # sin esto, los errores de la ventana no llegaban a sound.log
    try:
        report = config.migrate_legacy()
    except Exception as exc:  # noqa: BLE001 - una migración rota no debe impedir abrir la app
        _log.failure(_log.get("start"), "la migración de datos falló", exc)
        report = {"moved": [], "failed": [str(exc)]}
    for path in report["moved"]:
        _log.get("start").info("migrado a la carpeta de datos: %s", path)
    for problem in report["failed"]:
        _log.get("start").warning("no se pudo migrar: %s", problem)
    _log.session_header()

    from .single_instance import SingleInstance

    guard = SingleInstance()
    if not guard.acquire():
        return
    enable_dpi_awareness()
    from .diagnostics import install

    try:
        app = App()
    except Exception as exc:  # noqa: BLE001 - sin ventana, al menos un mensaje y el registro
        _log.failure(_log.get("ui"), "no se pudo crear la ventana", exc)
        from tkinter import messagebox

        messagebox.showerror(
            "Sound",
            f"No se pudo abrir Sound.\n\n{exc}\n\nDetalle completo en:\n{config.LOG_PATH}")
        raise
    install(app)
    app.mainloop()
