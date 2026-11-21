"""Página «Radio»: emisoras en directo del directorio libre Radio Browser y estaciones de Sound.

Arriba, un buscador (nombre, ciudad o género) y unas listas a un clic: «Para ti» (las estaciones
de Sound, radios sin fin hechas con lo que escuchas), tus favoritas, las de tu país (según la
región de Windows), las más escuchadas del mundo y algunos géneros. Debajo, las emisoras con su
logo: un clic la pone a sonar y el corazón la guarda en favoritas. La que suena dice «Sonando
ahora». El modo radio del reproductor está en radio.py; las estaciones, en stations.py.
"""
from __future__ import annotations

import io
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from PIL import Image

from ..core import covers, radio
from ..core import log as _log
from .page import Page
from .radio import art_track
from .theme import FONT_FAMILY, blend, seed_color
from .welcome import SuggestionTile, _disc, fit_count
from .widgets import ChipBar, Field, bind_touchpad, touchpad_deltas

log = _log.get("ui")
TAGS = (("news", "Noticias"), ("pop", "Pop"), ("rock", "Rock"), ("salsa", "Salsa"),
        ("vallenato", "Vallenato"), ("reggaeton", "Reggaetón"), ("classical", "Clásica"),
        ("jazz", "Jazz"))
TILE_MIN, TILE_MAX, TILE_GAP = 132, 176, 16
LOGO_WORKERS = 6  # logos a la vez: muchos son lentos o no existen
CREDIT = ("Emisoras del directorio libre Radio Browser (radio-browser.info). Cada una emite "
          "desde su propio servidor; Sound no las graba ni las descarga.")


