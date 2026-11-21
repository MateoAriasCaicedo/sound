"""La radio en la ventana: escuchar una emisora en directo con el mismo reproductor.

Mientras suena una emisora («modo radio»):
- La barra dice su nombre y «En directo» en vez del tiempo; la onda queda plana y no se puede
  adelantar (es en directo).
- ⏮ ⏭ (y las teclas multimedia) pasan a la emisora anterior o siguiente de la lista desde la
  que se eligió. Pausa corta la conexión y play vuelve a conectar, al directo.
- El corazón marca la emisora como favorita.
- Si la emisora se corta o no responde, se reintenta unas veces; después, se para con un aviso.
- No cuenta como escucha en el historial ni toca la cola de canciones.

Al elegir una canción se sale del modo radio. La página con las emisoras es radio_page.py.
"""
from __future__ import annotations

import time

from ..core import log as _log
from ..core import radio as radio_store

log = _log.get("ui")
OPEN_TIMEOUT_S = 15   # sin empezar a sonar en este tiempo, la emisora no responde
RETRIES = 3           # reconexiones antes de rendirse
SWITCH_FADE_MS = 280  # fundido al dejar lo que sonaba
INTRO_FADE_MS = 400   # entrada de la emisora
FLAT = [0.06] * 48    # la «onda» de una emisora: plana, no hay nada que adelantar


def live_text(seconds: float) -> str:
    """«En directo · 12:34» (lo que lleva escuchando)."""
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    clock = f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"
    return f"En directo · {clock}"


def art_track(station: dict) -> dict:
    """La emisora como «canción» para pedir su logo (la caché de portadas va por identificador)."""
    return {"video_id": f"radio-{station['uuid']}", "cover": station.get("favicon")}


