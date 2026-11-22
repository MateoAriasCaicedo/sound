"""Ventana principal: biblioteca local + YT Music (por género) + reproductor."""
import datetime
import io
import queue
import re
import sys
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
from ..core import playlists as playlists_store
from ..core import radio as radio_store
from ..core.filters import fold
from . import home, prefs, session, window_state
from .anim import Animator, ease_in_out
from .app_updates import CHECK_DELAY_MS, AppUpdateMixin
from .chrome import SYSTEM_POLL_MS, ChromeMixin
from .cover_grid import CoverGrid, album_key, artist_key, group_albums
from .discover import DiscoverMixin
from .downloads import DownloadsController
from .drag import DragMixin
from .events import AHEAD_RESULTS, EventLoopMixin
from .hero import HeroMixin
from .library_ui import LibraryMixin
from .lyrics_view import LyricsView
from .navigation import NavigationMixin, Place, section_key
from .playback import FADE_TICK_MS, PlaybackMixin, fmt
from .player import Player
from .playlists_ui import PlaylistsMixin
from .playqueue import PlayQueue
from .radio import RadioMixin, station_poster
from .search import SearchMixin
from .sleep_timer import SleepTimerMixin
from .stations import StationsMixin
from .theme import (
    FONT_FAMILY,
    blend,
    dominant_color,
    enable_dpi_awareness,
    palette,
    readable_on,
    seed_color,
    style_titlebar,
    vivid,
)
from .toast import Toast
from .up_next import DRAWER_W, UpNextMixin
from .welcome import TITLE as WELCOME_TITLE
from .welcome import WelcomeMixin
from .widgets import (
    ChipBar,
    Cover,
    Equalizer,
    Header,
    IconButton,
    NowPlaying,
    PlayButton,
    PointerTip,
    RowList,
    SearchBox,
    Slider,
    StatusLine,
    TextButton,
    Tooltip,
    WaveSeek,
    bind_touchpad,
    bind_wheel,
    touchpad_deltas,
    ui_fonts,
)

log = _log.get("ui")
FONT = (FONT_FAMILY, 10)
SIDE_W = 200  # ancho de la barra de géneros, en píxeles a 96 ppp
XFADE_CHOICES = (0, 2000, 4000, 6000, 8000, 12000)  # ms; 0 = gapless
MANUAL_FADE_MS = 280       # fundido al saltar de canción a mano
INTRO_FADE_MS = 150        # entrada al empezar desde silencio
TRANSITION_TIMEOUT_S = 25
NET_POLL_S = 6  # cada cuánto se comprueba si hay internet
STALL_S = 8      # segundos sin avanzar (sin internet) antes de saltar a la cola de respaldo
BACKUP_AFTER_MS = 10_000  # una canción en streaming se guarda tras escucharla este tiempo
AUTO_UPDATE_DELAY_MS = 20_000       # dejar arrancar la ventana antes de buscar actualizaciones
AUTO_UPDATE_RETRY_MS = 10 * 60_000  # si hay descargas en curso, reintentar más tarde
COLS = (("title", "Título", 280), ("artist", "Artista", 170), ("album", "Álbum", 170),
        ("dur", "Dur.", 50), ("state", "", 84))


def load_library() -> list[dict]:
    """La biblioteca descargada, sin las canciones cuyo archivo ya no existe en disco."""
    return [t for t in library.all_tracks() if Path(t["path"]).exists()]


DOWNLOAD_LABEL = "↓ Descargar"  # columna de estado de un resultado de YouTube: se puede pulsar
# al pasar el ratón por una canción: ▶ en vez del número y, a la derecha, Me gusta y «⋯»
HOVER_PLAY = "▶"
HOVER_LIKE, HOVER_LIKED, HOVER_MORE = "♡", "♥", "⋯"
HOVER_GAP = "      "
HOVER_LEAD = "    "  # aire tras la duración: el corazón no debe parecer parte de ella
LYRICS_NOTICE = ("Las letras están desactivadas. Al activarlas, el título, el artista, el álbum y "
                 "la duración de cada canción se envían a LRCLIB, un servicio externo.")
DUPES_CHECK_MS = 8000  # tras arrancar, se buscan canciones repetidas para ofrecer revisarlas


