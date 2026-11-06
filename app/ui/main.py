"""Ventana principal: biblioteca local + YT Music (por género) + reproductor."""
import io
import itertools
import math
import queue
import threading
import time
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import (
    config,
    covers,
    duplicates,
    favorites,
    health,
    history,
    library,
    loudness,
    startup,
    waveform,
)
from ..core import cookies as cookies_store
from ..core import log as _log
from ..core import lyrics as lyrics_store
from ..core.filters import fold
from . import prefs, session
from .anim import Animator, ease_in_out
from .cover_grid import CoverGrid
from .downloads import DownloadsController
from .events import AHEAD_RESULTS, EventLoopMixin
from .library_ui import LibraryMixin
from .lyrics_view import LyricsView
from .playback import FADE_TICK_MS, PlaybackMixin, fmt
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
    bind_touchpad,
    touchpad_deltas,
    ui_fonts,
)

log = _log.get("ui")
FONT = (FONT_FAMILY, 10)
SIDE_W = 200  # ancho de la barra de géneros, en píxeles a 96 ppp
DRAWER_W = 340  # ancho del panel «A continuación»
XFADE_CHOICES = (0, 2000, 4000, 6000, 8000, 12000)  # ms; 0 = gapless
MANUAL_FADE_MS = 280       # fundido al saltar de canción a mano
INTRO_FADE_MS = 150        # entrada al empezar desde silencio
TRANSITION_TIMEOUT_S = 25
NET_POLL_S = 6  # cada cuánto se comprueba si hay internet
STALL_S = 8      # segundos sin avanzar (sin internet) antes de saltar a la cola de respaldo
BACKUP_AFTER_MS = 10_000  # una canción en streaming se guarda tras escucharla este tiempo
AUTO_UPDATE_DELAY_MS = 20_000       # dejar arrancar la ventana antes de buscar actualizaciones
AUTO_UPDATE_RETRY_MS = 10 * 60_000  # si hay descargas en curso, reintentar más tarde
SEARCH_DEBOUNCE_MS = 200  # búsqueda automática en YT Music al dejar de escribir
SEARCH_MIN_CHARS = 3      # menos que esto no merece una petición
SEARCH_FIRST_PAGE = 20    # lo que se pide primero: una sola petición, resultados ya
SEARCH_LIMIT = 40         # el resto se completa luego, sin hacer esperar
COLS = (("title", "Título", 280), ("artist", "Artista", 170), ("album", "Álbum", 170),
        ("dur", "Dur.", 50), ("state", "", 84))


def load_library() -> list[dict]:
    """La biblioteca descargada, sin las canciones cuyo archivo ya no existe en disco."""
    return [t for t in library.all_tracks() if Path(t["path"]).exists()]


def _sort_key(col: str):
    if col == "dur":
        return lambda t: t.get("duration") or 0
    return lambda t: (t.get(col) or "").lower()