class RadioMixin:
    def _init_radio(self) -> None:
        self.radio_station: dict | None = None  # la emisora que suena (o la última, en pausa)
        self.radio_list: list[dict] = []         # de dónde se eligió: ⏮ ⏭ se mueven por aquí
        self._radio_since = 0.0                  # cuándo se pidió conectar
        self._radio_started: float | None = None  # cuándo empezó a sonar
        self._radio_retries = 0
        try:
            self.radio_favs = radio_store.favorite_ids()
        except Exception as exc:  # noqa: BLE001 - sin favoritas, la radio sigue
            _log.failure(log, "no se pudieron leer las emisoras favoritas", exc)
            self.radio_favs = set()

    @property
    def radio_on(self) -> bool:
        return self.radio_station is not None

    # ---------- empezar y dejar ----------
    def play_station(self, station: dict, context: list[dict] | None = None) -> None:
        """Suena una emisora (deja la canción que sonara). `context`: la lista visible."""
        self.play_token += 1  # lo que una canción estuviera abriendo ya no vale
        token = self.play_token
        if self.player.loaded:
            self.player.stop(SWITCH_FADE_MS)
        old, self.playing_vid = self.playing_vid, None
        if old:
            self._retag(old)
        self.cur_track = None
        self.resume_track, self.resume_position = None, 0
        self.stream_t = None
        self.transitioning_since = self.auto_fired = None
        self._sleep_wake()
        if self.sleep_mode == "track":  # «al terminar la canción» no tiene sentido en directo
            self.sleep_mode = None
            self._refresh_sleep_button()
        self.radio_station = station
        if context is not None:
            self.radio_list = list(context)
        self._radio_retries = 0
        name = radio_store.short_name(station)
        sub = radio_store.subtitle(station)
        self.now.set(name, f"En directo · {sub}" if sub else "En directo")
        self._refresh_now_heart()
        self.scale.set_track(f"radio-{station['uuid']}", FLAT)
        self.scale.set(0)
        self.time.configure(text="Conectando…")
        self.btn_play.configure(text="⏸")
        self.status.configure(text=f"Conectando con «{name}»…")
        self._system_media_track(self._radio_media())
        self._load_cover(art_track(station), None, token)
        self._radio_connect()
        self._radio_page_changed()

    def _radio_connect(self) -> None:
        station = self.radio_station
        self._radio_since, self._radio_started = time.monotonic(), None
        try:
            self.player.set_next_gain_db(0)
            self.player.load_url(station["url"], fade_ms=INTRO_FADE_MS)
            self.player.set_volume(self.vol.get())
        except Exception as exc:  # noqa: BLE001 - dirección rara: se avisa como un corte
            _log.failure(log, f"no se pudo abrir la emisora {station['url']}", exc)
            self._radio_give_up(str(exc))

    def _leave_radio(self) -> None:
        """Se eligió una canción: fuera del modo radio (quien llama ya corta el sonido)."""
        if self.radio_station is None:
            return
        self.radio_station = None
        self._radio_started = None
        self._radio_page_changed()

    def _radio_media(self) -> dict:
        station = self.radio_station or {}
        return {"title": radio_store.short_name(station), "artist": "Radio en directo",
                "album": radio_store.subtitle(station)}

    # ---------- botones ----------
    def _radio_toggle(self) -> None:
        """Pausa corta la conexión (no se sigue bajando la emisora); play vuelve al directo."""
        if self.player.loaded and not self.player.paused:
            self.player.stop(SWITCH_FADE_MS)
            self.btn_play.configure(text="▶")
            self.time.configure(text="En pausa")
            self.status.configure(text=f"«{radio_store.short_name(self.radio_station)}» en pausa")
        else:
            self._radio_retries = 0
            self.btn_play.configure(text="⏸")
            self.time.configure(text="Conectando…")
            self._radio_connect()

    def _radio_step(self, d: int) -> None:
        """⏮ ⏭: la emisora anterior o siguiente de la lista desde la que se eligió."""
        stations = self.radio_list or [s for s in self._radio_favorites()]
        if not stations:
            return
        ids = [s["uuid"] for s in stations]
        here = ids.index(self.radio_station["uuid"]) if self.radio_station["uuid"] in ids else -1
        self.play_station(stations[(here + d) % len(stations)])

    # ---------- cada tick ----------
    def _radio_tick(self) -> bool:
        """Desde el tick de reproducción: True si está en modo radio (lo demás es de canciones)."""
        if self.radio_station is None:
            return False
        p = self.player
        if not p.loaded:
            return True  # en pausa o sin señal
        try:
            p.check()
        except RuntimeError as exc:
            self._radio_trouble(str(exc))
            return True
        if p.finished():
            self._radio_trouble("la emisora dejó de emitir")
            return True
        if p.paused:  # p. ej. el temporizador para dormir la apagó
            self.time.configure(text="En pausa")
            return True
        now = time.monotonic()
        if self._radio_started is None:
            if p.position() > 0:
                self._radio_started = now
                self._radio_retries = 0
                self.status.configure(
                    text=f"En directo: «{radio_store.short_name(self.radio_station)}»")
            elif now - self._radio_since > OPEN_TIMEOUT_S:
                self._radio_trouble("no responde")
            return True
        self.time.configure(text=live_text(now - self._radio_started))
        return True

    def _radio_trouble(self, reason: str) -> None:
        if self._radio_retries < RETRIES:
            self._radio_retries += 1
            log.info("emisora cortada (%s): reconexión %d", reason, self._radio_retries)
            self.time.configure(text="Reconectando…")
            self.status.configure(
                text=f"Reconectando con «{radio_store.short_name(self.radio_station)}»… "
                     f"({self._radio_retries} de {RETRIES})")
            self._radio_connect()
            return
        self._radio_give_up(reason)

    def _radio_give_up(self, reason: str) -> None:
        station = self.radio_station
        name = radio_store.short_name(station)
        log.warning("la emisora %s no responde: %s", station.get("url"), reason)
        self.player.stop()
        self.btn_play.configure(text="▶")
        self.time.configure(text="Sin señal")
        self.status.configure(text=f"«{name}» no responde ahora mismo")
        self.toast.show(f"«{name}» no responde ahora mismo", "Reintentar",
                        lambda: self.play_station(station))

    # ---------- favoritas ----------
    def _radio_favorites(self) -> list[dict]:
        try:
            return radio_store.favorites()
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudieron leer las emisoras favoritas", exc)
            return []

    def toggle_station_favorite(self, station: dict) -> None:
        uuid = station["uuid"]
        name = radio_store.short_name(station)
        try:
            if uuid in self.radio_favs:
                radio_store.remove_favorite(uuid)
                self.radio_favs.discard(uuid)
                self.toast.show(f"«{name}» ya no está en tus emisoras favoritas",
                                "Deshacer", lambda: self.toggle_station_favorite(station))
            else:
                radio_store.add_favorite(station)
                self.radio_favs.add(uuid)
                self.toast.show(f"«{name}» añadida a tus emisoras favoritas")
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudo cambiar la emisora favorita", exc)
            self.toast.show("No se pudo guardar la emisora favorita")
            return
        if self.radio_on and self.radio_station["uuid"] == uuid:
            self._refresh_now_heart(pop=True)
        self._radio_page_changed(favorites=True)

    # ---------- la página ----------
    def open_radio(self) -> None:
        self.show_page("radio")

    def _radio_page_changed(self, favorites: bool = False) -> None:
        page = self.pages.get("radio")
        if page is not None:
            page.refresh_state(favorites=favorites)

    def _radio_list_loaded(self, token: int, stations, error) -> None:
        page = self.pages.get("radio")
        if page is not None:
            page.loaded(token, stations, error)

    def _radio_cover(self, vid: str, image) -> None:
        """Desde la cola de eventos: llegó un logo o una portada (puede ser de la página de la
        radio: los logos de las emisoras y las portadas de las estaciones de Sound)."""
        page = self.pages.get("radio")
        if page is not None:
            page.set_cover(vid, image)