class App(tk.Tk, NavigationMixin, HeroMixin, DragMixin, AppUpdateMixin, ChromeMixin,
          SleepTimerMixin, WelcomeMixin, DiscoverMixin, RadioMixin, StationsMixin,
          PlaylistsMixin, SearchMixin, UpNextMixin,
          LibraryMixin, PlaybackMixin, EventLoopMixin):
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
        # (biblioteca, YouTube, secciones propias) sin ordenar
        self.raw: tuple[list[dict], list[dict], list | None] = ([], [], None)
        self.rows: list[dict] = []  # lo que muestra la tabla ahora
        self.sections: list[tuple[int, str]] = []  # (primera fila, título) de cada sección
        self._last_row: int | None = None  # última fila elegida: para saltar títulos de sección
        self.vid_iid: dict[str, str] = {}
        self.genre_filter: set[str] = set()
        self.side_items: list[tuple[str, str | None, str]] = []  # (tipo, nombre, texto)
        self._genres_open = False  # géneros de una sola canción desplegados en el lateral
        self.sel_keys: set[tuple[str, str | None]] = {("home", None)}
        self.view_kind = "home"  # home | all | lib | liked | recent | top | playlist
        self.playlists: list[dict] = []    # listas propias (se releen al dibujar el lateral)
        self.playlist_id: int | None = None  # la lista abierta, con view_kind == "playlist"
        self._home_per = 0       # canciones por estantería en «Inicio» (una fila de portadas)
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
        # tamaño, posición y si estaba maximizada: como se dejó la última vez
        self._normal_geometry: str | None = None
        self._zoomed = False
        self._restore_window()
        self.bind("<Configure>", self._window_moved, add="+")
        self._dupes_seen = int(self.prefs.get("dupes_seen", 0) or 0)  # repetidas ya avisadas
        self._mini_pos = self.prefs.get("mini_pos")  # dónde se dejó el mini reproductor
        self.mini = None          # el mini reproductor, creado la primera vez que se abre
        self.mini_on = False
        self.taskbar = None       # botones bajo la miniatura de la barra de tareas
        self.system_media = None  # teclas multimedia y panel multimedia de Windows
        self._timeline_tick = 0
        self.mini_taskbar = None
        # pantalla «Reproduciendo» (la canción en grande), creada la primera vez que se abre
        self.now_view = None
        self.fullscreen = False
        self._now_tab = str(self.prefs.get("now_tab") or "lyrics")
        self._play_from = ""  # de dónde salió la cola: «Álbum «Rumours»», «Me gusta»…
        # view_pref: la vista elegida (se guarda); view_mode: la que se ve ahora. Solo difieren
        # con un álbum abierto, que siempre se muestra como lista de canciones.
        self.view_pref = "grid" if str(self.prefs.get("view", "list")).lower() == "grid" \
            else "list"
        self.view_mode = self.view_pref
        self.views: list[dict] = []  # páginas de álbum/artista abiertas; la última es la visible
        self._track_no_asked: set[tuple] = set()  # álbumes cuyo orden de pistas ya se pidió
        self.decade: int | None = None  # filtro por década (1980, 1990…) de la vista actual
        # canciones borradas que aún se pueden recuperar con «Deshacer»: ocultas, no borradas
        self._hidden: set[str] = set()
        self.cookies = str(self.prefs.get("cookies") or config.COOKIES_FROM_BROWSER or "").strip().lower()
        # Descargas y streaming comparten el navegador: se aplica antes de resolver nada.
        cookies_store.configure(self.cookies)
        self._cookies_dialog_shown = False
        self._crash_shown = False
        self._init_chrome()  # tema (Como el sistema, claro u oscuro) y barra de arriba (chrome.py)
        self._init_sleep()   # temporizador para dormir (sleep_timer.py)
        self._init_discover()  # «Descubre en YouTube» en Inicio (discover.py)
        self._init_radio()     # emisoras en directo (radio.py y radio_page.py)
        self._init_stations()  # estaciones de Sound: radios sin fin (stations.py)
        # secciones del lateral plegadas: «lists», «genres», «yt»
        self._side_folded = {str(n) for n in self.prefs.get("side_folded") or []}
        # al importar una playlist de YouTube, guardarla también como lista propia
        self.import_as_playlist = bool(self.prefs.get("import_as_playlist", True))
        self._last_volume = float(self.prefs.get("vol", 80))
        self.auto_update = bool(self.prefs.get("auto_update", True))
        try:
            self._ytdlp_checked_at = float(self.prefs.get("ytdlp_checked_at", 0) or 0)
        except (TypeError, ValueError):
            self._ytdlp_checked_at = 0.0
        self._auto_update_running = False
        self._init_app_updates()  # versiones nuevas de Sound (app_updates.py)
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
        self.lyrics_enabled = bool(self.prefs.get("lyrics", True))
        self.var_lyrics = tk.BooleanVar(value=self.lyrics_enabled)
        self._lyrics_vid: str | None = None
        self._lyrics_payload: dict | None = None
        self._lyrics_shown: tuple = (None, "", None)  # lo último mostrado: (letra, aviso, estado)
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
        # dónde iba la canción en un momento conocido (inicio o último salto): con esto se nota
        # si Windows la da por terminada antes de lo posible
        self._clock_ref: tuple[int, float] = (0, time.monotonic())
        self._fix_retried: set[str] = set()  # canciones ya reintentadas tras convertirlas
        # historial: segundos escuchados de la canción actual, para contarla una sola vez
        self._listen_s = 0.0
        self._listen_vid: str | None = None
        self._listen_counted = True
        self._tick_at = time.monotonic()

        self._init_navigation()  # Atrás y Adelante (navigation.py)
        self._init_hero()        # cabecera de portada de álbumes, artistas y listas (hero.py)
        self._init_drag()        # arrastrar canciones a una lista o dentro de ella (drag.py)
        self._loose_rows = 0     # filas del principio que la cuadrícula no junta por álbum
        self.waves = waveform.Analyzer(lambda vid, levels: self.q.put(("wave", vid, levels)))
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._build()
        self.apply_theme()
        self.refresh_library()
        self._restore_session()
        self._visited()  # el primer sitio del historial: Inicio
        self._start_discover()  # lo guardado de Descubre ya; lo nuevo, en unos segundos
        self.waves.sweep((t["video_id"], t["path"]) for t in self.library)
        threading.Thread(target=self._fix_fragmented, daemon=True, name="mp4fix").start()
        self._load_yt_genres()
        threading.Thread(target=self._warm_search, daemon=True, name="calentar-busqueda").start()
        threading.Thread(target=self.dl.start_service, daemon=True).start()
        self.bind("<Control-g>", lambda e: (self.open_prompt(), "break")[1])  # «Música por frase»
        self.bind("<Control-l>", lambda e: (self.toggle_like(), "break")[1])  # Me gusta
        self.bind("<Control-z>", self._undo_key)  # «Deshacer» del aviso visible
        self.bind("<Control-M>", lambda e: (self.toggle_mini(), "break")[1])  # Ctrl+Mayús+M
        self.bind("<Control-F>", lambda e: (self.toggle_now_view(), "break")[1])  # Ctrl+Mayús+F
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Control-a>", self._select_all)  # todas las canciones de la vista
        self.bind("<Control-Shift-L>", self._toggle_view_key)
        self.bind("<Control-comma>", lambda e: (self.open_settings(), "break")[1])  # ajustes
        self.bind("<F1>", lambda e: (self.open_settings(), "break")[1])  # ayuda y atajos
        self.bind("<Control-m>", self._toggle_mute)  # silenciar
        self.bind("<Control-Up>", lambda e: self._volume_step(5))
        self.bind("<Control-Down>", lambda e: self._volume_step(-5))
        bind_wheel(self, self._wheel_anywhere, add=True)
        bind_touchpad(self, self._touchpad_anywhere, add=True)
        self.bind("<Escape>", self._escape)
        # Atrás y Adelante: Alt+← / Alt+→ y los botones laterales del ratón. Tk solo numera
        # hasta el 5 (en X11, 4 y 5 son la rueda), así que los laterales solo se enlazan en
        # Windows.
        self.bind("<Alt-Left>", self.go_back)
        self.bind("<Alt-Right>", self.go_forward)
        if sys.platform == "win32":
            self.bind("<Button-4>", lambda e: self._mouse_side(False))
            self.bind("<Button-5>", lambda e: self._mouse_side(True))
        self.play_pool.submit(self._warm_up)
        threading.Thread(target=self._net_loop, daemon=True).start()
        self.after(FADE_TICK_MS, self._fade_tick)
        self.after(100, self._pump)
        self.after(400, self._tick)
        self.after(1200, self._check_environment)
        self.after(DUPES_CHECK_MS, self._check_duplicates)
        self.after(1500, self._setup_taskbar)  # cuando el icono de la barra de tareas ya existe
        self.after(600, self._setup_system_media)
        self.after(AUTO_UPDATE_DELAY_MS, self._maybe_auto_update)
        self.after(CHECK_DELAY_MS, self._maybe_check_app_update)
        self.after(SYSTEM_POLL_MS, self._follow_system_theme)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.anim.tween("window", 260, lambda p: self.attributes("-alpha", p),
                        done=lambda: self.attributes("-alpha", 1.0))

    # ---------- construcción ----------
    def _set_app_icon(self) -> None:
        """Icono de la ventana y de los diálogos (sin él Tk pondría el de Python/Tk)."""
        icon = config.icon_path()
        if icon is None:
            return
        if sys.platform == "win32":
            try:
                self.iconbitmap(str(icon))
                self.iconbitmap(default=str(icon))  # también para los diálogos
                return
            except tk.TclError as exc:  # .ico no soportado o archivo ilegible
                _log.failure(log, "no se pudo usar el icono .ico", exc)
        # Linux y macOS no entienden .ico en Tk: se convierte con Pillow, que ya es dependencia.
        try:
            from PIL import Image, ImageTk

            png = icon.with_suffix(".png")
            image = Image.open(png if png.exists() else icon)
            self._icon_photo = ImageTk.PhotoImage(image.convert("RGBA"))
            self.iconphoto(True, self._icon_photo)  # esta ventana y los futuros diálogos
        except Exception as exc:  # noqa: BLE001 - el icono es cosmético
            _log.failure(log, "no se pudo usar el icono de la ventana", exc)

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
        # Atrás y Adelante, justo antes del buscador (encima del borde del lateral)
        self.nav_box = tk.Frame(top)
        self.nav_box.grid(row=0, column=0, sticky="e", padx=(0, int(10 * k)),
                          pady=(int(6 * k), 0))
        self.btn_nav_back = IconButton(self.nav_box, self.anim, k, "chevron_left", self.go_back,
                                       tip=lambda: self._nav_tip(False), size=32, glyph=18)
        self.btn_nav_fwd = IconButton(self.nav_box, self.anim, k, "chevron_right",
                                      self.go_forward, tip=lambda: self._nav_tip(True),
                                      size=32, glyph=18)
        self.btn_nav_back.pack(side="left")
        self.btn_nav_fwd.pack(side="left")
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
        # «＋ Añadir música» reúne buscar en YouTube, importar un enlace y música por frase
        self._build_add_button(top, 5)
        # «Descargas» (la cola de descargas), distinta de la cola de reproducción (btn_queue)
        self.btn_downloads = IconButton(top, self.anim, k, "downloads", self.open_queue,
                                        tip=lambda: self._downloads_tip())
        self.btn_downloads.grid(row=0, column=7, padx=(int(2 * k), 0), pady=(int(6 * k), 0))
        self.btn_settings = IconButton(top, self.anim, k, "settings", self.open_settings,
                                       tip=lambda: self._settings_tip())
        self.btn_settings.grid(row=0, column=10, padx=(int(2 * k), int(14 * k)),
                               pady=(int(6 * k), 0))

        # título de la vista y línea de estado, sobre un degradado teñido por la canción
        self.header = Header(self, self.anim, k, f, list_x)
        self.header.grid(row=1, column=0, sticky="ew")
        self.status = StatusLine(self.header)
        self._build_hero_bar()

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
        self.genres = RowList(side, self.anim, k, f, on_select=self._pick_genre,
                              on_context=self._side_context, reselect=True,
                              on_header=self._toggle_side_section)
        self.genres.configure(width=int((SIDE_W - 14) * k))
        self.genres.grid(row=0, column=0, sticky="nsew")
        self.sb_genres = ttk.Scrollbar(side, orient="vertical",
                                       command=self._sidebar_scrollbar)
        self.genres.configure(yscrollcommand=self._sidebar_scrolled)
        self.sb_genres.grid(row=0, column=1, sticky="ns")
        self.sb_genres.grid_remove()
        # la rueda también desplaza la lista si el puntero cae en el margen o en la barra
        for w in (side, self.sb_genres):
            bind_wheel(w, self.genres._wheel)
            bind_touchpad(w, self.genres._touchpad)

        mid = tk.Frame(content)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, int(10 * k)))
        mid.rowconfigure(1, weight=1)
        mid.columnconfigure(0, weight=1)
        self.mid = mid
        # barra de la vista: volver y décadas a la izquierda; orden, vista y acciones a la derecha
        self.list_tools = tk.Frame(mid)
        self.list_tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(4 * k)))
        self.list_tools.columnconfigure(1, weight=1)
        pad_y = (int(2 * k), int(2 * k))
        self.chips = ChipBar(self.list_tools, self.anim, k, f["small"], self._pick_decade)
        self.chips.grid(row=0, column=1, sticky="w", padx=(int(6 * k), 0))
        self.btn_sort = TextButton(self.list_tools, self.anim, "", f["small"], self._sort_menu)
        self.btn_sort.grid(row=0, column=2, padx=(0, int(4 * k)))
        self.btn_view = IconButton(
            self.list_tools, self.anim, k, "grid", self.toggle_view_mode, size=32, glyph=18,
            tip=lambda: "Vista de lista (Ctrl+Shift+L)"
            if self.view_mode == "grid" else "Vista de cuadrícula (Ctrl+Shift+L)")
        self.btn_view.grid(row=0, column=3, padx=(0, int(2 * k)), pady=pad_y)
        self.btn_more = IconButton(self.list_tools, self.anim, k, "tools", self.open_actions,
                                   tip="Más acciones de la biblioteca", size=32, glyph=18)
        self.btn_more.grid(row=0, column=4, padx=(0, int(6 * k)), pady=pad_y)
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
        self.tree.bind("<Alt-Up>", lambda e: self._move_key(-1))
        self.tree.bind("<Alt-Down>", lambda e: self._move_key(1))
        self.tree.bind("<Configure>", self._fit_columns, add="+")
        self.tree.bind("<Motion>", self._row_hover)
        self.tree.bind("<Leave>", self._tree_leave)
        self.tree.bind("<ButtonRelease-1>", self._tree_click)
        self._bind_drag()
        self.tree_tip = PointerTip(self.tree, self.anim, lambda: (
            blend(self.pal["bg"], self.pal["fg"], 0.14), self.pal["fg"]))
        self.grid = CoverGrid(mid, self.anim, k, f, on_select=self._grid_selected,
                              on_activate=self._grid_activate, on_context=self._grid_context,
                              on_need_cover=self._request_thumb, on_like=self._grid_like,
                              on_artist=self._grid_artist, on_download=self._grid_download,
                              on_columns=self._grid_columns)
        self.grid.grid(row=1, column=0, sticky="nsew")
        self.grid.grid_remove()
        self.grid.bind("<Delete>", lambda e: self.delete_selected())
        # Alt+←/→ son Atrás y Adelante; para mover canciones de una lista, Alt+↑/↓
        for seq, delta in (("<Alt-Up>", -1), ("<Alt-Down>", 1)):
            self.grid.bind(seq, lambda e, d=delta: self._move_key(d))
        self.empty = tk.Label(mid, font=f["body"])
        self._build_welcome(mid)  # con la biblioteca vacía, en vez de la tabla (welcome.py)

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
        # la portada abre la pantalla «Reproduciendo»
        self.cover.configure(cursor="hand2")
        self.cover.bind("<ButtonRelease-1>", lambda e: self.toggle_now_view())
        Tooltip(self.cover, self.anim, lambda: "Cerrar la vista en grande (Ctrl+Shift+F)"
                if self.now_open else "Ver en grande (Ctrl+Shift+F)",
                lambda: (blend(self.scheme.get("band", self.pal["panel"]), self.pal["fg"], 0.14),
                         self.pal["fg"]))
        self.now = NowPlaying(left, self.anim, k, f, on_like=self.like_current,
                              on_artist=lambda: self.cur_track and self.open_artist(
                                  self.cur_track.get("artist") or ""))
        self.now.pack(side="left", fill="x", expand=True, padx=(int(12 * k), int(12 * k)))

        mid_bar = tk.Frame(bar)
        mid_bar.grid(row=0, column=1)
        self.btn_shuffle = IconButton(mid_bar, self.anim, k, "shuffle", self.toggle_shuffle,
                                      tip=lambda: "Orden aleatorio: " + (
                                          "activado" if self.pq.shuffle else "desactivado"))
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
        self.btn_mini = IconButton(right, self.anim, k, "mini", self.toggle_mini,
                                   tip="Mini reproductor (Ctrl+Shift+M)")
        self.btn_mini.pack(side="right")
        self.btn_stage = IconButton(right, self.anim, k, "stage", self.toggle_now_view,
                                    tip=lambda: "Cerrar la vista en grande (Esc)" if self.now_open
                                    else "Reproduciendo, en grande (Ctrl+Shift+F)")
        self.btn_stage.pack(side="right")
        self.btn_queue = IconButton(right, self.anim, k, "queue", self._show_queue,
                                    tip="A continuación")
        self.btn_queue.pack(side="right")
        self._build_sleep_button(right).pack(side="right")
        self.btn_xfade = IconButton(
            right, self.anim, k, "xfade", self.cycle_xfade,
            tip=lambda: f"Crossfade de {self.xfade_ms // 1000} s" if self.xfade_ms
            else "Sin crossfade")
        self.btn_xfade.pack(side="right")
        self.btn_auto = IconButton(right, self.anim, k, "infinity", self.toggle_auto,
                                   tip=lambda: "Cola automática con canciones similares: " + (
                                       "activada" if self.autoplay else "desactivada"))
        self.btn_auto.pack(side="right")
        self.time = tk.Label(right, text="0:00 / 0:00", font=f["digits"])
        self.time.pack(side="right", padx=(0, int(10 * k)))

        # avisos con «Deshacer»: flotan sobre la lista (o la vista en grande), encima de la onda
        self.toast = Toast(self, self.anim, k, f, area=content, below=self.scale)

        self.band_frames = (band, bar, left, mid_bar, right)
        self._bar_parts = (bar, mid_bar, right)
        self._bar_full: int | None = None  # ancho que pide la derecha del reproductor completa
        self._bar_level = 0  # 0 completa, 1 volumen corto sin icono, 2 además sin crossfade
        # la barra misma (no la franja): al saltar de maximizada al mínimo, la franja avisa antes
        # de que la barra tenga su ancho nuevo y el ajuste se quedaba con el viejo
        bar.bind("<Configure>", lambda e: self.after_idle(self._fit_bar))
        self.top_icons = (self.btn_downloads, self.btn_settings, self.btn_nav_back,
                          self.btn_nav_fwd)
        self.bar_icons = (self.btn_shuffle, self.btn_prev, self.btn_next, self.btn_repeat,
                          self.vol_icon, self.btn_queue, self.btn_xfade, self.btn_auto,
                          self.btn_mini, self.btn_stage, self.btn_sleep)
        self.vol.set(self.prefs.get("vol", 80))

        self.bind("<space>", self._space)
        self._refresh_toggles()

    def _fit_bar(self) -> None:
        """Con la ventana estrecha, el volumen se acorta y pierde su icono; más estrecha aún, se
        oculta el crossfade (está también en Ajustes › Transición). Así el tiempo («1:23 / 4:56»)
        sigue entero a la derecha del reproductor."""
        bar, mid_bar, right = self._bar_parts
        room = (bar.winfo_width() - mid_bar.winfo_width()) // 2
        if room <= 1:
            return
        if self._bar_full is None:
            if self._bar_level:
                return
            self._bar_full = right.winfo_reqwidth()
        k = self.k
        savings = (int(28 * k) + self.vol_icon.winfo_reqwidth() + int(6 * k),  # 92 → 64 y su icono
                   self.btn_xfade.winfo_reqwidth())
        need, level = self._bar_full, 0
        for save in savings:
            if room >= need:
                break
            need -= save
            level += 1
        if level == self._bar_level:
            return
        self._bar_level = level
        self.vol.set_length(64 if level else 92)
        if level >= 1:
            self.vol_icon.pack_forget()
        else:
            self.vol_icon.pack(side="right", padx=(int(6 * k), 0), after=self.vol)
        if level >= 2:
            self.btn_xfade.pack_forget()
        else:
            self.btn_xfade.pack(side="right", after=self.btn_sleep)

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
        self.view_pref = "list" if self.view_pref == "grid" else "grid"
        if self._display_mode() != self.view_mode:  # un álbum abierto sigue en lista
            self._set_view(self._display_mode())
        self.save_prefs()

    def _display_mode(self) -> str:
        """Lo que se ve ahora: la vista elegida, salvo en un álbum, que siempre es una lista."""
        top = self.sub_view
        return "list" if top is not None and top["kind"] == "album" else self.view_pref

    def _group_albums(self) -> bool:
        """En la cuadrícula, las canciones del mismo álbum se juntan en una tarjeta.

        Al explorar la biblioteca o la página de un artista; buscando, en el historial o con un
        álbum abierto se quiere ver cada canción.
        """
        top = self.sub_view
        if top is not None:
            return top["kind"] == "artist"
        return self.view_kind in ("all", "lib", "liked") and not self.query.get().strip()

    def _sync_grid(self) -> None:
        """Pasa a la cuadrícula las filas de la tabla (agrupadas por álbum si toca)."""
        self.grid.set_context(liked=self.liked, damaged=self.damaged, states=self.dl.state,
                              view_kind=self.view_kind)
        if self._group_albums():
            # lo de arriba (lo que más escuchas de un artista) va canción a canción
            loose = self._loose_rows if self.sections else 0
            cards, members = group_albums(self.rows[loose:])
            cards = list(self.rows[:loose]) + cards
            members = [(i,) for i in range(loose)] + [
                tuple(i + loose for i in group) for group in members]
        else:
            cards, members = self.rows, [(i,) for i in range(len(self.rows))]
        # las secciones van por filas de la tabla; la cuadrícula las quiere por tarjetas
        card_of = {row: i for i, rows in enumerate(members) for row in rows}
        sections = [(card_of[start], title) for start, title in self.sections if start in card_of]
        self.grid.set_items(cards, members, sections)
        self.grid.set_playing(self.playing_vid)
        self.grid.select(self.tree.selection())

    def _set_view(self, mode: str) -> None:
        self.view_mode = "grid" if mode == "grid" else "list"
        if self.view_mode == "grid":
            self.tree.grid_remove()
            self.tree.configure(yscrollcommand=lambda *_a: None)
            self.grid.grid(row=1, column=0, sticky="nsew")
            self.sb.configure(command=self._grid_scrollbar)
            self.grid.configure(yscrollcommand=self._grid_scrolled)
            self._sync_grid()
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
        self._hero_scrolled(float(first))
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
        card = self.grid.items[i]
        album = card if self.grid.is_album(card) else None
        self._context_at(str(self.grid.members[i][0]), x_root, y_root, album=album)

    def _grid_activate(self, i: int) -> None:
        """Doble clic o Intro en una tarjeta: una canción suena; un álbum se abre."""
        card = self.grid.items[i]
        if self.grid.is_album(card):
            self.open_album(card)
        else:
            self.play_index(self.grid.members[i][0])

    def _grid_like(self, i: int) -> None:
        self.toggle_like([self.rows[row] for row in self.grid.members[i] if row < len(self.rows)])

    def _grid_artist(self, i: int) -> None:
        """Clic en el artista bajo una portada: abre su página."""
        self.open_artist(self.grid.items[i].get("artist") or "")

    # ---------- páginas de álbum y de artista ----------
    @property
    def sub_view(self) -> dict | None:
        """La página de álbum o artista visible (la última abierta), o None."""
        return self.views[-1] if self.views else None

    def open_album(self, card: dict) -> None:
        """Las canciones de un álbum, en orden de pista, como lista."""
        album = self.grid.is_album(card)
        first = card["_tracks"][0] if album else card
        key = album_key(first)
        if key is None:
            return
        # al volver queda seleccionada la tarjeta del álbum
        back_to = [t["video_id"] for t in card["_tracks"]] if album else None
        self._open_view({"kind": "album", "key": key, "title": first.get("album") or "Álbum",
                         "artist": first.get("artist") or ""}, back_to)

    def open_artist(self, name: str) -> None:
        """Todas las canciones de un artista en tu biblioteca, con sus álbumes agrupados."""
        main = (name or "").split(",")[0].strip()
        key = artist_key({"artist": main})
        if not key or not any(artist_key(t) == key for t in self.library):
            self.toast.show(f"No tienes canciones de {main or 'este artista'} descargadas")
            return
        self._open_view({"kind": "artist", "key": key, "title": main, "artist": main})

    def _has_artist(self, name: str) -> bool:
        key = artist_key({"artist": name or ""})
        return bool(key) and any(artist_key(t) == key for t in self.library)

    def _open_view(self, view: dict, back_to: list[str] | None = None) -> None:
        top = self.sub_view
        self.close_now_view()  # desde la vista en grande: se va a la página pedida
        if top is not None and (top["kind"], top["key"]) == (view["kind"], view["key"]):
            return
        with self._navigating():
            self._hide_page()
            # al volver se selecciona lo que estaba seleccionado (la tarjeta del álbum, p. ej.)
            view["return"] = back_to or [t["video_id"] for t in self._selected()]
            self.views.append(view)
            self.tree.selection_remove(*self.tree.selection())
            self._sync_toolbar()
            self._set_view(self._display_mode())
            if self.query.get():
                self.query.set("")  # una página no se filtra por la búsqueda (y _on_query refresca)
            else:
                self.refresh_view()
            self._fade_rows()
            (self.grid if self.view_mode == "grid" else self.tree).focus_set()
            if self.view_mode == "list" and self.rows:
                self.tree.focus("0")
            if view["kind"] == "album":
                self._want_track_numbers(view)

    def close_view(self) -> None:
        """Sube un nivel (del álbum al artista, del artista a la biblioteca)."""
        self._go_up(len(self.views) - 1)

    def _go_up(self, level: int) -> None:
        """Deja abiertas solo las `level` primeras páginas: un tramo de la ruta de la cabecera."""
        if not 0 <= level < len(self.views):
            return
        with self._navigating():
            self.close_now_view()
            self._hide_page()
            view = self.views[level]  # al cerrarla vuelve lo que estaba elegido antes de abrirla
            del self.views[level:]
            self._sync_toolbar()
            self._set_view(self._display_mode())
            self.refresh_view()
            self._fade_rows()
            keep = [self.vid_iid[vid] for vid in view.get("return", []) if vid in self.vid_iid]
            if keep:
                self.tree.selection_set(keep)
                self._on_select()
                if self.view_mode == "grid":
                    self.grid.see_row(int(keep[0]))
                    self.grid.focus_set()
                else:
                    self.tree.see(keep[0])

    def _trail(self) -> list[tuple[str, object]]:
        """La ruta sobre el título de una página de álbum o artista; cada tramo lleva a su nivel."""
        if not self.views or self.query.get().strip():
            return []
        parts = [(self._section_label(section_key(self.sel_keys)), lambda: self._go_up(0))]
        for level, view in enumerate(self.views[:-1], start=1):
            parts.append((view["title"], lambda level=level: self._go_up(level)))
        return parts

    def close_views(self) -> None:
        """Cierra todas las páginas abiertas; quien llama refresca la vista."""
        if not self.views:
            return
        self.views.clear()
        self._sync_toolbar()
        self._set_view(self._display_mode())

    def _sub_view_rows(self, view: dict) -> list[dict]:
        key = view["key"]
        if view["kind"] == "album":
            rows = [t for t in self.library if album_key(t) == key]
            return sorted(rows, key=lambda t: t.get("track_no") or 10_000)  # estable: sin número, al final
        rows = [t for t in self.library if artist_key(t) == key]
        # lo más reciente primero, y cada álbum en su orden
        return sorted(rows, key=lambda t: (-(t.get("year") or 0), fold(t.get("album") or ""),
                                           t.get("track_no") or 10_000, fold(t["title"])))

    def _want_track_numbers(self, view: dict) -> None:
        """Pide a YouTube Music el orden de las pistas de un álbum que aún no lo tiene."""
        tracks = self._sub_view_rows(view)
        key = view["key"]
        if not tracks or all(t.get("track_no") for t in tracks) or key in self._track_no_asked:
            return
        self._track_no_asked.add(key)
        album, artist = view["title"], view["artist"]

        def work() -> None:
            from ..core import ytsearch
            try:
                numbers = ytsearch.match_track_numbers(
                    tracks, ytsearch.album_track_numbers(album, artist))
                library.set_track_numbers(numbers)
            except Exception as exc:  # noqa: BLE001 - sin red se queda el orden de descarga
                _log.failure(log, f"no se pudo pedir el orden de pistas de {album}", exc)
                return
            if numbers:
                self.q.put(("track_numbers", key, numbers))

        threading.Thread(target=work, daemon=True, name="pistas").start()

    def _on_track_numbers(self, key: tuple, numbers: dict[str, int]) -> None:
        for vid, number in numbers.items():
            if vid in self.lib_by_id:
                self.lib_by_id[vid]["track_no"] = number
        if self.now_open and self._now_album_key() == key:
            self._now_album()
        top = self.sub_view
        if top is not None and top["kind"] == "album" and top["key"] == key:
            keep = [t["video_id"] for t in self._selected()]
            self.refresh_view()
            iids = [self.vid_iid[vid] for vid in keep if vid in self.vid_iid]
            if iids:
                self.tree.selection_set(iids)

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
        self.welcome.restyle(p)
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
        self.tree.tag_configure("section", foreground=p["faint"], font=self.fonts["small"])
        self.chips.restyle(p["bg"], p["fg"], p["dim"])
        self.btn_sort.restyle(p["bg"], p["fg"], p["dim"])
        self.grid.restyle(bg=p["bg"], fg=p["fg"], dim=p["dim"], faint=p["faint"],
                          sel=p["sel"], hover=p["hover"], accent=self.scheme.get(
                              "accent", p["accent"]))
        scheme = self._scheme(self._ambient)
        self._paint(scheme)
        self._ambient_settled(scheme)
        for page in self.pages.values():
            page.restyle()
        self._set_view(self.view_mode)
        if self.now_view is not None:
            self.now_view.restyle()

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
        self.nav_box.configure(bg=top)
        for b in self.top_icons:
            b.restyle(top, p["fg"], p["dim"], accent)
        self.btn_add.restyle(bg=top, fg=p["fg"], dim=p["dim"], faint=p["faint"], accent=accent,
                             on_accent=p["accent_fg"], danger=p["danger"])
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
        p = self.pal
        # aviso en colores invertidos: destaca sobre la lista en los dos temas
        self.toast.restyle(p["bg"], blend(p["fg"], p["bg"], 0.08), p["bg"], c["accent"])
        self.tree.tag_configure("playing", foreground=c["accent"])
        self.grid.set_accent(c["accent"])
        if not self.now_open:  # con la vista en grande, la barra de título va con su fondo
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
        items = [("home", None, "Inicio"), ("all", None, "Todo"), ("liked", None, "Me gusta"),
                 ("recent", None, "Recientes"), ("top", None, "Más escuchado"),
                 ("radio", None, "Radio")]
        rights = ["", str(len(self.library)), str(len(self.liked)), "", "", ""]
        # tus listas, con su botón para crear otra
        try:
            self.playlists = playlists_store.all_playlists()
        except Exception as exc:  # noqa: BLE001 - sin listas, el resto del lateral sigue
            _log.failure(log, "no se pudieron leer las listas", exc)
            self.playlists = []
        # «Tus listas», «Géneros» y «YouTube» se pliegan con un clic en su título (se recuerda);
        # plegada, una sección solo deja a la vista lo que esté elegido
        folded = self._side_folded
        items.append(("hdr", "lists", "Tus listas"))
        rights.append("")
        for pl in self.playlists:
            if "lists" not in folded or ("pl", pl["id"]) in self.sel_keys:
                items.append(("pl", pl["id"], pl["name"]))
                rights.append(str(pl["count"]) if pl["count"] else "")
        if "lists" not in folded:
            items.append(("newpl", None, "＋ Nueva lista"))
            rights.append("")
        if counts:
            items.append(("hdr", "genres", "Géneros"))
            rights.append("")
        # los géneros de una sola canción se pliegan: la lista se lee de un vistazo
        chosen = {n for k, n in self.sel_keys if k == "lib"}
        few = [g for g, n in counts.items() if n == 1 and g not in chosen]
        few_folded = len(few) >= 3 and not self._genres_open
        for g, n in sorted(counts.items(), key=lambda x: fold(x[0])):
            if few_folded and g in few:
                continue
            if "genres" in folded and g not in chosen:
                continue
            items.append(("lib", g, g))
            rights.append(str(n))
        if len(few) >= 3 and "genres" not in folded:
            items.append(("more", None, "Ver menos" if self._genres_open
                          else f"{len(few)} géneros más…"))
            rights.append("")
        if self.yt_genres:
            items.append(("hdr", "yt", "YouTube"))
            rights.append("")
            for g in self.yt_genres:
                if "yt" not in folded or ("yt", g) in self.sel_keys:
                    items.append(("yt", g, g))
                    rights.append("")
        else:
            items.append(("hdr", None, "YouTube · sin conexión" if self.yt_failed
                          else "YouTube · cargando…"))
            rights.append("")
        self.side_items = items
        self.genres.sel.clear()
        rows = []
        for (k, n, label), right in zip(items, rights):
            row = dict(text=label, right=right, hdr=k == "hdr")
            if k == "hdr" and n is not None:  # se pliega: ▸ plegada, ▾ abierta
                row["fold"] = n in folded
                row["right"] = "▸" if n in folded else "▾"
            rows.append(row)
        self.genres.set_items(rows)
        self._mark_sidebar()

    def _toggle_side_section(self, index: int) -> None:
        """Pliega o despliega «Tus listas», «Géneros» o «YouTube» (se recuerda al cerrar)."""
        name = self.side_items[index][1] if index < len(self.side_items) else None
        if name is None:
            return
        self._side_folded ^= {name}
        self._build_sidebar()
        self.save_prefs()

    def _mark_sidebar(self) -> None:
        """El lateral marca la sección que se ve; con una página abierta (Ajustes…), ninguna."""
        self.genres.selection_clear()
        if self.active_page is not None:
            if self.active_page is self.pages.get("radio"):  # la página de la radio sí se marca
                index = next((i for i, (k, _n, _l) in enumerate(self.side_items)
                              if k == "radio"), None)
                if index is not None:
                    self.genres.selection_set(index)
            return
        for i, (k, n, _l) in enumerate(self.side_items):
            if (k, n) in self.sel_keys:
                self.genres.selection_set(i)
        if not self.genres.curselection():
            self.genres.selection_set(0)

    def _pick_genre(self, _e=None) -> None:
        sel = [self.side_items[i] for i in self.genres.curselection()]
        sel = [s for s in sel if s[0] != "hdr"]
        if any(s[0] == "more" for s in sel):  # desplegar o plegar géneros; la vista no cambia
            self._genres_open = not self._genres_open
            self._build_sidebar()
            return
        if any(s[0] == "newpl" for s in sel):
            self.new_playlist()
            return
        if any(s[0] == "radio" for s in sel):  # la radio es una página, no una vista de canciones
            self.open_radio()
            return
        if not sel:
            return
        if ({(k, n) for k, n, _l in sel} == self.sel_keys and not self.views
                and self.active_page is None and not self.query.get().strip()):
            return  # ya se está ahí
        with self._navigating():
            self.close_now_view()
            self._hide_page()
            had_query = bool(self.query.get())
            self._apply_section(sel)
            self._mark_sidebar()  # al salir de una página se marcaba aún la sección de antes
            if had_query:  # al explorar se deja la búsqueda de texto (y _on_query refresca)
                self.query.set("")
            else:
                self.refresh_view()
            self._fade_rows()

    def _apply_section(self, sel: list[tuple]) -> None:
        """Pone la sección del lateral elegida (Inicio, Todo, una lista, géneros…) sin pintar."""
        self.close_views()
        self.decade = None  # cada sección empieza sin filtro de década
        special = next((s for s in sel if s[0] in ("home", "liked", "recent", "top", "pl")),
                       None)
        if special:
            self.view_kind = "playlist" if special[0] == "pl" else special[0]
            self.playlist_id = special[1] if special[0] == "pl" else None
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

    def go_home(self, _e=None) -> str:
        """Vuelve a la pantalla principal: cierra páginas, cola y vista en grande, y abre «Inicio»."""
        self.close_now_view()
        if self.queue_win is not None:
            self._show_queue()
        if self.genres.items:
            self.genres.selection_clear()
            self.genres.selection_set(0)  # «Inicio»
            self._pick_genre()
        return "break"

    def _mark_enter(self, _e=None) -> None:
        self._mark_hover = True
        self.mark.configure(fg=self.scheme.get("accent", self.pal["fg"]))

    def _mark_leave(self, _e=None) -> None:
        self._mark_hover = False
        self.mark.configure(fg=self.pal["fg"])

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

        top = self.sub_view
        sections = None
        self._loose_rows = 0
        if top is not None:
            local, remote = self._sub_view_rows(top), []
            if top["kind"] == "artist" and not q:
                local, sections = self._artist_rows(local)
        elif self.view_kind == "home" and not q:
            (local, sections), remote = self._home_rows(), []
        elif self.view_kind == "playlist":
            local, remote = self._playlist_rows(self.playlist_id), []
        elif self.view_kind == "liked":
            # las filas de la biblioteca (con año, pista…), en el orden en que se marcaron
            local = [self.lib_by_id[t["video_id"]] for t in favorites.liked_tracks()
                     if t["video_id"] in self.lib_by_id]
            remote = []
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
        local = self._apply_decade(local, show=not q and sections is None and (
            top is None or top["kind"] != "album"))
        self._fill(local, remote, sections)
        if self.active_page is not None:
            return  # la cabecera pertenece a la página abierta
        self.header.set_trail(self._trail())
        self._update_hero(self.rows)
        self.header.set_title(self._view_title(), animate=not q)
        if top is not None:
            self.status.configure(text=self._sub_view_status(top, local))
        elif sections is not None:
            self.status.configure(text="Lo último que escuchaste, lo que más suena y lo nuevo"
                                  if local else "")
        elif self.view_kind == "playlist":
            self.status.configure(text=self._playlist_status(local))
        elif not self.searching:
            msg = f"{len(local)} en biblioteca"
            if remote:
                msg += f"  ·  {len(remote)} en YouTube"
            self.status.configure(text=msg)

    def _home_rows(self) -> tuple[list[dict], list[tuple[int, str]]]:
        """«Inicio»: una fila de portadas por estantería (en la lista, seis canciones)."""
        per = max(4, self.grid.columns) if self.view_mode == "grid" else 6
        self._home_per = per
        try:
            recent, top = history.recent(80), history.most_played(80)
            counts, liked = history.play_counts(), favorites.liked_tracks()
        except Exception as exc:  # noqa: BLE001 - sin historial, Inicio muestra lo nuevo
            _log.failure(log, "no se pudo leer el historial para Inicio", exc)
            recent, top, counts, liked = [], [], {}, []
        return home.shelves(self.lib_by_id, recent, top, self.library, liked, counts, per,
                            seed=datetime.date.today().toordinal(),
                            discover=self._discover_rows())

    def _grid_columns(self, cols: int) -> None:
        """La cuadrícula cambió de columnas: las estanterías de «Inicio» se rellenan de nuevo."""
        if (self.view_kind == "home" and self.sub_view is None and not self.query.get().strip()
                and self.view_mode == "grid" and max(4, cols) != self._home_per):
            self.refresh_view()

    def _artist_rows(self, rows: list[dict]) -> tuple[list[dict], list | None]:
        """Página de artista: primero lo que más escuchas suyo (hasta cinco) y luego el resto,
        con sus álbumes juntos. Si casi no hay escuchas, todo seguido, sin secciones."""
        try:
            counts = history.play_counts()
        except Exception as exc:  # noqa: BLE001 - sin historial, la página de siempre
            _log.failure(log, "no se pudo leer el historial para la página del artista", exc)
            return rows, None
        played = sorted((t for t in rows if counts.get(t["video_id"])),
                        key=lambda t: -counts[t["video_id"]])[:5]
        if not played or len(rows) - len(played) < 2:
            return rows, None
        ids = {t["video_id"] for t in played}
        self._loose_rows = len(played)
        return played + [t for t in rows if t["video_id"] not in ids], [
            (0, "Lo que más escuchas"), (len(played), "Discografía")]

    def _sub_view_status(self, view: dict, rows: list[dict]) -> str:
        n = len(rows)
        songs = f"{n} {'canción' if n == 1 else 'canciones'}"
        if view["kind"] == "album":
            minutes = round(sum(t.get("duration") or 0 for t in rows) / 60)
            parts = [songs] + ([f"{minutes} min"] if minutes else [])
        else:
            albums = len({album_key(t) for t in rows if album_key(t)})
            parts = [songs] + ([f"{albums} {'álbum' if albums == 1 else 'álbumes'}"] if albums else [])
        return "  ·  ".join(p for p in parts if p)

    # ---------- décadas ----------
    @staticmethod
    def _decade_label(decade: int) -> str:
        return f"{decade % 100:02d}s" if decade < 2000 else f"{decade}s"

    def _apply_decade(self, rows: list[dict], show: bool) -> list[dict]:
        """Muestra las décadas de la vista como filtros y aplica la elegida."""
        decades = sorted({t["year"] // 10 * 10 for t in rows if t.get("year")})
        if not show or len(decades) < 2:
            self.decade = None
            self.chips.set_options([])
            return rows
        if self.decade not in decades:
            self.decade = None
        self.chips.set_options([(None, "Todas")] + [(d, self._decade_label(d)) for d in decades],
                               self.decade)
        if self.decade is None:
            return rows
        return [t for t in rows if t.get("year") and t["year"] // 10 * 10 == self.decade]

    def _pick_decade(self, decade: int | None) -> None:
        self.decade = decade
        self.tree.selection_remove(*self.tree.selection())
        self.refresh_view()
        self._fade_rows()

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
        self._refresh_now_heart()
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
        if self.sub_view is not None:
            return self.sub_view["title"]
        if self.view_kind == "home":
            if self._welcome_wanted():
                return WELCOME_TITLE
            return home.greeting(datetime.datetime.now().hour)
        if self.view_kind == "playlist":
            return self._playlist_name(self.playlist_id) or "Lista"
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
        iid = self.tree.identify_row(e.y)
        self._set_hover_row(iid if iid.isdigit() else None)
        self._tree_tip(e, iid)
        self.tree.configure(cursor="hand2" if self._row_action(e) else "")

    def _row_action(self, e) -> str | None:
        """Lo que haría un clic aquí: play (▶), like (♡), more (⋯), download o nada."""
        iid = self.tree.identify_row(e.y)
        if not iid.isdigit() or iid != self._hover_row:
            return None
        column = self._tree_column(e.x)
        if column == "#0":
            return "play" if self.tree.item(iid, "text") == HOVER_PLAY else None
        if column != "state" or self.tree.identify_region(e.x, e.y) != "cell":
            return None
        value = self.tree.set(iid, "state")
        if value == DOWNLOAD_LABEL:
            return "download"
        if not value.startswith((HOVER_LEAD + HOVER_LIKE, HOVER_LEAD + HOVER_LIKED)):
            return None
        box = self.tree.bbox(iid, "state")
        split = (box[0] + self.fonts["body"].measure(HOVER_LEAD + HOVER_LIKE + HOVER_GAP[:3])
                 + int(6 * self.k) if box else e.x + 1)
        return "like" if e.x < split else "more"

    def _row_number(self, t: dict) -> str:
        album = self.sub_view is not None and self.sub_view["kind"] == "album"
        return str(t.get("track_no") or "") if album else ""

    def _hover_actions(self, iid: str, on: bool) -> None:
        """▶ en lugar del número y, en la columna de estado, Me gusta y «⋯» (si está libre:
        una descarga en curso o «↓ Descargar» se quedan como están)."""
        i = int(iid)
        if i >= len(self.rows):
            return
        t = self.rows[i]
        state = self._row_state(t)
        free = not state or state.startswith("×")
        if on:
            if t["video_id"] != self.playing_vid:
                self.tree.item(iid, text=HOVER_PLAY)
            if free:
                heart = HOVER_LIKED if t["video_id"] in self.liked else HOVER_LIKE
                self.tree.set(iid, "state", HOVER_LEAD + heart + HOVER_GAP + HOVER_MORE)
        else:
            self.tree.item(iid, text=self._row_number(t))
            if free:
                self.tree.set(iid, "state", state)

    def _tree_tip(self, e, iid: str) -> None:
        """Texto completo de la celda bajo el puntero, si la columna lo recorta."""
        text = None
        column = self._tree_column(e.x) if iid.isdigit() else ""
        if column in ("title", "artist", "album"):
            value = self.tree.set(iid, column)
            room = self.tree.column(column, "width") - int(14 * self.k)
            if value and self.fonts["body"].measure(value) > room:
                text = value
        self.tree_tip.set(text)

    def _tree_leave(self, _e=None) -> None:
        self._set_hover_row(None)
        self.tree_tip.set(None)

    def _tree_column(self, x: int) -> str:
        """Nombre de la columna bajo `x` (las columnas visibles cambian según la página)."""
        column = self.tree.identify_column(x)
        if not column or column == "#0":
            return column
        try:
            return self.tree.column(column, "id")
        except tk.TclError:
            return ""

    def _tree_click(self, e) -> str | None:
        """Clic en lo que aparece al pasar el ratón: ▶ reproduce, ♡ marca Me gusta, «⋯» abre el
        menú y «↓ Descargar» descarga un resultado de YouTube."""
        if self.dragging:  # soltar lo arrastrado no es pulsar lo que haya debajo
            return None
        action = self._row_action(e)
        if action is None:
            return None
        iid = self.tree.identify_row(e.y)
        t = self.rows[int(iid)]
        if action == "play":
            self.play_index(int(iid))
        elif action == "like":
            self.toggle_like([t])
        elif action == "more":
            self._context_at(iid, e.x_root, e.y_root)
        elif action == "download":
            self.download_tracks([t])
        return "break"

    def _set_hover_row(self, iid: str | None) -> None:
        if iid == self._hover_row:
            return
        for row, change in ((self._hover_row, False), (iid, True)):
            if row and self.tree.exists(row):
                tags = [x for x in self.tree.item(row, "tags") if x != "hover"]
                self.tree.item(row, tags=[*tags, "hover"] if change else tags)
                self._hover_actions(row, change)
        self._hover_row = iid
        self._place_eq()

    def _tree_scrolled(self, first: str, last: str) -> None:
        """La barra de desplazamiento solo aparece si hay algo que desplazar."""
        self.sb.set(first, last)
        self._hero_scrolled(float(first))
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
        return self.dl.state.get(t["video_id"]) or ("" if "path" in t else DOWNLOAD_LABEL)

    def _sort_key(self, col: str):
        if col == "dur":
            return lambda t: t.get("duration") or 0
        if col == "year":
            return lambda t: t.get("year") or 0
        if col == "added":
            return lambda t: t.get("added_at") or ""
        if col == "plays":
            counts = history.play_counts()
            return lambda t: counts.get(t["video_id"], 0)
        return lambda t: fold(t.get(col) or "")

    def _fill(self, local: list[dict], remote: list[dict] | tuple = (),
              sections: list[tuple[int, str]] | None = None) -> None:
        """Pinta la vista: lo tuyo primero y, si hay resultados de YouTube, en su sección.

        `sections` trae secciones propias («Inicio»): cada una ya viene en su orden."""
        self.raw = (local, list(remote), sections)
        album = self.sub_view is not None and self.sub_view["kind"] == "album"
        if self.sort and not album and sections is None:  # un álbum, en orden de pista
            col, rev = self.sort
            local = sorted(local, key=self._sort_key(col), reverse=rev)
            remote = sorted(remote, key=self._sort_key(col), reverse=rev)
        rows = list(local) + list(remote)
        self.rows = rows
        # secciones solo si hay resultados de YouTube: así se distingue qué ya es tuyo
        self.sections = list(sections or [])
        if remote and sections is None:
            if local:
                self.sections.append((0, f"En tu biblioteca · {len(local)}"))
            self.sections.append((len(local), f"En YouTube · {len(remote)}"))
        self.tree.delete(*self.tree.get_children())
        self.vid_iid = {}
        self._hover_row = None
        starts = {start: title for start, title in self.sections}
        for i, t in enumerate(rows):
            if i in starts:
                self.tree.insert("", "end", iid=f"§{i}", tags=("section",),
                                 values=(starts[i].upper(), "", "", "", ""))
            self.vid_iid[t["video_id"]] = str(i)
            heart = "♥  " if t["video_id"] in self.liked else ""
            number = str(t.get("track_no") or "") if album else ""
            self.tree.insert("", "end", iid=str(i), text=number, tags=self._row_tags(t), values=(
                heart + t["title"], t["artist"], t.get("album") or "", fmt(t.get("duration")),
                self._row_state(t)))
        # en un álbum sobran artista y álbum; en la página del artista, el artista
        top = self.sub_view
        shown = [c[0] for c in COLS]
        if top is not None:
            drop = {"artist", "album"} if top["kind"] == "album" else {"artist"}
            shown = [c for c in shown if c not in drop]
        self.tree.configure(displaycolumns=shown)
        self._shown_cols = shown
        self._fit_columns()
        self.tree.heading("#0", text="#" if album else "")
        if self.view_mode == "grid":
            self._sync_grid()
        self._sort_headings()
        self._sync_toolbar()
        self._update_empty()
        self._place_eq()

    def _fit_columns(self, _e=None) -> None:
        """Las columnas que se ven llenan el ancho: al ocultar artista y álbum (en un álbum)
        quedaba un hueco a la derecha. Título, artista y álbum se reparten lo que sobra."""
        width = self.tree.winfo_width()
        shown = getattr(self, "_shown_cols", [c[0] for c in COLS])
        if width <= 1:
            return
        k = self.k
        base = {c: int(w * k) for c, _t, w in COLS}
        flex = [c for c in shown if c in ("title", "artist", "album")]
        fixed = int(30 * k) + sum(base[c] for c in shown if c not in flex)
        room = max(len(flex) * int(90 * k), width - fixed - 4)
        total = sum(base[c] for c in flex) or 1
        for c in flex:
            self.tree.column(c, width=max(int(90 * k), room * base[c] // total))

    def _update_empty(self) -> None:
        if self._sync_welcome():  # biblioteca vacía: la bienvenida ocupa el sitio de la tabla
            self.empty.place_forget()
            return
        if self.rows:
            self.empty.place_forget()
            return
        if self.searching:
            text = "Buscando…"
        elif self.query.get().strip():
            text = "Nada encontrado. Prueba con otra búsqueda."
        elif self.view_kind == "liked":
            text = "Aún no has marcado ninguna canción. Pulsa ♡ en una portada o en el reproductor."
        elif self.view_kind in ("recent", "top"):
            text = "Todavía no hay reproducciones registradas."
        elif self.view_kind == "playlist":
            text = ("Esta lista está vacía. Añade canciones con clic derecho → «Añadir a lista»\n"
                    "desde la biblioteca, una búsqueda o un álbum.")
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
        if col == "state" or self.raw[2] is not None:  # «Inicio» no se reordena
            return
        if self.sort and self.sort[0] == col:
            self.sort = (col, True) if not self.sort[1] else None  # asc → desc → sin orden
        else:
            self.sort = (col, False)
        self._fill(*self.raw)

    # ---------- orden ----------
    SORTS = ((None, "Predeterminado", False), ("added", "Recién añadidas", True),
             ("title", "Título", False), ("artist", "Artista", False), ("album", "Álbum", False),
             ("year", "Año (más reciente)", True), ("plays", "Más escuchadas", True),
             ("dur", "Duración", False))

    def _sort_label(self) -> str:
        if not self.sort:
            return "Ordenar ▾"
        col, rev = self.sort
        name = next((label for key, label, _r in self.SORTS if key == col), col)
        if col in ("title", "artist", "album") and rev:
            name += " (Z–A)"
        return f"Orden: {name} ▾"

    def _sort_menu(self) -> None:
        m = self._menu()
        current = self.sort[0] if self.sort else None
        for key, label, rev in self.SORTS:
            mark = "✓  " if key == current else "     "
            m.add_command(label=mark + label,
                          command=lambda k=key, r=rev: self.set_sort((k, r) if k else None))
        x = self.btn_sort.winfo_rootx()
        y = self.btn_sort.winfo_rooty() + self.btn_sort.winfo_height()
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def set_sort(self, sort: tuple[str, bool] | None) -> None:
        self.sort = sort
        self._fill(*self.raw)
        self._fade_rows()

    def _sync_toolbar(self) -> None:
        """En un álbum no hay orden ni cambio de vista (es una lista)."""
        top = self.sub_view
        album = top is not None and top["kind"] == "album"
        (self.btn_view.grid_remove if album else self.btn_view.grid)()
        # en «Inicio» cada estantería trae su propio orden
        self.btn_sort.set_visible(not album and self.raw[2] is None)
        self.btn_sort.configure(text=self._sort_label())

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

    def _sel_rows(self) -> list[int]:
        """Filas seleccionadas (sin los títulos de sección, que no son canciones)."""
        return sorted(int(i) for i in self.tree.selection()
                      if i.isdigit() and int(i) < len(self.rows))

    def _selected(self) -> list[dict]:
        return [self.rows[i] for i in self._sel_rows()]

    # ---------- menús ----------
    def _menu(self, master=None) -> tk.Menu:
        p = self.pal
        return tk.Menu(master or self, tearoff=0, bg=p["panel"], fg=p["fg"],
                       activebackground=p["sel"], activeforeground=p["fg"], borderwidth=0)

    def toggle_like(self, tracks: list[dict] | None = None) -> None:
        """Marca o desmarca con «Me gusta» `tracks` (por defecto, la selección o lo que suena).

        Si alguna no estaba marcada, se marcan todas; si ya lo estaban, se desmarcan.
        """
        if tracks is None:
            tracks = self._selected() or ([self.cur_track] if self.cur_track else [])
        if not tracks:
            return
        picks = [t for t in tracks if t["video_id"] in self.lib_by_id]
        if not picks:
            self.toast.show("Solo se pueden guardar en Me gusta canciones descargadas")
            return
        want = not all(t["video_id"] in self.liked for t in picks)
        before = {t["video_id"]: t["video_id"] in self.liked for t in picks}
        self._apply_likes({vid: want for vid in before})
        one = len(picks) == 1
        what = f"«{picks[0]['title']}»" if one else f"{len(picks)} canciones"
        verb = ("guardada en" if want else "quitada de") if one else (
            "guardadas en" if want else "quitadas de")
        self.toast.show(f"{what} {verb} Me gusta", "Deshacer",
                        on_action=lambda: self._apply_likes(before))

    def _apply_likes(self, states: dict[str, bool]) -> None:
        for vid, liked in states.items():
            favorites.set_liked(vid, liked)
        # contador del lateral, corazones y vista «Me gusta»; la selección y el sitio se quedan
        self._refresh_keep_selection()
        cur = self.cur_track["video_id"] if self.cur_track else None
        if cur in states:
            self._refresh_now_heart(pop=states[cur])

    def like_current(self) -> None:
        """El corazón del reproductor: marca o desmarca la canción que suena (o, con la radio,
        la emisora como favorita)."""
        if self.radio_on:
            self.toggle_station_favorite(self.radio_station)
            return
        if self.cur_track:
            self.toggle_like([self.cur_track])

    def _refresh_now_heart(self, pop: bool = False) -> None:
        """Corazón junto al título: solo si la canción que suena está en la biblioteca (con la
        radio, si la emisora es favorita)."""
        if self.radio_on:
            self.now.set_liked(self.radio_station["uuid"] in self.radio_favs, pop=pop)
            self.now.set_artist_link(False)
            if self.now_view is not None:
                self.now_view.set_links(self.now.liked, False, False, pop=pop)
            return
        t = self.cur_track
        if t is None or t["video_id"] not in self.lib_by_id:
            self.now.set_liked(None)
        else:
            self.now.set_liked(t["video_id"] in self.liked, pop=pop)
        self.now.set_artist_link(t is not None and self._has_artist(t.get("artist") or ""))
        if self.now_view is not None:
            self.now_view.set_links(self.now.liked, self.now.artist_link,
                                    self._now_album_key() is not None, pop=pop)

    def _refresh_keep_selection(self) -> None:
        """Recarga la biblioteca sin perder la selección ni la posición de la lista."""
        keep = [t["video_id"] for t in self._selected()]
        top = self.tree.yview()[0]
        self.refresh_library()
        if self.view_mode == "list":
            self.tree.yview_moveto(top)
        iids = [self.vid_iid[vid] for vid in keep if vid in self.vid_iid]
        if iids:
            self.tree.selection_set(iids)
            self._on_select()

    def _undo_key(self, _e=None) -> str | None:
        """Ctrl+Z: la acción del aviso visible («Deshacer»), salvo mientras se escribe."""
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None
        return "break" if self.toast.act() else None

    # ---------- duplicados y etiquetas ----------
    def find_duplicates(self) -> None:
        groups = duplicates.find()
        if not groups:
            self.toast.show("No hay canciones repetidas")
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
        message = f"{removed} canciones repetidas borradas"
        if failed:
            message += f" · {len(failed)} no se pudieron borrar (puede que estén en uso)"
        self.toast.show(message)

    def _check_duplicates(self) -> None:
        """Busca repetidas en segundo plano; si hay, se ofrece revisarlas con un aviso."""
        def work() -> None:
            try:
                extras = sum(len(group.extras) for group in duplicates.find())
            except Exception as exc:  # noqa: BLE001 - es solo una sugerencia
                _log.failure(log, "no se pudieron buscar canciones repetidas", exc)
                return
            self.q.put(("duplicates", extras))

        threading.Thread(target=work, daemon=True, name="repetidas").start()

    def _offer_duplicates(self, extras: int) -> None:
        # no insiste: solo vuelve a avisar si el número de repetidas cambia
        if not extras or extras == self._dupes_seen:
            return

        def seen() -> None:
            self._dupes_seen = extras
            self.save_prefs()

        self.toast.show(f"Hay {extras} canciones repetidas en tu biblioteca", "Revisar",
                        on_action=lambda: (seen(), self.find_duplicates()), on_expire=seen)

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
            self.toast.show(f"No se pudo exportar: {exc.strerror or exc}")
            return
        self.toast.show(f"{count} canciones exportadas a {Path(dest).name}")

    def clean_missing(self) -> None:
        missing = library.missing_files()
        if not missing:
            self.toast.show("Todas las canciones de la biblioteca tienen su archivo")
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
        self.toast.show(f"{count} canciones quitadas de la biblioteca")

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
        """Activa o desactiva que Sound se abra al iniciar sesión."""
        wanted = not self.start_with_windows
        if not startup.set_enabled(wanted):
            self.status.configure(
                text="No se pudo cambiar el inicio automático. Revisa los permisos del usuario.")
            return
        self.start_with_windows = wanted
        self.status.configure(text=(
            "Sound se abrirá al iniciar sesión"
            if wanted else "Sound ya no se abrirá al iniciar sesión"))

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

    # ---------- descargas / borrado ----------
    def download_selected(self) -> None:
        self.download_tracks(self._selected())

    def download_tracks(self, tracks: list[dict]) -> None:
        """Descarga los resultados de YouTube de `tracks` que aún no tienes ni están en camino."""
        picks = [t for t in tracks
                 if "path" not in t and t["video_id"] not in self.lib_by_id
                 and self.dl.state.get(t["video_id"], "") in ("", "✗")]
        if not picks:
            self.toast.show("Elige canciones de YouTube que aún no tengas para descargarlas")
            return
        by_genre: dict[str | None, list[dict]] = {}
        for t in picks:
            by_genre.setdefault(t.get("_genre"), []).append(t)
        for genre, group in by_genre.items():  # una escritura en la base por género, no una por canción
            self.dl.submit(group, genre)
        what = f"«{picks[0]['title']}»" if len(picks) == 1 else f"{len(picks)} canciones"
        self.toast.show(f"Descargando {what}…")

    def _grid_download(self, i: int) -> None:
        self.download_tracks([self.rows[row] for row in self.grid.members[i]
                              if row < len(self.rows)])

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
        if self._in_playlist():  # la lista abierta, también desde aquí
            pid = self.playlist_id
            m.add_separator()
            m.add_command(label="Reproducir la lista", command=lambda: self._play_playlist(pid))
            m.add_command(label="Renombrar la lista", command=lambda: self.rename_playlist(pid))
            m.add_command(label="Exportar la lista a M3U…",
                          command=lambda: self.export_playlist(pid))
            m.add_command(label="Eliminar la lista", command=lambda: self.delete_playlist(pid))
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
        elif name == "radio":
            from .radio_page import RadioPage
            page = RadioPage(self)
        else:
            raise KeyError(name)
        self.pages[name] = page
        return page

    def show_page(self, name: str) -> None:
        """Trae una página al frente, a la derecha del lateral; nunca abre otra ventana."""
        page = self._ensure_page(name)
        if self.active_page is page:
            page.activate()
            return
        with self._navigating():
            self._show_page_now(name)

    def _show_page_now(self, name: str) -> None:
        page = self._ensure_page(name)
        if self._qdebounce:  # una búsqueda a medio escribir no debe cerrar la página sola
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        if self.queue_win is not None:  # el panel «A continuación» no debe quedar debajo
            self._show_queue()
        self.close_now_view()  # la página quedaría tapada por la vista en grande
        if self.active_page is not None:
            self.active_page.deactivate()
            self.active_page.place_forget()  # si no, al cerrar la nueva reaparecería la anterior
        self.active_page = page
        page.restyle()
        # ocupa la columna de la lista: el lateral sigue a la vista para ir a cualquier sección
        page.place(in_=self.mid, relx=0, rely=0, relwidth=1, relheight=1)
        page.lift()
        self.header.set_trail([])
        self.header.set_hero(None)
        self.header.set_title(page.page_title)
        page.activate()
        self._refresh_nav()
        self._mark_sidebar()

    def close_page(self, *_args) -> None:
        """Quita la página y enseña lo que había debajo."""
        if self.active_page is None:
            return
        with self._navigating():
            self._hide_page()
            self.refresh_view()
            (self.grid if self.view_mode == "grid" else self.tree).focus_set()

    def _hide_page(self) -> None:
        """Quita la página abierta sin repintar la lista (quien llama la repinta)."""
        page = self.active_page
        if page is None:
            return
        page.deactivate()
        page.place_forget()
        self.active_page = None
        self._refresh_nav()
        self._mark_sidebar()

    def _page_back(self) -> None:
        """El enlace «← …» de una página: vuelve a donde se estaba."""
        if self.nav.can_back:
            self.go_back()
        else:
            self.close_page()

    # ---------- historial (ver navigation.py) ----------
    def _restore_place(self, place: Place) -> None:
        """Deja la ventana en un sitio guardado del historial."""
        self.close_now_view()
        self._hide_page()
        keys = set(place.section)
        sel = [item for item in self.side_items if (item[0], item[1]) in keys]
        self._apply_section(sel or self.side_items[:1])  # una lista borrada: Inicio
        self.views = [{"kind": kind, "key": key, "title": title, "artist": artist}
                      for kind, key, title, artist in place.views]
        self._sync_toolbar()
        self._set_view(self._display_mode())
        self.decade = place.decade
        self.tree.selection_remove(*self.tree.selection())
        if self.query.get() != place.query:
            self.query.set(place.query)  # vuelve a buscar (y _on_query repinta)
        else:
            self.refresh_view()
        self._mark_sidebar()
        if place.page:
            self._show_page_now(place.page)
            return
        self._fade_rows()
        self._restore_spot(place)
        (self.grid if self.view_mode == "grid" else self.tree).focus_set()
        top = self.sub_view
        if top is not None and top["kind"] == "album":
            self._want_track_numbers(top)

    def _restore_spot(self, place: Place) -> None:
        """Selección y desplazamiento de cuando se dejó el sitio."""
        keep = [self.vid_iid[vid] for vid in place.selected if vid in self.vid_iid]
        if keep:
            self.tree.selection_set(keep)
            self._on_select()

        def scroll() -> None:
            if self.view_mode == "grid":
                self.grid.scroll_to_fraction(place.scroll)
            else:
                self.tree.yview_moveto(place.scroll)

        self.after_idle(scroll)

    def _scroll_fraction(self) -> float:
        try:
            if self.view_mode == "grid":
                return self.grid.scroll_fraction()
            return float(self.tree.yview()[0])
        except (tk.TclError, AttributeError, ValueError):
            return 0.0

    def _sync_nav(self) -> None:
        """Botones ‹ › y el enlace de vuelta de la página abierta."""
        self.btn_nav_back.set_enabled(self.nav.can_back)
        self.btn_nav_fwd.set_enabled(self.nav.can_forward)
        page = self.active_page
        if page is not None and hasattr(page, "set_back_label"):
            page.set_back_label(self._place_label(self.nav.peek_back()) or "Inicio")

    def _nav_tip(self, forward: bool) -> str:
        place = self.nav.peek_forward() if forward else self.nav.peek_back()
        name, key = ("Adelante", "Alt+→") if forward else ("Atrás", "Alt+←")
        label = self._place_label(place)
        return f"{name}: {label} ({key})" if label else f"{name} ({key})"

    def _escape(self, _e=None) -> str | None:
        """Esc cierra lo que está encima y, en una página o un álbum, vuelve atrás."""
        if self.fullscreen:  # primero se sale de la pantalla completa; luego, de la vista
            self._set_fullscreen(False)
            return "break"
        if self.now_open:
            self.close_now_view()
            return "break"
        if self.queue_win is not None:  # el panel «A continuación» también se cierra con Esc
            self._show_queue()
            return "break"
        typing = isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text))
        if self.active_page is not None or (self.views and not typing):
            if self.nav.can_back:
                self.go_back()
            elif self.active_page is not None:
                self.close_page()
            else:
                self.close_view()
            return "break"
        return None

    def _refresh_nav(self) -> None:
        for name, button in (("downloads", self.btn_downloads),
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
        self._refresh_downloads_badge()

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
        sel = self._sel_rows()
        if len(sel) == 1 and self._is_remote(self.rows[sel[0]]):
            vids.add(self.rows[sel[0]]["video_id"])
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
        headers = [i for i in self.tree.selection() if not i.isdigit()]
        if headers:  # un título de sección no se selecciona: con el teclado, se salta
            self.tree.selection_remove(*headers)
            if not self.tree.selection():
                start = int(headers[0][1:])  # «§N»: la sección empieza en la fila N
                going_down = self._last_row is None or self._last_row < start or start == 0
                target = str(start if going_down else start - 1)
                if self.tree.exists(target):
                    self.tree.selection_set(target)
                    self.tree.focus(target)
                    self.tree.see(target)
            return  # el cambio de selección vuelve a llamar aquí
        sel = self._sel_rows()
        self._last_row = sel[0] if sel else None
        self._place_eq()
        if self.view_mode == "grid":
            self.grid.select([str(i) for i in sel])
        self.btn_dl.set_visible(any(self._is_remote(self.rows[i]) for i in sel))
        if len(sel) == 1:
            t = self.rows[sel[0]]
            if self._is_remote(t):
                self._resolve_ahead([t["video_id"]])

    def _select_all(self, _e=None) -> str | None:
        """Selecciona todas las canciones de la vista actual (Ctrl+A)."""
        # En un campo de texto, Ctrl+A debe seguir seleccionando el texto.
        if self.active_page is not None or isinstance(
                self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return None
        rows = [str(i) for i in range(len(self.rows))]
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
        """Quita las canciones de la vista ya, pero no las borra del disco hasta que el aviso
        desaparece: mientras tanto, «Deshacer» (o Ctrl+Z) las devuelve tal cual.

        Dentro de una lista, Supr la quita de la lista: el archivo no se toca."""
        if self._in_playlist():
            self.remove_from_playlist()
            return
        picks = [t for t in self._selected() if "path" in t]
        if not picks:
            return
        ids = {t["video_id"] for t in picks}
        if self.playing_vid in ids:
            self.player.stop()
            self.btn_play.configure(text="▶")
            self.playing_vid = None
        self._hidden |= ids
        self.tree.selection_remove(*self.tree.selection())
        self.refresh_library()
        what = f"«{picks[0]['title']}»" if len(picks) == 1 else f"{len(picks)} canciones"
        self.toast.show(f"{what} {'eliminada' if len(picks) == 1 else 'eliminadas'}", "Deshacer",
                        on_action=lambda: self._undo_delete(ids),
                        on_expire=lambda: self._commit_delete(picks))

    def _undo_delete(self, ids: set[str]) -> None:
        self._hidden -= ids
        self.refresh_library()
        iids = [self.vid_iid[vid] for vid in ids if vid in self.vid_iid]
        if iids:
            self.tree.selection_set(iids)
            self._on_select()
            if self.view_mode == "grid":
                self.grid.see_row(int(iids[0]))
            else:
                self.tree.see(iids[0])

    def _commit_delete(self, picks: list[dict]) -> None:
        """Borrado definitivo: índice, archivo, portada y onda."""
        for t in picks:
            vid = t["video_id"]
            library.remove_track(vid)
            waveform.forget(vid)
            try:
                Path(t["path"]).unlink(missing_ok=True)
                covers.cache_path(vid).unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar %s", t["path"], exc_info=True)
            self.dl.state.pop(vid, None)
            self._hidden.discard(vid)

    # ---------- menú contextual ----------
    def _context(self, e) -> None:
        iid = self.tree.identify_row(e.y)
        if iid.isdigit():  # los títulos de sección no tienen menú
            self._context_at(iid, e.x_root, e.y_root)

    def _context_at(self, iid: str | None, x_root: int, y_root: int,
                    album: dict | None = None) -> None:
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self._on_select()
        m = self._menu()
        if album is not None:  # tarjeta de álbum: el resto del menú actúa sobre todas sus canciones
            m.add_command(label="Reproducir álbum",
                          command=lambda: self.play_tracks(album["_tracks"]))
            m.add_command(label="Abrir álbum", command=lambda: self.open_album(album))
        else:
            m.add_command(label="Reproducir", command=lambda: self.play_index(int(iid)))
        m.add_command(label="Reproducir a continuación",
                      command=lambda: self.enqueue(self._selected(), front=True))
        m.add_command(label="Añadir a la cola", command=lambda: self.enqueue(self._selected()))
        m.add_cascade(label="Añadir a lista", menu=self._playlist_menu(m, self._selected()))
        picked = self._selected()
        seed = album["_tracks"][0] if album is not None else (picked[0] if len(picked) == 1 else None)
        if seed is not None:  # una radio sin fin que empieza por esta canción
            m.add_command(label="Iniciar radio", command=lambda: self.start_track_station(seed))
        if self._in_playlist():
            m.add_command(label="Quitar de esta lista", accelerator="Supr",
                          command=self.remove_from_playlist)
            if self._can_reorder():
                m.add_command(label="Subir", accelerator="Alt+↑",
                              command=lambda: self.move_in_playlist(-1))
                m.add_command(label="Bajar", accelerator="Alt+↓",
                              command=lambda: self.move_in_playlist(1))
        if self.pq.up_next:
            m.add_command(label=f"Vaciar cola ({len(self.pq.up_next)})", command=self.clear_queue)
        picks = self._selected()
        local = [t for t in picks if "path" in t or t["video_id"] in self.lib_by_id]
        # ir a la página del álbum o del artista (si no es la que ya está abierta)
        top = self.sub_view
        here = (top["kind"], top["key"]) if top is not None else None
        first = album["_tracks"][0] if album is not None else (picks[0] if len(picks) == 1 else None)
        if first is not None:
            lib = self.lib_by_id.get(first["video_id"], first)
            artist = (lib.get("artist") or "").split(",")[0].strip()
            go_album = album is None and album_key(lib) and here != ("album", album_key(lib))
            go_artist = self._has_artist(artist) and here != ("artist", artist_key(lib))
            if go_album or go_artist:
                m.add_separator()
            if go_album:
                name = lib.get("album") or ""
                name = name if len(name) <= 40 else name[:39].rstrip() + "…"
                m.add_command(label=f"Ir al álbum «{name}»", command=lambda: self.open_album(lib))
            if go_artist:
                m.add_command(label=f"Ir a {artist}", command=lambda: self.open_artist(artist))
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
            m.add_command(label="Eliminar de la biblioteca", command=self.delete_selected)
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

    # ---------- reproducción desde la lista ----------
    def _on_double(self, e=None) -> None:
        if (e is not None and str(e.type) == "ButtonPress"
                and self._tree_column(e.x) in ("#0", "state")):
            return  # ▶, ♡ y «⋯» ya actuaron con el primer clic
        sel = self._sel_rows()
        if sel:
            self.play_index(sel[0])

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
            self._discover_online()
            self.dl.download_ahead()
        self._refresh_queue()

    # ---------- letras ----------
    def _lyrics_wanted(self) -> bool:
        """La letra se ve en el panel lateral o en la pestaña «Letra» de la vista en grande."""
        return ((self.queue_win is not None and self.drawer_mode == "lyrics")
                or (self.now_open and self.now_view.tab == "lyrics" and not self.radio_on))

    def _lyrics_publish(self, payload: dict | None = None, message: str = "",
                        sub: str | None = None) -> None:
        """Lleva la letra (o el aviso que la sustituye) a los dos sitios donde se muestra."""
        self._lyrics_shown = (payload, message, sub)
        if message:
            self.lyrics_view.set_message(message)
        else:
            self.lyrics_view.show(payload)
        if sub is not None and self.drawer_mode == "lyrics":
            self.drawer_sub.configure(text=sub)
        if self.now_view is not None:
            if self.lyrics_enabled:
                retry = ("Volver a intentarlo", self._retry_lyrics) \
                    if message == "No se pudo buscar la letra." else None
                self.now_view.set_lyrics(payload, message, retry)
            else:  # se ofrece activarlas ahí mismo, pero diciendo antes qué se envía y a quién
                self.now_view.set_lyrics(None, LYRICS_NOTICE, ("Activar letras", self.toggle_lyrics))

    def _load_lyrics(self, t: dict | None) -> None:
        """Pide la letra de la canción actual y la muestra; nunca bloquea el hilo de la ventana."""
        if not self.lyrics_enabled:
            self._lyrics_payload = self._lyrics_vid = None
            self._lyrics_publish(message="Activa las letras en Ajustes › Reproducción.")
            return
        if not t:
            self._lyrics_payload = self._lyrics_vid = None
            self._lyrics_publish(message="Sin canción.")
            return
        vid = t["video_id"]
        if vid == self._lyrics_vid:
            payload, message, sub = self._lyrics_shown
            self._lyrics_publish(payload, message, sub)  # el otro sitio también la quiere
            self._sync_lyrics(force=True)
            return
        self._lyrics_vid, self._lyrics_payload = vid, None
        cached = lyrics_store.cached(vid)
        if cached is not None:
            self._apply_lyrics(vid, cached)
            if not lyrics_store.stale(cached):
                return
            # se vuelve a mirar por detrás (p. ej. había solo texto y quizá ya hay tiempos)
        else:
            self._lyrics_publish(message="Buscando letra…")
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
            self._lyrics_publish(message="No se pudo buscar la letra.", sub="Sin conexión con LRCLIB")
        elif payload.get("negative"):
            self._lyrics_publish(message="Sin letra para esta canción.",
                                 sub="LRCLIB no tiene esta letra")
        else:
            self._lyrics_publish(payload, sub="Sincronizada" if payload.get("synced")
                                 else "Letra sin tiempos")
        self._sync_lyrics(force=True)

    def _retry_lyrics(self) -> None:
        self._lyrics_vid = None
        self._load_lyrics(self.cur_track)

    def _sync_lyrics(self, force: bool = False) -> None:
        if not self._lyrics_payload or not self.player.loaded:
            return
        try:
            position = self.player.position()
        except Exception:  # noqa: BLE001 - la letra no debe parar el tick de reproducción
            return
        if self.drawer_mode == "lyrics" and self.queue_win is not None:
            self.lyrics_view.sync(position, force=force)

    def toggle_lyrics(self) -> None:
        self.lyrics_enabled = not self.lyrics_enabled
        self.var_lyrics.set(self.lyrics_enabled)
        self.save_prefs()
        if self._lyrics_wanted():
            self._lyrics_vid = None
            self._load_lyrics(self.cur_track)
        self.status.configure(text="Letras " + ("activadas" if self.lyrics_enabled
                                                else "desactivadas"))

    # ---------- reproducción: cargar, sonar, volumen y pasos ----------
    def _play_track(self, t: dict, how: str = "manual") -> None:
        """`how`: "auto" = fin natural (mezcla con la que suena), "manual" = la pediste tú
        (fundido corto), "cut" = sin fundido de salida."""
        self.play_token += 1
        token = self.play_token
        vid = t["video_id"]
        self._leave_radio()  # una canción saca del modo radio (el fundido corta la emisora)
        if how == "manual":
            self._sleep_wake()  # la pediste tú: ya no se está apagando
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
        self._continue_after_album(t)
        self._refresh_queue()
        iid = self.vid_iid.get(vid)
        if iid is not None and self.tree.exists(iid):
            self.tree.see(iid)
        if self.view_mode == "grid" and iid is not None:
            self.grid.see_row(int(iid))
        self.now.set(t["title"], t.get("artist") or "")
        self._refresh_now_heart()
        self._system_media_track(t)
        if self.now_open:
            self._now_track()
            self._now_album()
            self._now_queue()
        if self._lyrics_wanted():
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
        if path:
            self._ensure_playable(vid, path)
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
        self._clock_ref = (0, time.monotonic())
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
                    self._clock_ref = (ms, time.monotonic())
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
        if self.radio_on:  # pausa corta la emisora; play vuelve al directo
            self._sleep_wake()
            self._radio_toggle()
            return
        if self._sleep_wake():  # el temporizador paró al acabar la canción: sigue la siguiente
            self.step(1)
            return
        if not self.player.loaded:
            if self.resume_track is not None:
                track = self.resume_track
                position = self.resume_position
                self.resume_track = None
                self.resume_position = 0
                self.pending_seek = (track["video_id"], position)
                self._play_track(track)
                return
            sel = self._sel_rows()
            self.play_index(sel[0] if sel else 0)
            return
        self.player.toggle()
        self.btn_play.configure(text="▶" if self.player.paused else "⏸")

    def step(self, d: int, auto: bool = False) -> None:
        if self.radio_on:  # ⏮ ⏭ cambian de emisora; en directo no hay «siguiente» automático
            if not auto:
                self._radio_step(d)
            return
        if auto and self._sleep_stops_queue():  # temporizador para dormir: aquí se para
            return
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
        self.set_xfade(XFADE_CHOICES[(i + 1) % len(XFADE_CHOICES)])

    def set_xfade(self, ms: int) -> None:
        self.xfade_ms = ms if ms in XFADE_CHOICES else 4000
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
        self.set_repeat((self.pq.repeat + 1) % 3)

    def set_repeat(self, mode: int) -> None:
        """0 sin repetir, 1 toda la lista, 2 la canción que suena."""
        self.pq.repeat = int(mode) % 3
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
        if self.radio_on:  # en directo no se adelanta
            self.scale.set(0)
            return
        if self.player.loaded and self.player.length:
            ms = self.scale.get() / 1000 * self.player.length
            self.player.seek(ms)
            self._clock_ref = (int(ms), time.monotonic())

    def _tick(self) -> None:
        """Reprograma siempre: un fallo en un fotograma no debe congelar la reproducción."""
        try:
            self._tick_once()
            self._sleep_tick()
            self._sync_outside()
            self._count_downloads()
        except Exception as exc:  # noqa: BLE001 - el bucle de Tk no debe quedarse sin tick
            _log.failure(log, "el tick de reproducción falló", exc)
        finally:
            self.after(400, self._tick)

    # ---------- pantalla «Reproduciendo» (la canción en grande) ----------
    @property
    def now_open(self) -> bool:
        return self.now_view is not None and self.now_view.visible

    def toggle_now_view(self) -> None:
        if self.now_open:
            self.close_now_view()
        else:
            self.open_now_view()

    def open_now_view(self) -> None:
        """Portada, álbum, letra y cola de lo que suena, sobre la portada desenfocada (con la
        radio, el logo de la emisora y las emisoras de su lista)."""
        if self.now_open:
            return
        if self.now_view is None:
            from .now_view import NowView
            self.now_view = NowView(self, dict(
                close=self.close_now_view, fullscreen=self.toggle_fullscreen,
                like=self.like_current, artist=self._now_open_artist,
                album=self._now_open_album, play_album=self._now_play_album,
                queue=self._play_queue_entry, seek=self._seek_ms, tab=self._now_tab_changed,
                station=self.play_station))
            self.now_view.tab = self._now_tab if self._now_tab in ("album", "lyrics", "queue") \
                else "lyrics"
            self.now_view.set_fullscreen(self.fullscreen)
        if self.queue_win is not None:  # el panel lateral quedaría debajo
            self._show_queue()
        self.tree_tip.hide()
        view = self.now_view
        self._now_track()
        img, seed = getattr(self, "_cover_img", (None, None))
        view.set_art(img, seed, getattr(self, "_cover_color", None))
        self._now_album()
        self._now_queue()
        self._now_stations()
        if view.tab == "lyrics" and not self.radio_on:  # antes de abrir: sin avisos que no tocan
            self._load_lyrics(self.cur_track)
        view.open()
        self.btn_stage.set_on(True)

    def close_now_view(self) -> None:
        if not self.now_open:
            return
        if self.fullscreen:
            self._set_fullscreen(False)
        self.now_view.close()
        self.btn_stage.set_on(False)
        style_titlebar(self, self.dark, self.scheme.get("top"))
        target = self.active_page if self.active_page is not None else (
            self.grid if self.view_mode == "grid" else self.tree)
        target.focus_set()

    def now_caption(self, color: str) -> None:
        """La barra de título de Windows toma el color de arriba de la vista en grande."""
        if self.now_open:
            style_titlebar(self, self.dark, color)

    def toggle_fullscreen(self, _e=None) -> str:
        """F11: la vista en grande ocupa toda la pantalla (Esc o F11 para salir)."""
        self._set_fullscreen(not self.fullscreen)
        if self.fullscreen and not self.now_open:
            self.open_now_view()
        return "break"

    def _set_fullscreen(self, on: bool) -> None:
        try:
            self.attributes("-fullscreen", on)
            self.fullscreen = on
        except tk.TclError:
            self.fullscreen = False
        if self.now_view is not None:
            self.now_view.set_fullscreen(self.fullscreen)

    def _now_track(self) -> None:
        if self.radio_on:
            station = self.radio_station
            self.now_view.set_radio(radio_store.short_name(station), radio_store.subtitle(station),
                                    self.time.cget("text"), station["uuid"] in self.radio_favs,
                                    "Radio en directo")
            return
        t = self.cur_track
        liked = self.now.liked
        self.now_view.set_track(t, liked, self._play_context(), self.now.artist_link,
                                self._now_album_key() is not None)
        self.now_view.set_facts(self._now_facts())

    def _now_facts(self) -> list[tuple[str, str]]:
        """«Sobre esta canción» en la vista en grande, para cuando no tiene letra."""
        from .now_view import added_text, plays_text
        t = self.cur_track
        if t is None:
            return []
        base = self.lib_by_id.get(t["video_id"]) or t
        facts = []
        genre = self._genre_of(t)
        if genre:
            facts.append(("Género", genre))
        if base.get("duration"):
            facts.append(("Duración", fmt(base["duration"])))
        try:
            facts.append(("Escuchada", plays_text(history.play_counts().get(t["video_id"], 0))))
        except Exception as exc:  # noqa: BLE001 - sin historial, un dato menos
            _log.failure(log, "no se pudieron contar las escuchas", exc)
        if t["video_id"] in self.lib_by_id:
            added = added_text(base.get("added_at"))
            if added:
                facts.append(("En tu biblioteca", f"desde el {added}"))
        else:
            facts.append(("Suena desde", "YouTube, sin descargar"))
        return facts

    def _now_stations(self) -> None:
        """Con la radio, las emisoras de la lista desde la que se eligió (las de ⏮ ⏭)."""
        if self.now_view is None or not self.radio_on:
            return
        here = self.radio_station["uuid"]
        stations = self.radio_list or self._radio_favorites()
        if here not in {s["uuid"] for s in stations}:
            stations = [self.radio_station] + list(stations)
        self.now_view.set_stations([
            dict(station=s, title=radio_store.short_name(s), sub=radio_store.subtitle(s),
                 current=s["uuid"] == here) for s in stations])

    def _play_context(self) -> str:
        """De dónde viene lo que suena, para la cabecera de la vista en grande."""
        if self.cur_track is None:
            return ""
        if self.pq.auto_ctx:
            if self.station_now is not None:  # una estación de Sound: su nombre
                return f"{self.station_now['title']} · sin fin"
            seed = self.radio_seed
            return f"Cola automática · similares a «{seed['title']}»" if seed else "Cola automática"
        return self._play_from

    def _now_album_key(self) -> tuple | None:
        t = self.cur_track
        if t is None:
            return None
        return album_key(self.lib_by_id.get(t["video_id"]) or t)

    def _now_album(self) -> None:
        """Las canciones del álbum de lo que suena que tienes, en orden de pista."""
        view, t = self.now_view, self.cur_track
        if view is None:
            return
        if t is None:
            view.set_album([], None, "Cuando suene algo, aquí verás su álbum.")
            return
        base = self.lib_by_id.get(t["video_id"]) or t
        key = album_key(base)
        if key is None:
            if not self._is_remote(t):
                note = "Esta canción no indica a qué álbum pertenece."
                action = None
            else:
                note = ("Suena desde YouTube. Descárgala para tenerla en tu biblioteca junto al "
                        "resto de su álbum.")
                action = ("↓ Descargar", lambda: self.download_tracks([t]))
            view.set_album([], None, note, action, current=t["video_id"])
            return
        view_key = {"kind": "album", "key": key, "title": base.get("album") or "",
                    "artist": (base.get("artist") or "").split(",")[0].strip()}
        rows = self._sub_view_rows(view_key)
        info = dict(title=base.get("album") or "Álbum", artist=view_key["artist"],
                    year=base.get("year"), count=len(rows))
        note = "Es la única canción de este álbum en tu biblioteca." if len(rows) == 1 else ""
        view.set_album(rows, info, note, current=t["video_id"])
        self._want_track_numbers(view_key)

    def _now_queue(self) -> None:
        if self.now_view is None:
            return
        from .now_view import QUEUE_LIMIT
        pq = self.pq
        entries: list[dict] = []
        cur = pq.current(self.playing_vid)
        if cur:
            entries += [dict(kind="hdr", text="Sonando ahora"), dict(kind="cur", idx=0, track=cur)]
        if pq.up_next:
            entries.append(dict(kind="hdr", text=f"Añadidas por ti · {len(pq.up_next)}"))
            entries += [dict(kind="up", idx=i, track=t) for i, t in enumerate(pq.up_next)]
        upcoming = list(range(pq.opos + 1, len(pq.order)))
        if upcoming:
            entries.append(dict(kind="hdr", text="Siguen automáticamente" if pq.auto_ctx
                                else "Siguen en la lista"))
            for j in upcoming[:QUEUE_LIMIT]:
                t = pq.ctx[pq.order[j]]
                remote = self._is_remote(t)
                entries.append(dict(kind="ctx", idx=j, track=t, remote=remote,
                                    offline=remote and not self.online,
                                    why=self.queue_why.get(t["video_id"]) if pq.auto_ctx
                                    else None))
            if len(upcoming) > QUEUE_LIMIT:
                entries.append(dict(kind="more", text=f"y {len(upcoming) - QUEUE_LIMIT} más"))
        self.now_view.set_queue(entries)

    def _now_tab_changed(self, tab: str) -> None:
        self._now_tab = tab
        if tab == "lyrics":
            self._load_lyrics(self.cur_track)
        elif tab == "queue":
            self._now_queue()
        self.save_prefs()

    def _now_open_artist(self) -> None:
        if self.cur_track:
            self.open_artist(self.cur_track.get("artist") or "")

    def _now_open_album(self) -> None:
        t = self.cur_track
        if t is not None:
            self.open_album(self.lib_by_id.get(t["video_id"]) or t)

    def _now_play_album(self, i: int) -> None:
        rows = self.now_view.album_rows if self.now_view is not None else []
        if 0 <= i < len(rows):
            self.play_tracks(rows, i)

    def _seek_ms(self, ms: int) -> None:
        """Clic en una línea de la letra: la canción salta a ese momento."""
        if self.player.loaded and self.player.length:
            ms = max(0, min(int(ms), self.player.length - 500))
            self.player.seek(ms)
            self._clock_ref = (ms, time.monotonic())
            self.scale.set(ms / self.player.length * 1000)

    # ---------- ventana recordada ----------
    def _restore_window(self) -> None:
        saved = self.prefs.get("window") or {}
        geometry = window_state.parse(saved.get("geometry")) if isinstance(saved, dict) else None
        if geometry:
            k = self.k
            areas = window_state.work_areas() or [
                (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())]
            # el tamaño guardado es el del contenido: se deja sitio al marco y a la barra de título
            frame_w, frame_h = int(16 * k), int(40 * k)
            areas = [(l_, t_, r_ - frame_w, b_ - frame_h) for l_, t_, r_, b_ in areas]
            w, h, x, y = window_state.fit(geometry, areas, int(880 * k), int(560 * k))
            self.geometry(f"{w}x{h}+{x}+{y}")
            self._normal_geometry = f"{w}x{h}+{x}+{y}"
        if isinstance(saved, dict) and saved.get("zoomed"):
            self._zoomed = True
            try:  # «zoomed» es de Windows; en X11 se pide con el atributo
                if sys.platform == "win32":
                    self.state("zoomed")
                else:
                    self.attributes("-zoomed", True)
            except tk.TclError:  # el gestor de ventanas no lo soporta
                self._zoomed = False

    def _window_moved(self, e) -> None:
        """Se apunta la geometría normal (la de antes de maximizar) para guardarla al cerrar."""
        if e.widget is not self or self.fullscreen:
            return
        state = self.state()
        if state == "normal":
            self._zoomed = False
            self._normal_geometry = self.wm_geometry()
        elif state == "zoomed":
            self._zoomed = True

    # ---------- mini reproductor y barra de tareas ----------
    def _sync_outside(self) -> None:
        """Lleva el estado al mini reproductor y a los botones de la barra de tareas."""
        playing = self.btn_play.playing
        for buttons in (self.taskbar, self.mini_taskbar):
            if buttons is not None:
                buttons.set_playing(playing)
        if self.mini_on and self.mini is not None:
            self.mini.sync()
        self._sync_system_media()
        if self.now_open and self.radio_on:  # «En directo · 12:34» bajo el nombre
            self.now_view.set_live(self.time.cget("text"))
            self.now_view.sync(0, playing)
        elif self.now_open:  # la línea de la letra y las barras de lo que suena
            try:
                position = self.player.position() if self.player.loaded else self.resume_position
            except Exception:  # noqa: BLE001 - la vista en grande no debe parar el tick
                position = 0
            self.now_view.sync(int(position or 0), playing)

    def toggle_mini(self) -> None:
        if self.mini_on:
            self.exit_mini()
        else:
            self.enter_mini()

    def enter_mini(self) -> None:
        """La ventana se esconde y queda el mini reproductor, siempre encima."""
        from .mini_player import HEIGHT, WIDTH, MiniPlayer
        first = self.mini is None
        if first:
            self.mini = MiniPlayer(self)
            img, seed = getattr(self, "_cover_img", (None, None))
            self.mini.show_cover(img, seed)
        self.mini_on = True
        x, y = self._mini_spot(int(WIDTH * self.k), int(HEIGHT * self.k))
        self.mini.geometry(f"+{x}+{y}")
        self.mini.sync()
        self.withdraw()
        self.mini.deiconify()
        self.mini.lift()
        self.mini.focus_force()
        if first:
            self.after(800, self._setup_mini_taskbar)

    def exit_mini(self) -> None:
        if self.mini is None or not self.mini_on:
            return
        self.mini_on = False
        self._remember_mini_pos()
        self.mini.withdraw()
        self.deiconify()
        self.lift()
        self.focus_force()
        self.save_prefs()

    def _remember_mini_pos(self) -> None:
        # la posición de «wm geometry» es la del marco, la misma que se usa al colocarla:
        # winfo_x/y darían la del contenido y la ventana se correría un poco cada vez
        found = re.search(r"\+(-?\d+)\+(-?\d+)$", self.mini.geometry())
        if found:
            self._mini_pos = [int(found.group(1)), int(found.group(2))]

    def _mini_spot(self, w: int, h: int) -> tuple[int, int]:
        """Donde se dejó la última vez, si sigue en pantalla; si no, abajo a la derecha."""
        right, bottom = self.winfo_screenwidth(), self.winfo_screenheight()
        try:  # el área de trabajo: la pantalla sin la barra de tareas
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0):
                right, bottom = area.right, area.bottom
        except Exception:  # noqa: BLE001 - fuera de Windows: la pantalla entera
            pass
        # w y h son del contenido: se deja sitio para el marco y la barra de título
        frame_w, frame_h = w + int(16 * self.k), h + int(48 * self.k)
        pos = self._mini_pos
        if (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)
                and 0 <= pos[0] <= right - 40 and 0 <= pos[1] <= bottom - 40):
            # entera a la vista aunque la pantalla o la ventana hayan cambiado de tamaño
            return min(pos[0], right - frame_w), min(pos[1], bottom - frame_h)
        margin = int(16 * self.k)
        return right - frame_w - margin, bottom - frame_h - margin

    def _setup_system_media(self) -> None:
        """Teclas ⏯ ⏭ ⏮ (teclado, auriculares) y el recuadro multimedia de Windows."""
        if sys.platform != "win32":
            return  # sin SMTC en Linux; la ventana sigue igual
        from .media_keys import SystemMedia
        self.system_media = SystemMedia.create(
            on_button=lambda name: self.q.put(("media_key", name)),
            on_seek=lambda ms: self.q.put(("media_seek", ms)))
        if self.system_media is not None and self.cur_track is not None:
            self._system_media_track(self.cur_track, self._cover_data)

    def _system_media_track(self, t: dict, cover: bytes | None = None) -> None:
        if self.system_media is not None:
            try:
                self.system_media.set_track(t.get("title") or "", t.get("artist") or "",
                                            t.get("album") or "", cover)
            except Exception as exc:  # noqa: BLE001 - el panel de Windows es un extra
                _log.failure(log, "no se pudo actualizar el panel de Windows", exc)

    def _sync_system_media(self) -> None:
        """Estado y progreso en el panel de Windows (el progreso, cada ~2 s)."""
        sm = self.system_media
        if sm is None:
            return
        try:
            if self.player.loaded:
                sm.set_playing(not self.player.paused)
                self._timeline_tick = (self._timeline_tick + 1) % 5
                if self._timeline_tick == 0 and self.player.length and not self.radio_on:
                    sm.set_timeline(self.player.position(), self.player.length)
            else:
                sm.set_playing(False if self.cur_track is not None else None)
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudo actualizar el panel de Windows", exc)
            self.system_media = None  # no insistir en cada tick

    def _on_media_key(self, name: str) -> None:
        """Una tecla multimedia (o un botón del panel de Windows)."""
        playing = self.player.loaded and not self.player.paused
        if name == "next":
            self.step(1)
        elif name == "previous":
            self.step(-1)
        elif name == "play" and not playing:
            self.play_pause()
        elif name in ("pause", "stop") and playing:
            self.play_pause()

    def _setup_taskbar(self) -> None:
        if sys.platform != "win32":  # la miniatura con botones es una API de Windows
            return
        from .taskbar import TaskbarButtons
        self.taskbar = TaskbarButtons(int(self.wm_frame(), 16), self._thumb_clicked)
        self.taskbar.install()

    def _setup_mini_taskbar(self) -> None:
        if sys.platform != "win32":
            return
        from .taskbar import TaskbarButtons
        if self.mini is not None:
            self.mini_taskbar = TaskbarButtons(int(self.mini.wm_frame(), 16), self._thumb_clicked)
            self.mini_taskbar.install()

    def _thumb_clicked(self, button: int) -> None:
        """Llega desde el procedimiento de ventana: se pasa a la cola, no se toca Tk aquí."""
        self.q.put(("thumb_button", button))

    def _on_thumb_button(self, button: int) -> None:
        from .taskbar import NEXT, PLAY_PAUSE, PREV
        if button == PREV:
            self.step(-1)
        elif button == PLAY_PAUSE:
            self.play_pause()
        elif button == NEXT:
            self.step(1)
        else:  # el icono de la barra de tareas se creó de nuevo: se vuelven a añadir
            for buttons in (self.taskbar, self.mini_taskbar):
                if buttons is not None and not buttons.added:
                    self.after(300, buttons.install)

    def _tick_once(self) -> None:
        now = time.monotonic()
        delta, self._tick_at = now - self._tick_at, now
        if self._radio_tick():  # una emisora: ni progreso, ni historial, ni siguiente canción
            return
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
                        elif self._impossible_end(actual):
                            self._halt_impossible()
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
        station = self.radio_on and self.cur_track is None
        if data:
            try:  # un logo de emisora conserva su transparencia: va sobre un color
                img = Image.open(io.BytesIO(data)).convert("RGBA" if station else "RGB")
            except Exception as exc:  # noqa: BLE001 - imagen corrupta: se usa el degradado
                _log.failure(log, "portada ilegible", exc)
        seed = self.cur_track["video_id"] if self.cur_track else (
            f"radio-{self.radio_station['uuid']}" if self.radio_on else None)
        color = (dominant_color(img) if img else None) or (seed_color(seed) if seed else None)
        if station:  # el logo (pequeño y casi nunca cuadrado) centrado sobre su color
            img = station_poster(img, color)
        self.cover.show(img, seed)
        self._cover_img = (img, seed)
        if self.mini is not None:
            self.mini.show_cover(img, seed)
        self._cover_color = color
        if self.cur_track is not None:
            self._system_media_track(self.cur_track, data)
        elif self.radio_on:
            self._system_media_track(self._radio_media(), data)
        if self.now_view is not None:
            self.now_view.set_art(img, seed, color)
        self._set_ambient(color)

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
        return {"dark": self.dark, "theme": self.theme_mode,
                "import_as_playlist": self.import_as_playlist,
                "discover": self.discover_on,
                "side_folded": sorted(self._side_folded), "vol": volume, "shuffle": self.pq.shuffle,
                "repeat": self.pq.repeat, "auto": self.autoplay, "xfade": self.xfade_ms,
                "normalize": self.normalize, "cookies": self.cookies,
                "reduce_motion": self.anim.reduced, "auto_update": self.auto_update,
                "view": self.view_pref, "lyrics": self.lyrics_enabled,
                "dupes_seen": self._dupes_seen, "mini_pos": self._mini_pos,
                "now_tab": self._now_tab,
                "window": {"geometry": self._normal_geometry, "zoomed": self._zoomed},
                "ytdlp_checked_at": self._ytdlp_checked_at, **self._app_update_prefs()}

    def _close(self) -> None:
        self.toast.flush()  # un borrado a la espera de «Deshacer» se confirma al cerrar
        if self.system_media is not None:
            self.system_media.close()
        if self.mini is not None and self.mini_on:
            self._remember_mini_pos()
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