class App(tk.Tk, LibraryMixin, PlaybackMixin, EventLoopMixin):
    def __init__(self) -> None:
        super().__init__()
        self.pq = PlayQueue(self._is_remote, lambda: self.online)
        self.dl = DownloadsController(self)
        self.title("Sound")
        self._set_app_icon()
        self.attributes("-alpha", 0.0)  # entra con un fundido al terminar de construirse
        self.k = max(1.0, self.winfo_fpixels("1i") / 96)  # escala de la pantalla
        self.geometry(f"{int(1120 * self.k)}x{int(720 * self.k)}")
        self.minsize(int(880 * self.k), int(560 * self.k))
        self.player = Player()
        self.q: queue.Queue = queue.Queue()
        # hilos fijos para reproducir: conservan su yt-dlp ya preparado y no esperan tras las precargas
        self.play_pool = ThreadPoolExecutor(2)
        # portadas de la cuadrícula: pocas a la vez y solo las visibles
        self.thumb_pool = ThreadPoolExecutor(2, thread_name_prefix="cover")
        self._thumbs_pending: set[str] = set()
        self.library: list[dict] = []
        self.lib_by_id: dict[str, dict] = {}
        self._search_cache: dict[str, str] = {}  # texto normalizado por canción, para buscar sin repetir
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
        self._remote_inflight: tuple[str, float] | None = None  # búsqueda de YT en curso
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
        self._preview_vid: str | None = None  # próxima canción cuyo color ya se está anticipando
        self._energy_cache: dict[str, float | None] = {}
        self.scheme: dict[str, str] = {}
        self.dl_paused = False
        self.disk_level = "ok"  # ok | low (una descarga a la vez) | critical (en pausa)
        self.disk_free: int | None = None
        self._disk_dialog_shown = False
        self.damaged: set[str] = set()  # archivos locales incompletos: se conservan y se pueden rehacer
        self.prefs = prefs.load()
        self.view_mode = "grid" if str(self.prefs.get("view", "list")).lower() == "grid" \
            else "list"
        self.cookies = str(self.prefs.get("cookies") or config.COOKIES_FROM_BROWSER or "").strip().lower()
        # Descargas y streaming comparten el navegador: se aplica antes de resolver nada.
        cookies_store.configure(self.cookies)
        self._cookies_dialog_shown = False
        self._crash_shown = False
        self.dark = self.prefs.get("dark", system_is_dark())
        self._last_volume = float(self.prefs.get("vol", 80))
        self.auto_update = bool(self.prefs.get("auto_update", True))
        try:
            self._ytdlp_checked_at = float(self.prefs.get("ytdlp_checked_at", 0) or 0)
        except (TypeError, ValueError):
            self._ytdlp_checked_at = 0.0
        self._auto_update_running = False
        self.start_with_windows = startup.is_enabled()
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
        self.queue_why: dict[str, str] = {}  # por qué la cola automática eligió cada canción
        self.radio_seed: dict | None = None  # canción desde la que se pidieron las similares
        self.drawer_mode = "queue"  # queue | lyrics
        self.lyrics_enabled = bool(self.prefs.get("lyrics", False))
        self.var_lyrics = tk.BooleanVar(value=self.lyrics_enabled)
        self._lyrics_vid: str | None = None
        self._lyrics_payload: dict | None = None
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
        self._stream_retries: dict[str, int] = {}  # reintentos de streaming por canción
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
        threading.Thread(target=self._warm_search, daemon=True, name="calentar-busqueda").start()
        threading.Thread(target=self.dl.start_service, daemon=True).start()
        self.bind("<Control-g>", lambda e: (self.open_prompt(), "break")[1])  # «Música por frase»
        self.bind("<Control-l>", lambda e: (self.toggle_like(), "break")[1])  # Me gusta
        self.bind("<Control-f>", lambda e: (self.search_box.entry.focus_set(), "break")[1])
        self.bind("<Control-a>", self._select_all)  # todas las canciones de la vista
        self.bind("<Control-Shift-L>", self._toggle_view_key)
        self.bind("<Control-comma>", lambda e: (self.open_settings(), "break")[1])  # ajustes
        self.bind("<F1>", lambda e: (self.open_settings(), "break")[1])  # ayuda y atajos
        self.bind("<Control-m>", self._toggle_mute)  # silenciar
        self.bind("<Control-Up>", lambda e: self._volume_step(5))
        self.bind("<Control-Down>", lambda e: self._volume_step(-5))
        self.bind("<MouseWheel>", self._wheel_anywhere, add="+")
        bind_touchpad(self, self._touchpad_anywhere, add=True)
        self.bind("<Escape>", self._escape)
        self.play_pool.submit(self._warm_up)
        threading.Thread(target=self._net_loop, daemon=True).start()
        self.after(FADE_TICK_MS, self._fade_tick)
        self.after(100, self._pump)
        self.after(400, self._tick)
        self.after(1200, self._check_environment)
        self.after(AUTO_UPDATE_DELAY_MS, self._maybe_auto_update)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.anim.tween("window", 260, lambda p: self.attributes("-alpha", p),
                        done=lambda: self.attributes("-alpha", 1.0))

    # ---------- construcción ----------
    def _set_app_icon(self) -> None:
        """Icono de la ventana y de los diálogos (sin él Tk pondría el de Python/Tk)."""
        icon = config.icon_path()
        if icon is None:
            return
        try:
            self.iconbitmap(str(icon))
            self.iconbitmap(default=str(icon))  # también para los diálogos
            return
        except tk.TclError as exc:  # .ico no soportado o archivo ilegible
            _log.failure(log, "no se pudo usar el icono .ico", exc)
        png = icon.with_suffix(".png")
        try:
            if png.exists():
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(False, self._icon_photo)  # esta ventana
                self.iconphoto(True, self._icon_photo)   # futuros diálogos
        except Exception as exc:  # noqa: BLE001 - el icono es cosmético
            _log.failure(log, "no se pudo usar el icono .png", exc)

    def _build(self) -> None:
        k = self.k
        self.anim = Animator(self)
        if "reduce_motion" in self.prefs:  # preferencia explícita; si no, la del sistema
            self.anim.reduced = bool(self.prefs["reduce_motion"])
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
        self._mark_hover = False
        self.mark = tk.Label(top, text="Sound", font=f["mark"], anchor="w", cursor="hand2")
        self.mark.grid(row=0, column=0, sticky="w", padx=(int(24 * k), 0))
        self.mark.bind("<Button-1>", self.go_home)
        self.mark.bind("<Enter>", self._mark_enter)
        self.mark.bind("<Leave>", self._mark_leave)
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
            tip=lambda: ("Descargas en pausa" if self.dl_paused else
                         "Descargas limitadas: poco espacio" if self.disk_level != "ok" else
                         "Descargas"))
        self.btn_downloads.grid(row=0, column=7, padx=(int(2 * k), 0))
        self.btn_import = IconButton(top, self.anim, k, "link", self.open_import,
                                     tip="Importar álbum, playlist o artista (URL)")
        self.btn_import.grid(row=0, column=5, padx=(int(6 * k), 0))
        self.btn_prompt = IconButton(top, self.anim, k, "note", self.open_prompt,
                                     tip="Música por frase (Ctrl+G)")
        self.btn_prompt.grid(row=0, column=6, padx=(int(2 * k), 0))
        self.btn_settings = IconButton(top, self.anim, k, "settings", self.open_settings,
                                       tip="Ajustes y atajos (Ctrl+,)")
        self.btn_settings.grid(row=0, column=10, padx=(int(2 * k), int(14 * k)))
        self.btn_theme = IconButton(top, self.anim, k, "theme", self.toggle_theme,
                                    tip="Tema claro u oscuro")
        self.btn_theme.grid(row=0, column=9, padx=(int(2 * k), 0))

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
            bind_touchpad(w, self.genres._touchpad)

        mid = tk.Frame(content)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, int(10 * k)))
        mid.rowconfigure(1, weight=1)
        mid.columnconfigure(0, weight=1)
        self.mid = mid
        # vista de lista o cuadrícula y acciones de la biblioteca: arriba a la derecha
        self.list_tools = tk.Frame(mid)
        self.list_tools.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.btn_more = IconButton(self.list_tools, self.anim, k, "tools", self.open_actions,
                                   tip="Más acciones de la biblioteca", size=32, glyph=18)
        self.btn_more.pack(side="right", padx=(0, int(6 * k)), pady=(int(2 * k), int(2 * k)))
        self.btn_view = IconButton(
            self.list_tools, self.anim, k, "grid", self.toggle_view_mode, size=32, glyph=18,
            tip=lambda: "Vista de lista (Ctrl+Shift+L)"
            if self.view_mode == "grid" else "Vista de cuadrícula (Ctrl+Shift+L)")
        self.btn_view.pack(side="right", padx=(0, int(2 * k)), pady=(int(2 * k), int(2 * k)))
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
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.sb.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)
        self.tree.bind("<Button-3>", self._context)
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Motion>", self._row_hover)
        self.tree.bind("<Leave>", lambda e: self._set_hover_row(None))
        self.grid = CoverGrid(mid, self.anim, k, f, on_select=self._grid_selected,
                              on_activate=self.play_index, on_context=self._grid_context,
                              on_need_cover=self._request_thumb)
        self.grid.grid(row=1, column=0, sticky="nsew")
        self.grid.grid_remove()
        self.grid.bind("<Delete>", lambda e: self.delete_selected())
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
        self.drawer_tabs = tk.Frame(self.drawer)
        self.drawer_tabs.pack(fill="x", padx=int(16 * k), pady=(0, int(8 * k)))
        self.tab_queue = tk.Label(self.drawer_tabs, text="Cola", font=f["small"], cursor="hand2")
        self.tab_lyrics = tk.Label(self.drawer_tabs, text="Letra", font=f["small"], cursor="hand2")
        self.tab_queue.pack(side="left")
        self.tab_lyrics.pack(side="left", padx=(int(14 * k), 0))
        self.tab_queue.bind("<Button-1>", lambda _e: self._set_drawer_mode("queue"))
        self.tab_lyrics.bind("<Button-1>", lambda _e: self._set_drawer_mode("lyrics"))
        self.lyrics_view = LyricsView(self.drawer, k, f)
        self.drawer_hint = tk.Label(
            self.drawer, text="Arrastra para ordenar · clic derecho o Supr para quitar",
            font=f["small"], anchor="w")
        self.drawer_hint.pack(fill="x", padx=int(16 * k), pady=(0, int(8 * k)))
        self.drawer_list = RowList(self.drawer, self.anim, k, f, multi=False, row=42,
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
        self.eq = Equalizer(mid, self.anim, k, playing, level=lambda: self.scale.pulse)
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
        self.vol = Slider(right, self.anim, k, command=self._on_volume)
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
        self.top_icons = (self.btn_import, self.btn_prompt, self.btn_downloads,
                          self.btn_theme, self.btn_settings)
        self.bar_icons = (self.btn_shuffle, self.btn_prev, self.btn_next, self.btn_repeat,
                          self.vol_icon, self.btn_queue, self.btn_xfade, self.btn_auto)
        self.vol.set(self.prefs.get("vol", 80))

        self.bind("<space>", self._space)
        self.bind("<Control-f>", lambda e: (self.entry.focus_set(), "break"))
        self._refresh_toggles()

    def _place_eq(self) -> None:
        """Coloca el ecualizador sobre la fila que suena, con el fondo que tenga esa fila."""
        if self.view_mode == "grid":
            # la cuadrícula dibuja su propio ecualizador encima de la portada
            self.eq.place_forget()
            self._eq_spot = None
            return
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

    # ---------- vista de lista o cuadrícula ----------
    def _toggle_view_key(self, _e=None) -> str | None:
        # mientras se escribe, Ctrl+Shift+L no debe cambiar la vista
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None
        self.toggle_view_mode()
        return "break"

    def toggle_view_mode(self) -> None:
        self._set_view("list" if self.view_mode == "grid" else "grid")
        self.save_prefs()

    def _set_view(self, mode: str) -> None:
        self.view_mode = "grid" if mode == "grid" else "list"
        if self.view_mode == "grid":
            self.tree.grid_remove()
            self.tree.configure(yscrollcommand=lambda *_a: None)
            self.grid.grid(row=1, column=0, sticky="nsew")
            self.sb.configure(command=self._grid_scrollbar)
            self.grid.configure(yscrollcommand=self._grid_scrolled)
            self.grid.set_context(liked=self.liked, damaged=self.damaged, states=self.dl.state,
                                  view_kind=self.view_kind)
            self.grid.set_items(self.rows)
            self.grid.set_playing(self.playing_vid)
            self.grid.select(self.tree.selection())
        else:
            self.grid.grid_remove()
            self.grid.configure(yscrollcommand=lambda *_a: None)
            self.tree.grid(row=1, column=0, sticky="nsew")
            self.sb.configure(command=self.tree.yview)
            self.tree.configure(yscrollcommand=self._tree_scrolled)
            self._tree_scrolled(*self.tree.yview())
        self.btn_view.set_icon("grid" if self.view_mode == "list" else "list")
        self._place_eq()
        self.empty.lift()

    def _grid_scrolled(self, first: str, last: str) -> None:
        """La barra de la cuadrícula solo aparece si hay algo que desplazar."""
        self.sb.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.sb.grid_remove()
        else:
            self.sb.grid()

    def _grid_scrollbar(self, *args) -> None:
        self.grid.scrollbar(*args)

    def _grid_selected(self, iids) -> None:
        """La cuadrícula cambió la selección: se refleja en el Treeview, que manda."""
        if iids:
            self.tree.selection_set(tuple(iids))
        else:
            self.tree.selection_remove(*self.tree.selection())
        self._on_select()

    def _grid_context(self, i: int, x_root: int, y_root: int) -> None:
        self._context_at(str(i), x_root, y_root)

    def _request_thumb(self, track: dict) -> bool:
        """Pide una portada en segundo plano; el resultado vuelve por la cola de eventos."""
        vid = track.get("video_id")
        if not vid:
            return False
        if vid in self._thumbs_pending:
            return True
        if len(self._thumbs_pending) >= 10:  # la cuadrícula reintenta al repintar
            return False
        self._thumbs_pending.add(vid)

        def work() -> None:
            image = None
            try:
                data = covers.load(track)
                if data:
                    from PIL import Image

                    image = Image.open(io.BytesIO(data)).convert("RGB")
                    image.thumbnail((512, 512), Image.LANCZOS)
            except Exception as exc:  # noqa: BLE001 - sin portada se usa el degradado
                _log.failure(log, f"no se pudo leer la portada de {vid}", exc)
                image = None
            self.q.put(("thumb", vid, image))

        try:
            self.thumb_pool.submit(work)
        except RuntimeError:  # la ventana se está cerrando
            self._thumbs_pending.discard(vid)
            return False
        return True

    # ---------- rueda del ratón ----------
    def _wheel_anywhere(self, e) -> str | None:
        """La rueda mueve lo que hay bajo el puntero, aunque el foco esté en otro control.

        Tk entrega la rueda al control que la recibe: si el foco está en el buscador, la lista
        no se enteraría. Este enrutador busca el control desplazable bajo el ratón y lo mueve.
        """
        target = self.winfo_containing(e.x_root, e.y_root)
        if target is None:
            return None
        if target is e.widget and isinstance(
                target, (ttk.Treeview, tk.Canvas, tk.Listbox, tk.Text)):
            return None  # ese control ya tiene su propio desplazamiento
        handler = self._wheel_target(target)
        if handler is None:
            return None
        handler(e)
        return "break"

    def _wheel_target(self, target):
        """Devuelve el manejador de rueda del control desplazable que contiene `target`."""
        w = target
        while w is not None:
            if w is self.grid:
                return self.grid._wheel
            if w is self.genres or w is self.side:
                return self.genres._wheel
            if w is self.drawer_list or (w is self.drawer and self.queue_win is not None):
                return self.drawer_list._wheel
            if w is self.mid:
                return self.grid._wheel if self.view_mode == "grid" else self._tree_wheel
            if isinstance(w, ttk.Treeview):
                return lambda e, tree=w: self._wheel_view(tree, e)
            if isinstance(w, ttk.Scrollbar):
                sibling = self._scrollbar_sibling(w)
                if sibling is self.grid:
                    return self.grid._wheel
                if isinstance(sibling, RowList):
                    return sibling._wheel
                if sibling is not None:
                    return lambda e, s=sibling: self._wheel_view(s, e)
            w = w.master
        page = self.active_page
        if page is not None and self._inside(target, page):
            handler = getattr(page, "_wheel", None)
            if callable(handler):
                return handler
        return None

    def _tree_wheel(self, e) -> str:
        return self._wheel_view(self.tree, e)

    @staticmethod
    def _wheel_steps(e) -> int:
        """Unidades de rueda; las ruedas de alta resolución mandan deltas menores de 120."""
        steps = int(e.delta / 120)
        if steps == 0 and e.delta:
            steps = 1 if e.delta > 0 else -1
        return -steps

    @classmethod
    def _wheel_view(cls, widget, e) -> str:
        try:
            widget.yview_scroll(cls._wheel_steps(e), "units")
        except tk.TclError:  # el control ya no existe
            pass
        return "break"

    # ---------- touchpad (Tk 9: dos dedos = <TouchpadScroll>, no <MouseWheel>) ----------
    def _touchpad_anywhere(self, e) -> str | None:
        """El gesto de dos dedos mueve lo que hay bajo el puntero, como la rueda."""
        target = self.winfo_containing(e.x_root, e.y_root)
        if target is None:
            return None
        if target is e.widget and isinstance(
                target, (ttk.Treeview, tk.Canvas, tk.Listbox, tk.Text)):
            return None  # los controles estándar de Tk 9 ya lo manejan solos
        handler = self._touchpad_target(target)
        if handler is None:
            return None
        handler(e)
        return "break"

    def _touchpad_target(self, target):
        w = target
        while w is not None:
            if w is self.grid:
                return self.grid._touchpad
            if w is self.genres or w is self.side:
                return self.genres._touchpad
            if w is self.drawer_list or (w is self.drawer and self.queue_win is not None):
                return self.drawer_list._touchpad
            if w is self.mid:
                return self.grid._touchpad if self.view_mode == "grid" else self._tree_touchpad
            if isinstance(w, ttk.Treeview):
                return lambda e, tree=w: self._touchpad_view(tree, e)
            if isinstance(w, ttk.Scrollbar):
                sibling = self._scrollbar_sibling(w)
                if sibling is self.grid:
                    return self.grid._touchpad
                if isinstance(sibling, RowList):
                    return sibling._touchpad
                if sibling is not None:
                    return lambda e, s=sibling: self._touchpad_view(s, e)
            w = w.master
        page = self.active_page
        if page is not None and self._inside(target, page):
            handler = getattr(page, "_touchpad", None)
            if callable(handler):
                return handler
        return None

    def _tree_touchpad(self, e) -> str:
        return self._touchpad_view(self.tree, e)

    @staticmethod
    def _touchpad_view(widget, e) -> str:
        _dx, dy = touchpad_deltas(e)
        if not dy:
            return "break"
        try:
            if isinstance(widget, tk.Canvas):
                widget.tk.call("tk::ScrollByPixels", widget._w, 0, dy)
            elif e.serial % 5 == 0:  # los Treeview/Listbox van por unidades
                widget.yview_scroll(-1 if dy > 0 else 1, "units")
        except tk.TclError:
            pass
        return "break"

    def _scrollbar_sibling(self, bar: ttk.Scrollbar):
        """La vista que controla una barra de desplazamiento, si está a la vista."""
        for sibling in bar.master.winfo_children():
            if sibling is bar or not sibling.winfo_ismapped():
                continue
            if sibling is self.grid or isinstance(
                    sibling, (RowList, ttk.Treeview, tk.Canvas, tk.Listbox, tk.Text)):
                return sibling
        return None

    def _scrollbar_target(self, bar: ttk.Scrollbar):
        sibling = self._scrollbar_sibling(bar)
        if sibling is self.grid:
            return self.grid._wheel
        if isinstance(sibling, RowList):
            return sibling._wheel
        if sibling is not None:
            return lambda e, s=sibling: self._wheel_view(s, e)
        return None

    @staticmethod
    def _inside(widget, ancestor) -> bool:
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.master
        return False

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
        for w in (self.content, self.mid, self.list_tools):
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
        self.grid.restyle(bg=p["bg"], fg=p["fg"], dim=p["dim"], faint=p["faint"],
                          sel=p["sel"], hover=p["hover"], accent=self.scheme.get(
                              "accent", p["accent"]))
        scheme = self._scheme(self._ambient)
        self._paint(scheme)
        self._ambient_settled(scheme)
        for page in self.pages.values():
            page.restyle()
        self._set_view(self.view_mode)

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
        self.mark.configure(bg=top, fg=accent if self._mark_hover else p["fg"])
        self.search_box.restyle(bg=top, field=p["raised"], fg=p["fg"], dim=p["dim"],
                                faint=p["faint"], line=blend(p["bg"], p["fg"], 0.18),
                                accent=accent)
        self.btn_dl.restyle(top, p["fg"], p["dim"])
        for b in self.top_icons:
            b.restyle(top, p["fg"], p["dim"], accent)
        self.list_tools.configure(bg=p["bg"])
        self.btn_view.restyle(p["bg"], p["fg"], p["dim"], accent)
        self.btn_more.restyle(p["bg"], p["fg"], p["dim"], accent)
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
        self.grid.set_accent(c["accent"])
        style_titlebar(self, self.dark, c["top"])
        self._style_drawer_tabs(c["accent"])
        self.lyrics_view.restyle(self.pal["panel"], self.pal["fg"], self.pal["dim"],
                                 self.pal["faint"], c["accent"])

    def _style_drawer_tabs(self, accent: str) -> None:
        p = self.pal
        self.drawer_tabs.configure(bg=p["panel"])
        for label, active in ((self.tab_queue, self.drawer_mode == "queue"),
                              (self.tab_lyrics, self.drawer_mode == "lyrics")):
            label.configure(bg=p["panel"], fg=accent if active else p["faint"])

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
        self.save_prefs()

    def toggle_reduce_motion(self) -> None:
        """Anima o no la ventana; se guarda y se aplica al momento."""
        self.anim.set_reduced(not self.anim.reduced)
        self.save_prefs()
        self.status.configure(text="Animaciones " + (
            "reducidas" if self.anim.reduced else "activadas"))

    def save_prefs(self) -> None:
        prefs.save(self._prefs_state())

    # ---------- vista unificada: biblioteca + YT Music ----------

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

    def go_home(self, _e=None) -> str:
        """Vuelve a la pantalla principal: cierra páginas/cola y muestra toda la biblioteca."""
        if self.queue_win is not None:
            self._show_queue()
        if self.genres.items:
            self.genres.selection_clear()
            self.genres.selection_set(0)  # «Todo»
            self._pick_genre()
        return "break"

    def _mark_enter(self, _e=None) -> None:
        self._mark_hover = True
        self.mark.configure(fg=self.scheme.get("accent", self.pal["fg"]))

    def _mark_leave(self, _e=None) -> None:
        self._mark_hover = False
        self.mark.configure(fg=self.pal["fg"])

    def _on_query(self) -> None:
        q = self.query.get().strip()
        if q and self.view_kind in ("liked", "recent", "top"):
            # buscar desde una vista especial busca en toda la biblioteca
            self.view_kind = "all"
            self.genre_filter = set()
            self.yt_sel = []
            self.sel_keys = {("all", None)}
            self._build_sidebar()
        self.search_id += 1  # lo que esté en vuelo ya no corresponde a lo que se está escribiendo
        self.searching = len(q) >= SEARCH_MIN_CHARS
        self._show_pre_results(q)
        self.refresh_view()
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        if len(q) >= SEARCH_MIN_CHARS:  # búsqueda automática en YT Music al dejar de escribir
            if len(q) == SEARCH_MIN_CHARS:
                # primera pista ya, mientras se sigue escribiendo: el resto se completa después
                self.search("songs", auto=True)
            self._qdebounce = self.after(
                SEARCH_DEBOUNCE_MS, lambda: self.search("songs", auto=True))

    def _show_pre_results(self, q: str) -> None:
        """Pinta al instante resultados ya vistos para esta búsqueda o para un prefijo suyo.

        Así la lista nunca se queda vacía ni parpadea al escribir: los pre-resultados ocupan
        su sitio y se sustituyen cuando llega la respuesta definitiva de YouTube.
        """
        if not q:
            if self.remote_src in ("songs", "genre"):
                self.remote, self.remote_src, self.remote_q = [], None, ""
            return
        from ..core import ytsearch
        hit = ytsearch.cached_songs(q)
        if hit is not None:
            if self.remote_src not in ("songs", "genre", None):
                return  # hay una vista abierta (géneros, importación): no se pisa
            _query, tracks = hit
            self.remote, self.remote_src, self.remote_q = tracks, "songs", q
            self.searching = False  # ya hay algo que enseñar
            return
        if self.remote_src not in ("songs", "genre"):
            return
        if len(q) < SEARCH_MIN_CHARS:
            # Sin búsqueda automática no habría quien sustituya estos resultados: se quitan.
            self.remote, self.remote_src, self.remote_q = [], None, ""
            return
        if self._related_query(q, self.remote_q):
            # La consulta nueva continúa (o recorta) la anterior: sus resultados aún sirven.
            self.remote_q = q
            return
        self.remote, self.remote_src, self.remote_q = [], None, ""

    @staticmethod
    def _related_query(a: str, b: str) -> bool:
        """¿Una consulta es prefijo de la otra? Entonces lo que ya se vio sigue siendo útil."""
        a, b = a.casefold().strip(), (b or "").casefold().strip()
        return bool(a and b and (a.startswith(b) or b.startswith(a)))

    @staticmethod
    def _warm_search() -> None:
        from ..core import ytsearch
        ytsearch.warm_up()

    def _rebuild_search_cache(self) -> None:
        self._search_cache = {
            t["video_id"]: fold(f"{t['title']} {t['artist']} {t.get('album') or ''}")
            for t in self.library
        }

    def _search_text(self, t: dict) -> str:
        """Texto normalizado de una fila; se memoriza porque la búsqueda corre en cada tecla."""
        vid = t.get("video_id")
        if vid and vid in self._search_cache:
            return self._search_cache[vid]
        text = fold(f"{t['title']} {t['artist']} {t.get('album') or ''}")
        if vid:
            self._search_cache[vid] = text
        return text

    def _upsert_track(self, track: dict) -> None:
        """Actualiza una sola canción tras una descarga, sin releer toda la biblioteca."""
        vid = track["video_id"]
        previous = self.lib_by_id.get(vid)
        if previous is None:
            self.library.append(track)
        else:
            for index, item in enumerate(self.library):
                if item["video_id"] == vid:
                    self.library[index] = track
                    break
        self.lib_by_id[vid] = track
        self._search_cache[vid] = fold(
            f"{track['title']} {track['artist']} {track.get('album') or ''}"
        )

    def refresh_view(self) -> None:
        q = self.query.get().strip()
        wanted = fold(q)

        def match(t: dict) -> bool:
            return wanted in self._search_text(t)

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
        if t["video_id"] in self.damaged:
            return "✗"
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
        if self.view_mode == "grid":
            self.grid.set_context(liked=self.liked, damaged=self.damaged, states=self.dl.state,
                                  view_kind=self.view_kind)
            self.grid.set_items(rows)
            self.grid.set_playing(self.playing_vid)
            self.grid.select(self.tree.selection())
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
            text = "Nada encontrado. Prueba con otra búsqueda."
        elif self.view_kind == "liked":
            text = "Aún no has marcado ninguna canción. Botón derecho → Me gusta."
        elif self.view_kind in ("recent", "top"):
            text = "Todavía no hay reproducciones registradas."
        else:
            text = "Aún no hay nada aquí. Busca una canción arriba para empezar."
        self.empty.configure(text=text)
        self.empty.place(relx=0.5, rely=0.42, anchor="center")
        self.empty.lift()

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
        if self.view_mode == "grid":
            self.grid.set_playing(self.playing_vid)
        self._place_eq()

    def _set_state(self, vid: str, text: str) -> None:
        self.dl.state[vid] = text
        iid = self.vid_iid.get(vid)
        if iid is not None and self.tree.exists(iid):
            self.tree.set(iid, "state", text)
        self.grid.set_state(vid, text)

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
            self._on_select()
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

    def toggle_start_with_windows(self) -> None:
        """Activa o desactiva que Sound se abra al iniciar sesión en Windows."""
        wanted = not self.start_with_windows
        if not startup.set_enabled(wanted):
            self.status.configure(
                text="No se pudo cambiar el inicio automático. Revisa los permisos del usuario.")
            return
        self.start_with_windows = wanted
        self.status.configure(text=(
            "Sound se abrirá al iniciar Windows" if wanted
            else "Sound ya no se abrirá al iniciar Windows"))

    def toggle_auto_update(self) -> None:
        """Activa o desactiva la búsqueda diaria de una versión nueva de yt-dlp."""
        self.auto_update = not self.auto_update
        self.save_prefs()
        self.status.configure(text="Actualización automática de yt-dlp " + (
            "activada" if self.auto_update else "desactivada"))
        if self.auto_update:
            self._maybe_auto_update()

    def _maybe_auto_update(self, force: bool = False) -> None:
        """Busca una versión nueva de yt-dlp sin bloquear la ventana.

        Como mucho una vez al día. En desarrollo espera a que no haya descargas, porque pip
        reemplaza archivos que un trabajador podría estar importando. Empaquetada descarga a
        `vendor/`, que no toca lo que ya está en marcha y se usa al reiniciar.
        """
        if not self.auto_update or self._auto_update_running:
            return
        if not force and time.time() - self._ytdlp_checked_at < health.AUTO_UPDATE_INTERVAL_S:
            return
        if not health.is_frozen() and self.dl.queue_left() > 0:
            self.after(AUTO_UPDATE_RETRY_MS, self._maybe_auto_update)
            return
        self._auto_update_running = True

        def work() -> None:
            try:
                checked, version = health.auto_update_ytdlp()
                self.q.put(("auto_update", checked, version, force))
            except Exception as exc:  # noqa: BLE001 - nunca debe tumbar la ventana
                _log.failure(log, "la actualización automática de yt-dlp falló", exc)
                self.q.put(("auto_update", False, None, force))

        threading.Thread(target=work, daemon=True, name="actualizar-ytdlp").start()

    def after_tag_edit(self, updated: dict) -> None:
        vid = updated["video_id"]
        self.refresh_library()
        self.grid.forget_cover(vid)  # la carátula incrustada pudo cambiar con las etiquetas
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

    def _search_in_flight(self, q: str) -> bool:
        """¿Ya hay una petición en curso para esta misma consulta? No se duplica."""
        running = self._remote_inflight
        return bool(running and running[0] == q and time.monotonic() - running[1] < 15)

    def search(self, kind: str = "songs", auto: bool = False) -> None:
        q = self.query.get().strip()
        if not q:
            return
        if kind == "songs" and self._search_in_flight(q):
            return
        if self.active_page is not None:
            self.close_page()
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        self.searching = True
        self.status.configure(text="Buscando en YouTube…")
        self.search_id += 1
        sid = self.search_id
        marker = None
        if kind == "songs":
            marker = self._remote_inflight = (q, time.monotonic())

        def work() -> None:
            try:
                if sid != self.search_id:  # mientras se lanzaba el hilo se escribió otra cosa
                    return
                from ..core import genres, ytsearch
                if kind == "genre":
                    # solo canciones nuevas: lo que ya está descargado no ocupa sitio en los 100
                    skip_ids, skip_songs = genres.known_from_library(include_queue=False)
                    label, tracks = genres.select_tracks(
                        q, 100, skip_ids=skip_ids, skip_songs=skip_songs)
                    self._tag_results(tracks, label)
                    self.q.put(("results", sid, tracks, kind, q, True))
                    return
                # Primero una sola página (una petición): resultados visibles cuanto antes.
                tracks = self._tag_results(ytsearch.search_songs(q, SEARCH_FIRST_PAGE), None)
                if sid != self.search_id:
                    return
                if len(tracks) < SEARCH_FIRST_PAGE or SEARCH_LIMIT <= SEARCH_FIRST_PAGE:
                    self.q.put(("results", sid, tracks, kind, q, True))
                    return
                self.q.put(("results", sid, tracks, kind, q, False))
                if sid != self.search_id:
                    return
                full = self._tag_results(ytsearch.search_songs(q, SEARCH_LIMIT), None)
                self.q.put(("results", sid, full, kind, q, True))
            except Exception as exc:  # noqa: BLE001 - sin internet, etc.
                _log.failure(log, f"búsqueda fallida ({kind}: {q!r})", exc)
                self.q.put(("remote_error", sid, str(exc)))
            finally:
                if marker is not None and self._remote_inflight is marker:
                    self._remote_inflight = None

        threading.Thread(target=work, daemon=True, name="buscar-youtube").start()

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

    def redownload_selected(self) -> None:
        """Rehace archivos marcados como incompletos; la copia anterior se aparta, no se borra."""
        picks = [t for t in self._selected() if t["video_id"] in self.damaged]
        if not picks:
            return
        for track in picks:
            redownload = dict(track)
            redownload.pop("path", None)
            redownload["_genre"] = self._genre_of(track)
            library.requeue([track["video_id"]])  # antes de despertar al servicio
            self.dl.start_download(redownload)
            self._set_state(track["video_id"], "en cola")
        self.status.configure(text=f"Reintentando {len(picks)} archivo(s) incompleto(s)")






    def open_queue(self) -> None:
        """Abre la página de la cola de descargas."""
        self.show_page("downloads")

    def open_settings(self) -> None:
        """Ajustes de apariencia, reproducción, descargas y atajos."""
        self.show_page("settings")

    def open_actions(self) -> None:
        """Menú visible con las acciones que antes solo estaban en el clic derecho."""
        m = self._menu()
        m.add_command(label="Ajustes y atajos…", command=self.open_settings)
        m.add_separator()
        m.add_command(label="Buscar canciones repetidas…", command=self.find_duplicates)
        m.add_command(label="Exportar biblioteca a M3U…", command=self.export_m3u)
        m.add_command(label="Quitar archivos que faltan…", command=self.clean_missing)
        m.add_checkbutton(label="Normalizar volumen (ReplayGain)",
                          variable=self.var_normalize, command=self.toggle_normalize)
        m.add_separator()
        m.add_command(label="Diagnóstico del entorno…", command=self.open_health)
        x = self.btn_more.winfo_rootx()
        y = self.btn_more.winfo_rooty() + self.btn_more.winfo_height()
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def open_prompt(self) -> None:
        """Abre «Música por frase»: pides lo que quieres oír y cuántas canciones."""
        self.show_page("prompt")

    def open_import(self) -> None:
        """Abre «Importar de YouTube Music»: pega un enlace y encola lo que aún no tengas."""
        self.show_page("import")

    def open_health(self) -> None:
        """Abre el diagnóstico del entorno (Node, ffmpeg, cookies, carpetas…)."""
        self.show_page("health")

    def open_music_folder(self) -> None:
        """Abre la carpeta donde se guardan las descargas (útil si falta espacio)."""
        health.open_path(config.MUSIC_DIR)

    def _check_environment(self) -> None:
        """Avisa al arrancar de lo que impedirá descargar, sin bloquear la ventana."""
        try:
            problems = health.problems()
        except Exception:  # noqa: BLE001 - una comprobación rota no debe impedir usar la app
            return
        if problems:
            self.status.configure(text=problems[0])

    def set_cookies(self, browser: str | None) -> None:
        """Guarda el navegador de cookies y lo aplica a descargas y streaming.

        Se aplica aunque sea el mismo navegador: así volver a elegirlo relee las cookies si la
        sesión se renovó en el navegador.
        """
        browser = (browser or "").strip().lower()
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
        elif name == "settings":
            from .settings_page import SettingsPage
            page = SettingsPage(self)
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
        (self.grid if self.view_mode == "grid" else self.tree).focus_set()

    def _escape(self, _e=None) -> str | None:
        if self.active_page is not None:
            self.close_page()
            return "break"
        if self.queue_win is not None:  # el panel «A continuación» también se cierra con Esc
            self._show_queue()
            return "break"
        return None

    def _refresh_nav(self) -> None:
        for name, button in (("prompt", self.btn_prompt), ("import", self.btn_import),
                             ("downloads", self.btn_downloads),
                             ("settings", self.btn_settings)):
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
        self._refresh_downloads_icon()
        if paused:
            message = "Descargas en pausa: lo que ya empezó termina. Reanuda desde «Descargas»."
        elif self.disk_level == "critical":
            message = "Descargas en pausa por falta de espacio: libera espacio y se reanudarán solas."
        elif self.disk_level == "low":
            message = "Descargas reanudadas: con poco espacio, irán de una en una."
        else:
            message = "Descargas reanudadas"
        self.status.configure(text=message)
        page = self.pages.get("downloads")
        if page is not None and self.active_page is page:
            page.refresh()

    def _refresh_downloads_icon(self) -> None:
        """Marca el icono de Descargas si el usuario las pausó o si falta espacio."""
        self.btn_downloads.set_on(self.dl_paused or self.disk_level != "ok")

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
        if self.view_mode == "grid":
            self.grid.select(sel)
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
            self._on_select()
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
                covers.cache_path(t["video_id"]).unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar %s", t["path"], exc_info=True)
            self.dl.state.pop(t["video_id"], None)
        self.refresh_library()
        self.status.configure(text=f"{len(picks)} eliminadas")

    # ---------- menú contextual ----------
    def _context(self, e) -> None:
        self._context_at(self.tree.identify_row(e.y), e.x_root, e.y_root)

    def _context_at(self, iid: str | None, x_root: int, y_root: int) -> None:
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self._on_select()
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
        if any(t["video_id"] in self.damaged for t in picks):
            m.add_command(label="Volver a descargar (archivo incompleto)",
                          command=self.redownload_selected)
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
            m.tk_popup(x_root, y_root)
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
        self.queue_why = {}
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
        self.radio_seed = t

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
        seed = self.radio_seed or {}
        reason = f"similar a {seed.get('artist') or seed.get('title') or 'la semilla'}"
        new = []
        for x in tracks:
            if x["video_id"] in have:
                continue
            x["_genre"] = self.seed_genre  # lo que se guarde por el respaldo cae en el mismo género
            new.append(self.lib_by_id.get(x["video_id"], x))  # si ya la tienes, suena desde el disco
            self.queue_why[x["video_id"]] = reason
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
        tracks = autoqueue.backup_queue(
            self.library, genre, exclude, n,
            energy=self._track_energy, target_energy=self._track_energy(cur) if cur else None)
        if tracks:
            for track in tracks:
                self.queue_why[track["video_id"]] = autoqueue.reason_for(track, genre)
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
        self.save_prefs()
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
        self.queue_lb = (self.drawer_list if opening and self.drawer_mode == "queue"
                         else None)
        self.btn_queue.set_on(opening)
        start, to = self._drawer_x, 0 if opening else int(DRAWER_W * self.k)
        self.drawer.lift()
        if opening:
            self._set_drawer_mode(self.drawer_mode)
            if self.drawer_mode == "lyrics":
                self.lyrics_view.text.focus_set()
            else:
                self.drawer_list.focus_set()

        def step(p: float) -> None:
            self._drawer_x = int(start + (to - start) * p)
            self.drawer.place_configure(x=self._drawer_x)

        self.anim.tween("drawer", 260 if opening else 190, step)

    def _set_drawer_mode(self, mode: str) -> None:
        """Alterna el panel entre la cola y la letra, sin cerrarlo."""
        self.drawer_mode = "lyrics" if mode == "lyrics" else "queue"
        self.queue_lb = self.drawer_list if self.drawer_mode == "queue" else None
        if self.drawer_mode == "lyrics":
            self.drawer_list.pack_forget()
            self.drawer_hint.pack_forget()
            self.lyrics_view.pack(fill="both", expand=True, padx=int(4 * self.k),
                                  pady=(0, int(4 * self.k)))
            self.drawer_title.configure(text="Letra")
            self.drawer_sub.configure(
                text="Sincronizada con la canción" if self.lyrics_enabled
                else "Actívala en Ajustes › Reproducción")
            self._load_lyrics(self.cur_track)
            self._sync_lyrics(force=True)
        else:
            self.lyrics_view.pack_forget()
            self.drawer_hint.pack(fill="x", padx=int(16 * self.k), pady=(0, int(8 * self.k)))
            self.drawer_list.pack(fill="both", expand=True, padx=int(4 * self.k))
            self.drawer_title.configure(text="A continuación")
            self._refresh_queue()
        self._style_drawer_tabs(self.scheme.get("accent", self.pal["accent"]))

    # ---------- letras ----------
    def _load_lyrics(self, t: dict | None) -> None:
        """Pide la letra de la canción actual y la muestra; nunca bloquea el hilo de la ventana."""
        if not self.lyrics_enabled:
            self._lyrics_payload = self._lyrics_vid = None
            self.lyrics_view.set_message("Activa las letras en Ajustes › Reproducción.")
            return
        if not t:
            self._lyrics_payload = self._lyrics_vid = None
            self.lyrics_view.set_message("Sin canción.")
            return
        vid = t["video_id"]
        if vid == self._lyrics_vid:
            return
        self._lyrics_vid, self._lyrics_payload = vid, None
        cached = lyrics_store.cached(vid)
        if cached is not None:
            self._apply_lyrics(vid, cached)
            return
        self.lyrics_view.set_message("Buscando letra…")
        self.status.configure(text="Buscando la letra…")
        token = self.play_token

        def work() -> None:
            self.q.put(("lyrics", vid, token, lyrics_store.fetch(t)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_lyrics(self, vid: str, payload: dict | None) -> None:
        if vid != self._lyrics_vid:
            return
        self._lyrics_payload = payload
        if payload is None:
            self.lyrics_view.set_message("No se pudo buscar la letra.")
            self.drawer_sub.configure(text="Sin conexión con LRCLIB")
        elif payload.get("negative"):
            self.lyrics_view.set_message("Sin letra para esta canción.")
            self.drawer_sub.configure(text="LRCLIB no tiene esta letra")
        else:
            self.lyrics_view.show(payload)
            self.drawer_sub.configure(
                text="Sincronizada" if payload.get("synced") else "Letra sin tiempos")
        self._sync_lyrics(force=True)

    def _sync_lyrics(self, force: bool = False) -> None:
        if (self.drawer_mode != "lyrics" or self.queue_win is None
                or not self._lyrics_payload or not self.player.loaded):
            return
        try:
            position = self.player.position()
        except Exception:  # noqa: BLE001 - la letra no debe parar el tick de reproducción
            return
        self.lyrics_view.sync(position, force=force)

    def toggle_lyrics(self) -> None:
        self.lyrics_enabled = not self.lyrics_enabled
        self.var_lyrics.set(self.lyrics_enabled)
        self.save_prefs()
        if self.drawer_mode == "lyrics" and self.queue_win is not None:
            self._lyrics_vid = None
            self._load_lyrics(self.cur_track)
        self.status.configure(text="Letras " + ("activadas" if self.lyrics_enabled
                                                else "desactivadas"))

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
            why = self.queue_why.get(t["video_id"]) if kind == "ctx" and self.pq.auto_ctx else None
            if why:
                item["sub"] = why
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
        upcoming = list(range(self.pq.opos + 1, len(self.pq.order)))
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
        queued = list(self.pq.up_next) + [self.pq.ctx[self.pq.order[j]] for j in upcoming]
        total = sum((t.get("duration") or 0) for t in queued)
        if queued:
            if total >= 3600:
                length = f"{int(total) // 3600} h {int(total) % 3600 // 60} min"
            else:
                length = fmt(total)
            summary = f"{len(queued)} canciones · {length}"
        else:
            summary = "nada en cola"
        self.drawer_sub.configure(
            text=f"{kind} · {'en línea' if self.online else 'sin conexión'} · {summary}")

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
        self._preview_vid = None  # la siguiente canción podrá anticipar su color
        old, self.playing_vid = self.playing_vid, vid
        self._stream_retries.pop(vid, None)
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
        if self.view_mode == "grid" and iid is not None:
            self.grid.see(int(iid))
        self.now.set(t["title"], t.get("artist") or "")
        if self.queue_win is not None and self.drawer_mode == "lyrics":
            self._load_lyrics(t)
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
        self.save_prefs()
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

    def _on_volume(self, value: float) -> None:
        """Volumen desde el carril: mantiene el icono y el último valor audible al día."""
        self.player.set_volume(value)
        if value > 0:
            self._last_volume = value
        if hasattr(self, "vol_icon"):
            self.vol_icon.set_on(value <= 0)

    def set_volume(self, value: float) -> None:
        """Cambia el volumen desde el teclado o la página de ajustes."""
        value = max(0.0, min(100.0, float(value)))
        self.vol.set(value)
        if value > 0:
            self._last_volume = value
        self.save_prefs()
        self.status.configure(text=f"Volumen: {int(value)}%")

    def _volume_step(self, delta: int) -> str | None:
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None  # dentro de un campo, las flechas mueven el cursor
        self.set_volume(self.vol.get() + delta)
        return "break"

    def _toggle_mute(self, _e=None) -> str | None:
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None
        if self.vol.get() > 0:
            self._last_volume = self.vol.get()
            self.vol.set(0)
            self.save_prefs()
            self.status.configure(text="Silenciado (Ctrl+M para recuperar el volumen)")
        else:
            self.set_volume(self._last_volume or 80)
        return "break"

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
            if cur and (self.online or not self._is_remote(cur)):
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
                npos = self.pq.skip_remote(npos, 1 if d > 0 else -1)
            else:
                if auto:  # fin de la lista
                    self.player.stop()
                    self.btn_play.configure(text="▶")
                return
        if not self.pq.order or not 0 <= npos < len(self.pq.order):
            if auto:
                self.player.stop()
                self.btn_play.configure(text="▶")
            return
        self.pq.opos = npos
        self._play_track(self.pq.ctx[self.pq.order[npos]], how)

    # ---------- transiciones ----------

    def cycle_xfade(self) -> None:
        i = XFADE_CHOICES.index(self.xfade_ms) if self.xfade_ms in XFADE_CHOICES else 0
        self.xfade_ms = XFADE_CHOICES[(i + 1) % len(XFADE_CHOICES)]
        self._refresh_toggles()
        self.save_prefs()
        self.status.configure(text="Transición: " + (
            f"crossfade de {self.xfade_ms // 1000} s (sin hueco entre pistas del mismo álbum)"
            if self.xfade_ms else "sin crossfade, paso sin hueco (gapless)"))

    def toggle_shuffle(self) -> None:
        self.pq.shuffle = not self.pq.shuffle
        if self.pq.ctx and self.pq.order:
            self.pq.make_order(self.pq.order[self.pq.opos])
        self._refresh_toggles()
        self.save_prefs()

    def cycle_repeat(self) -> None:
        self.pq.repeat = (self.pq.repeat + 1) % 3
        self._refresh_toggles()
        self.save_prefs()

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
        """Reprograma siempre: un fallo en un fotograma no debe congelar la reproducción."""
        try:
            self._tick_once()
        except Exception as exc:  # noqa: BLE001 - el bucle de Tk no debe quedarse sin tick
            _log.failure(log, "el tick de reproducción falló", exc)
        finally:
            self.after(400, self._tick)

    def _tick_once(self) -> None:
        now = time.monotonic()
        delta, self._tick_at = now - self._tick_at, now
        if self.player.loaded:
            try:
                self.player.check()
                self.player.position()  # también lee la duración, que la barra de progreso necesita
                if self.player.finished():
                    since = self.transitioning_since
                    if since is None or time.monotonic() - since > TRANSITION_TIMEOUT_S:
                        actual = self.player.length or self.player.position()
                        if self._track_truncated(actual):
                            self._stop_damaged(actual)
                        else:
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
                    self._sync_lyrics()
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
                elif t and t["video_id"] == self.playing_vid:  # falló el streaming: reintenta y, si persiste, vía lenta
                    vid = t["video_id"]
                    if self._stream_retries.get(vid, 0) < 2 and self._check_online():
                        self._stream_retries[vid] = self._stream_retries.get(vid, 0) + 1
                        self.transitioning_since = time.monotonic()
                        self.status.configure(text="Reintentando streaming…")
                        self.play_pool.submit(self._resolve_and_play, t, self.play_token)
                    else:
                        self._stream_retries.pop(vid, None)
                        self._download_and_play(t, self.play_token)
                else:
                    self.btn_play.configure(text="▶")
                    self.status.configure(text=f"No se pudo reproducir: {exc}")

    # ---------- portada ----------
    def _preview_ambient(self, t: dict) -> None:
        """Pide la portada de la siguiente canción para teñir la ventana antes de la mezcla."""
        vid = t.get("video_id")
        if not vid or vid == self._preview_vid:
            return
        self._preview_vid = vid
        local = t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")

        def work() -> None:
            self.q.put(("preview_cover", vid, covers.load(t, local)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_preview_cover(self, vid: str, data: bytes | None) -> None:
        """Aplica el color de la próxima solo si sigue siendo la próxima y hay música sonando."""
        if (vid != self._preview_vid or not self.player.loaded or self.player.paused
                or (self.cur_track or {}).get("video_id") == vid
                or (self._peek_next() or {}).get("video_id") != vid):
            return
        color = None
        if data:
            from PIL import Image
            try:
                color = dominant_color(Image.open(io.BytesIO(data)).convert("RGB"))
            except Exception as exc:  # noqa: BLE001 - portada corrupta: degradado de la canción
                _log.failure(log, "portada ilegible para el color de la próxima", exc)
        self._set_ambient(color or seed_color(vid))

    def _load_cover(self, t: dict, path: str | None, token: int) -> None:
        def work() -> None:
            data = covers.load(t, path)
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

    # ---------- preferencias ----------

    def _prefs_state(self) -> dict:
        volume = self.vol.get()
        if volume <= 0 and self._last_volume > 0:
            volume = self._last_volume  # no guardar un 0 accidental al cerrar silenciado
        return {"dark": self.dark, "vol": volume, "shuffle": self.pq.shuffle,
                "repeat": self.pq.repeat, "auto": self.autoplay, "xfade": self.xfade_ms,
                "normalize": self.normalize, "cookies": self.cookies,
                "reduce_motion": self.anim.reduced, "auto_update": self.auto_update,
                "view": self.view_mode, "lyrics": self.lyrics_enabled,
                "ytdlp_checked_at": self._ytdlp_checked_at}

    def _close(self) -> None:
        self._save_session()
        prefs.save(self._prefs_state())
        for page in self.pages.values():
            page.deactivate()
        self.player.close()
        self.dl.close()
        self.play_pool.shutdown(wait=False, cancel_futures=True)
        self.thumb_pool.shutdown(wait=False, cancel_futures=True)
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