def tile_columns(width: int, k: float) -> tuple[int, int]:
    """(columnas, lado de cada logo) para `width` píxeles."""
    gap = int(TILE_GAP * k)
    cols = max(1, (width + gap) // (int(TILE_MIN * k) + gap))
    side = min(int(TILE_MAX * k), (width - gap * (cols - 1)) // cols)
    return cols, max(1, side)


def list_status(key: str, count: int, label: str) -> str:
    """Lo que dice la página bajo las listas, según qué se ve y cuántas hay."""
    if key == "mixes":
        return ("Radios sin fin hechas con lo que escuchas: empiezan por una canción tuya y "
                "siguen con parecidas de YouTube Music")
    if key == "fav":
        if not count:
            return "Aún no tienes emisoras favoritas: márcalas con el corazón"
        return f"{count} {'emisora favorita' if count == 1 else 'emisoras favoritas'}"
    if not count:
        return f"No se encontraron emisoras para «{label}»" if key == "search" \
            else "No hay emisoras en esta lista ahora mismo"
    what = "emisora" if count == 1 else "emisoras"
    if key == "search":
        return f"{count} {what} para «{label}»"
    if key == "world":
        return f"Las {count} {what} más escuchadas del mundo"
    return f"{count} {what} · {label}"


class StationTile(SuggestionTile):
    """Una emisora: su logo sobre un color propio, el nombre y su ciudad o géneros. El corazón
    de la esquina la marca como favorita."""

    def __init__(self, master, anim, k: float, fonts, on_play, on_corner) -> None:
        super().__init__(master, anim, k, fonts, on_play, on_corner)
        self.station: dict | None = None
        self.favorite = False
        self.playing = False

    def set_station(self, station: dict, image, favorite: bool, playing: bool) -> None:
        changed = (favorite, playing) != (self.favorite, self.playing)
        self.station, self.favorite, self.playing = station, favorite, playing
        track = self.track if self.track and self.track.get("uuid") == station["uuid"] else \
            dict(art_track(station), title=station["name"], uuid=station["uuid"])
        before = (self.track, self.image)
        self.set_track(track, image, False)
        if changed and before == (self.track, self.image):
            self._render()

    def corner_kind(self) -> str:
        return "heart_on" if self.favorite else "heart"

    def corner_active(self) -> bool:
        return True

    def subtitle(self) -> tuple[str, str]:
        if self.playing:
            return "Sonando ahora", "accent"
        station = self.station or {}
        detail = radio.subtitle(station) or " ".join(
            x for x in (station.get("codec", ""), f"{station.get('bitrate')} kbps"
                        if station.get("bitrate") else "") if x)
        return detail, "dim"

    def placeholder_icon(self) -> str:
        return "radio"

    def art(self, side: int) -> Image.Image:
        """El logo (casi siempre pequeño y no cuadrado) centrado sobre un color de la emisora."""
        c = self.c
        if self.image is None:
            return self.placeholder(side)
        if self._scaled is None or self._scaled[0] != side:
            seed = (self.track or {}).get("video_id") or ""
            back = Image.new("RGBA", (side, side), blend(c["bg"], seed_color(seed), 0.18))
            logo = self.image.convert("RGBA")
            box = int(side * 0.66)
            scale = box / max(logo.width, logo.height)
            logo = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
                               Image.LANCZOS)
            back.alpha_composite(logo, ((side - logo.width) // 2, (side - logo.height) // 2))
            self._scaled = (side, back)
        return self._scaled[1].copy()


class MixTile(SuggestionTile):
    """Una estación de Sound: la portada de la canción con la que empieza, con una marca de
    radio arriba a la izquierda. Sin botón en la esquina."""

    def __init__(self, master, anim, k: float, fonts, on_play, on_corner) -> None:
        super().__init__(master, anim, k, fonts, on_play, on_corner)
        self.item: dict | None = None
        self.playing = False

    def set_item(self, item: dict, image, playing: bool) -> None:
        changed = playing != self.playing
        self.item, self.playing = item, playing
        seed = item.get("seed") or {}
        track = self.track if self.track and self.track.get("mix_id") == item["id"] else {
            "video_id": seed.get("video_id") or item["id"], "title": item["title"],
            "mix_id": item["id"], "cover": seed.get("cover"), "path": seed.get("path")}
        before = (self.track, self.image)
        self.set_track(track, image, False)
        if changed and before == (self.track, self.image):
            self._render()

    def corner_kind(self) -> str | None:
        return None

    def subtitle(self) -> tuple[str, str]:
        if self.playing:
            return "Sonando ahora", "accent"
        return (self.item or {}).get("subtitle") or "", "dim"

    def placeholder_icon(self) -> str:
        return "radio"

    def art(self, side: int) -> Image.Image:
        art = super().art(side)
        d, pad = int(26 * self.k), int(8 * self.k)
        badge = _disc(d, "#202020", "#ffffff", "radio")
        badge.putalpha(badge.getchannel("A").point(lambda a: int(a * 0.88)))
        art.alpha_composite(badge, (pad, pad))
        return art


class RadioPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Radio", status="Emisoras en directo de todo el mundo")
        k, f = self.k, self.fonts
        self.country = radio.country_code()
        self.country_label = radio.country_name(self.country) if self.country else ""
        self.key: str | None = None
        self.label = ""
        self.stations: list[dict] = []  # emisoras o, en «Para ti», estaciones de Sound
        self.tiles: list[StationTile | MixTile] = []
        self._mode = "live"  # «live» (emisoras) o «mixes» (estaciones de Sound)
        self._covers: dict[str, Image.Image | None] = {}
        self._asked: set[str] = set()
        self._logos = ThreadPoolExecutor(LOGO_WORKERS, thread_name_prefix="logos")
        self._token = 0
        self._width = 0
        self._cols = 0
        self._cover_retry = None
        self._colors: dict = {}

        b = self.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(3, weight=1)
        self.var_query = tk.StringVar()
        self.field = Field(b, app.anim, k, (FONT_FAMILY, 11), self.var_query,
                           hint="Busca una emisora, una ciudad o un género…", height=40)
        self.field.grid(row=0, column=0, sticky="ew", pady=(int(4 * k), int(10 * k)))
        self.field.entry.bind("<Return>", lambda e: self._search())
        self.field.entry.bind("<Escape>", self._clear_search)
        self.chips = ChipBar(b, app.anim, k, f["small"], self._pick, idle=0.06)
        self.chips.grid(row=1, column=0, sticky="w")
        self.info = tk.Label(b, anchor="w", font=f["small"])
        self.info.grid(row=2, column=0, sticky="ew", pady=(int(10 * k), int(8 * k)))

        area = tk.Frame(b)
        area.grid(row=3, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.area = area
        self.outer = tk.Canvas(area, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(area, orient="vertical", command=self.outer.yview)
        self.outer.configure(yscrollcommand=self.scroll.set)
        self.outer.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.inner = tk.Frame(self.outer)
        self._window = self.outer.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.outer.configure(
            scrollregion=self.outer.bbox("all")))
        self.outer.bind("<Configure>", self._resized)
        for widget in (self.outer, self.inner, self.info):
            widget.bind("<MouseWheel>", self._wheel)
            bind_touchpad(widget, self._touchpad)
        self.credit = tk.Label(b, anchor="w", justify="left", font=f["small"], text=CREDIT)
        self.credit.grid(row=4, column=0, sticky="ew", pady=(int(10 * k), 0))

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        if self.key is None:  # la primera vez: tus favoritas, si hay; si no, las de tu país
            first = "fav" if self.app.radio_favs else ("country" if self.country else "world")
            self._choose(first)
        else:
            self.refresh_state()

    def _list_options(self) -> list[tuple[str, str]]:
        options = [("mixes", "Para ti")]
        if self.app.radio_favs or self.key == "fav":
            options.append(("fav", "Favoritas"))
        if self.country:
            options.append(("country", self.country_label))
        options.append(("world", "Más escuchadas"))
        options += [(f"tag:{tag}", label) for tag, label in TAGS]
        return options

    def _sync_chips(self) -> None:
        options = self._list_options()
        room = max(1, self.chips.master.winfo_width() or self._width)
        count = fit_count([self.chips.chip_width(label) for _k, label in options], room)
        self.chips.set_options(options[:max(1, count)], selected=self.key)

    # ---------- elegir qué se ve ----------
    def _pick(self, key) -> None:
        if key and key != self.key:
            self._choose(key)

    def _choose(self, key: str) -> None:
        self.var_query.set("")
        label = dict(self._list_options()).get(key, "")
        self._load(key, label)

    def _search(self) -> None:
        text = self.var_query.get().strip()
        if text:
            self._load("search", text)

    def _clear_search(self, _e=None) -> str | None:
        if not self.var_query.get():
            return None  # Esc sin búsqueda: vuelve atrás, como en cualquier página
        self.var_query.set("")
        if self.key == "search":
            self._choose("fav" if self.app.radio_favs else ("country" if self.country else "world"))
        return "break"

    def _load(self, key: str, label: str) -> None:
        self._token += 1
        token = self._token
        self.key, self.label = key, label
        self._sync_chips()
        self.outer.yview_moveto(0)
        if key == "mixes":
            self._show(self.app._station_suggestions())
            return
        if key == "fav":
            self._show(self.app._radio_favorites())
            return
        if not self.app.online:
            self._show([], text="Sin conexión: la radio necesita internet (tus favoritas sí "
                                "se ven, pero no suenan hasta que vuelva)")
            return
        self.info.configure(text="Buscando emisoras…")

        def work() -> None:
            try:
                if key == "country":
                    stations = radio.by_country(self.country)
                elif key == "world":
                    stations = radio.popular()
                elif key.startswith("tag:"):
                    stations = radio.by_tag(key[4:], self.country)
                else:
                    stations = radio.search(label)
                self.app.q.put(("radio_list", token, stations, None))
            except Exception as exc:  # noqa: BLE001 - sin red, directorio caído…
                _log.failure(log, f"no se pudieron pedir las emisoras ({key})", exc)
                self.app.q.put(("radio_list", token, None, str(exc)))

        threading.Thread(target=work, daemon=True, name="radio").start()

    def loaded(self, token: int, stations: list[dict] | None, error: str | None) -> None:
        """Desde la cola de eventos: llegó una lista (o falló)."""
        if token != self._token:
            return  # ya se pidió otra
        if stations is None:
            self._show([], text="No responde el directorio de emisoras. Prueba en un momento.")
            return
        self._show(stations)

    # ---------- pintar la lista ----------
    def _show(self, stations: list[dict], text: str | None = None) -> None:
        self.stations = list(stations)
        if self.key == "mixes":  # la nota del directorio es de las emisoras, no de «Para ti»
            self.credit.grid_remove()
        else:
            self.credit.grid()
        self.info.configure(text=text or list_status(self.key or "", len(stations), self.label))
        self._layout()

    def _layout(self) -> None:
        if self._width <= 1:
            return
        cols, side = tile_columns(self._width, self.k)
        self._cols = cols
        gap = int(TILE_GAP * self.k)
        mode = "mixes" if self.key == "mixes" else "live"
        if mode != self._mode:  # otro tipo de tarjeta: se rehacen
            while self.tiles:
                self.tiles.pop().destroy()
            self._mode = mode
        if mode == "mixes":
            self._layout_mixes(cols, side, gap)
            return
        while len(self.tiles) < len(self.stations):
            tile = StationTile(self.inner, self.app.anim, self.k, self.fonts, self._play,
                               self._favorite)
            if self._colors:
                tile.restyle(**self._colors)
            tile.bind("<MouseWheel>", self._wheel, add="+")
            bind_touchpad(tile, self._touchpad, add=True)
            self.tiles.append(tile)
        while len(self.tiles) > len(self.stations):
            self.tiles.pop().destroy()
        playing = (self.app.radio_station or {}).get("uuid")
        missing = []
        for i, (tile, station) in enumerate(zip(self.tiles, self.stations)):
            tile.grid(row=i // cols, column=i % cols, sticky="nw",
                      padx=(0 if i % cols == 0 else gap, 0), pady=(0, gap))
            tile.set_side(side)
            vid = f"radio-{station['uuid']}"
            tile.set_station(station, self._covers.get(vid), station["uuid"] in self.app.radio_favs,
                             station["uuid"] == playing)
            if vid not in self._covers and station.get("favicon"):
                missing.append(station)
        if missing:
            self._ask_covers(missing)

    def _layout_mixes(self, cols: int, side: int, gap: int) -> None:
        """«Para ti»: las estaciones de Sound, con la portada de su primera canción."""
        while len(self.tiles) < len(self.stations):
            tile = MixTile(self.inner, self.app.anim, self.k, self.fonts, self._play_mix,
                           lambda _t: None)
            if self._colors:
                tile.restyle(**self._colors)
            tile.bind("<MouseWheel>", self._wheel, add="+")
            bind_touchpad(tile, self._touchpad, add=True)
            self.tiles.append(tile)
        while len(self.tiles) > len(self.stations):
            self.tiles.pop().destroy()
        now = None if self.app.radio_on else (self.app.station_now or {}).get("id")
        missing = []
        for i, (tile, item) in enumerate(zip(self.tiles, self.stations)):
            tile.grid(row=i // cols, column=i % cols, sticky="nw",
                      padx=(0 if i % cols == 0 else gap, 0), pady=(0, gap))
            tile.set_side(side)
            seed = item.get("seed")
            vid = seed["video_id"] if seed else None
            tile.set_item(item, self._covers.get(vid) if vid else None, item["id"] == now)
            if seed is not None and vid not in self._covers:
                missing.append(seed)
        if missing:
            refused = [t for t in missing if not self.app._request_thumb(t)]
            if refused and self._cover_retry is None:
                def retry() -> None:
                    self._cover_retry = None
                    if self._mode == "mixes":
                        self._layout()
                self._cover_retry = self.after(700, retry)

    def _ask_covers(self, stations: list[dict]) -> None:
        """Pide los logos que faltan, en orden (los de arriba antes) y cada uno una sola vez."""
        for station in stations:
            vid = art_track(station)["video_id"]
            if vid not in self._asked:
                self._asked.add(vid)
                self._logos.submit(self._fetch_logo, vid, station.get("favicon"))

    def _fetch_logo(self, vid: str, url: str | None) -> None:
        """En un hilo: caché → descarga; con su transparencia (los logos suelen tenerla)."""
        image = None
        try:
            data = covers.cached(vid)
            fresh = data is None
            if fresh:
                data = radio.fetch_logo(url)
            if data:
                image = Image.open(io.BytesIO(data))
                image.load()
                image = image.convert("RGBA")
                image.thumbnail((256, 256), Image.LANCZOS)
                if fresh:  # solo se guarda lo que de verdad es una imagen
                    covers.store(vid, data)
        except Exception as exc:  # noqa: BLE001 - un logo raro: se dibuja uno propio
            _log.failure(log, f"logo ilegible de la emisora {vid}", exc)
            image = None
        self.app.q.put(("radio_logo", vid, image))

    def set_cover(self, vid: str, image) -> None:
        """Llegó un logo (None si no se pudo: no se vuelve a pedir)."""
        if vid in self._covers and self._covers[vid] is image:
            return
        mine = vid.startswith("radio-") or any(
            tile.track and tile.track.get("video_id") == vid for tile in self.tiles)
        if not mine or (image is None and not vid.startswith("radio-")):
            return  # portadas de la lista principal, o una que no llegó (se reintentará)
        self._covers[vid] = image
        for tile in self.tiles:
            if not (tile.track and tile.track.get("video_id") == vid):
                continue
            if isinstance(tile, StationTile) and tile.station:
                tile.set_station(tile.station, image, tile.favorite, tile.playing)
            elif isinstance(tile, MixTile) and tile.item:
                tile.set_item(tile.item, image, tile.playing)

    def refresh_state(self, favorites: bool = False) -> None:
        """Cambió la emisora que suena o una favorita: corazones, «Sonando ahora» y, si se ven
        las favoritas, la lista."""
        if favorites and self.key == "fav":
            self.stations = self.app._radio_favorites()
            self.info.configure(text=list_status("fav", len(self.stations), self.label))
        self._sync_chips()
        self._layout()

    # ---------- acciones ----------
    def _station_of(self, track: dict) -> dict | None:
        uuid = track.get("uuid")
        return next((s for s in self.stations if s["uuid"] == uuid), None)

    def _play(self, track: dict) -> None:
        station = self._station_of(track)
        if station is not None:
            self.app.play_station(station, self.stations)

    def _play_mix(self, track: dict) -> None:
        item = next((s for s in self.stations if s.get("id") == track.get("mix_id")), None)
        if item is not None:
            self.app.start_station(item)

    def _favorite(self, track: dict) -> None:
        station = self._station_of(track)
        if station is not None:
            self.app.toggle_station_favorite(station)

    # ---------- tamaño y rueda ----------
    def _resized(self, e) -> None:
        self.outer.itemconfigure(self._window, width=max(1, e.width))
        width = max(1, e.width - int(4 * self.k))
        if width != self._width:
            self._width = width
            self._layout()
            self._sync_chips()

    def _wheel(self, e) -> str:
        self.outer.yview_scroll(self.app._wheel_steps(e), "units")
        return "break"

    def _touchpad(self, e) -> str:
        _dx, dy = touchpad_deltas(e)
        if dy:
            self.outer.tk.call("tk::ScrollByPixels", self.outer._w, 0, dy)
        return "break"

    def _restyle_content(self) -> None:
        p = self.app.pal
        bg = p["bg"]
        for widget in (self.area, self.outer, self.inner):
            widget.configure(bg=bg)
        self.info.configure(bg=bg, fg=p["dim"])
        self.credit.configure(bg=bg, fg=p["faint"])
        self.chips.restyle(bg, p["fg"], p["dim"])
        self.field.restyle(bg=bg, field=p["field"], panel=p["panel"], fg=p["fg"], dim=p["dim"],
                           line=p["line"], accent=p["accent"])
        self._colors = dict(bg=bg, fg=p["fg"], dim=p["dim"], faint=p["faint"],
                            accent=p["accent"], on_accent=p["accent_fg"])
        for tile in self.tiles:
            tile.restyle(**self._colors)
