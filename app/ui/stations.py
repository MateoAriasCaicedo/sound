"""Estaciones de Sound en la ventana: «Iniciar radio» y la lista «Para ti» de la página Radio.

Una estación es una cola automática que no se acaba aunque la cola automática esté desactivada
en Ajustes: suena la semilla, luego unas cuantas tuyas parecidas y después lo que YouTube Music
pone en la «radio» de la semilla (sin internet, tu biblioteca). «A continuación» dice de qué
estación viene. Elegir otra cosa para escuchar la termina. Qué estaciones ofrecer: core/stations.py.
"""
from __future__ import annotations

import datetime
import random
import threading

from ..core import favorites, history
from ..core import log as _log
from ..core import stations as stations_core

log = _log.get("ui")


class StationsMixin:
    def _init_stations(self) -> None:
        self.station_now: dict | None = None  # la estación que suena (su descripción)
        self._station_pending: dict[str, dict] = {}  # esperando su semilla de YouTube

    # ---------- empezar ----------
    def start_station(self, item: dict) -> None:
        """Suena la estación: la semilla, sus `extras` y, sin fin, canciones parecidas."""
        if item["seed"] is None:  # un género sin nada tuyo: la semilla viene de YouTube
            self._fetch_station_seed(item)
            return
        seed = item["seed"]
        extras = [t for t in item["extras"] if t["video_id"] != seed["video_id"]]
        self.station_now = item
        self._play_from = item["title"]
        self.pq.ctx, self.pq.auto_ctx = [seed] + extras, True
        self.pq.album = False
        self.pq.make_order(0)
        self._seed(seed)  # pide ya la «radio» de la semilla
        for t in extras:
            self.queue_why[t["video_id"]] = "de tu biblioteca, para empezar"
        self._play_track(seed)
        self.toast.show(f"«{item['title']}»: suena sin fin, con canciones parecidas")
        self._radio_page_changed()

    def _end_station(self) -> None:
        """Otra cosa para escuchar (una lista, un álbum…): la estación termina."""
        if self.station_now is not None:
            self.station_now = None
            self._radio_page_changed()

    def _station_keeps_going(self) -> bool:
        """¿La cola automática sigue? Con una estación, aunque esté desactivada en Ajustes."""
        return (self.autoplay or self.station_now is not None) and self.pq.auto_ctx

    def _fetch_station_seed(self, item: dict) -> None:
        if not self.online:
            self.toast.show(f"«{item['title']}» necesita internet para empezar")
            return
        self._station_pending[item["id"]] = item
        self.status.configure(text=f"Preparando «{item['title']}»…")

        def work() -> None:
            try:
                from ..core import genres
                tracks = genres.select_tracks(item["genre"], 12)[1]
            except Exception as exc:  # noqa: BLE001 - sin red, API cambiada…
                _log.failure(log, f"no se pudo empezar la estación {item['title']}", exc)
                tracks = None
            self.q.put(("station_seed", item["id"], tracks))

        threading.Thread(target=work, daemon=True, name="estacion").start()

    def _station_seed_loaded(self, item_id: str, tracks: list[dict] | None) -> None:
        """Desde la cola de eventos: llegaron las canciones con las que empieza la estación."""
        item = self._station_pending.pop(item_id, None)
        if item is None:
            return
        if not tracks:
            self.toast.show(f"No se pudo empezar «{item['title']}» ahora mismo")
            return
        for t in tracks:
            t["_genre"] = item.get("genre")  # lo que se guarde de ella cae en ese género
        self.start_station(dict(item, seed=tracks[0], extras=tracks[1:1 + stations_core.EXTRAS]))

    # ---------- desde una canción, un artista, una lista… ----------
    def start_track_station(self, track: dict) -> None:
        """«Iniciar radio» en el menú de una canción."""
        genre = self._genre_of(track)
        same = [t for t in self.library if genre and t.get("genre") == genre]
        random.Random(track["video_id"]).shuffle(same)
        self.start_station(stations_core.for_track(track, same[:stations_core.EXTRAS]))

    def _hero_station(self, rows: list[dict]) -> dict | None:
        """La estación de lo que se ve (artista, álbum, lista o Me gusta), para el «⋯»."""
        top = self.sub_view
        day = datetime.date.today().toordinal()
        if top is not None and top["kind"] == "artist":
            try:
                counts = history.play_counts()
            except Exception:  # noqa: BLE001
                counts = {}
            return stations_core.for_artist(top["title"], self.library, counts, day)
        if not rows:
            return None
        rng = random.Random(day)
        seed = rng.choice(rows)
        extras = [t for t in rows if t["video_id"] != seed["video_id"]]
        rng.shuffle(extras)
        extras = extras[:stations_core.EXTRAS]
        if top is not None and top["kind"] == "album":
            return stations_core.station("album", f"Radio de «{top['title']}»",
                                         f"{top.get('artist') or 'El álbum'} y parecidas",
                                         rows[0], [])
        if self.view_kind == "playlist" and self.playlist_id is not None:
            name = self._playlist_name(self.playlist_id) or "esta lista"
            return stations_core.station("playlist", f"Radio de «{name}»",
                                         "Tu lista y parecidas", seed, extras)
        if self.view_kind == "liked":
            return stations_core.station("liked", "Radio de tus Me gusta",
                                         "Lo que marcaste y parecidas", seed, extras)
        return None

    # ---------- «Para ti» ----------
    def _station_suggestions(self) -> list[dict]:
        try:
            top, counts = history.most_played(60), history.play_counts()
            liked = favorites.liked_tracks()
        except Exception as exc:  # noqa: BLE001 - sin historial, lo más general
            _log.failure(log, "no se pudo leer el historial para las estaciones", exc)
            top, counts, liked = [], {}, []
        names = self.yt_genres
        if not names:
            from ..core import genres
            names = genres.sidebar_genres()
        return stations_core.suggest(top, liked, self.library, counts,
                                     datetime.date.today().toordinal(), names)
