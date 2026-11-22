"""Reproductor local con dos backends: Media Foundation (WinRT) en Windows y GStreamer en Linux.

En Windows lee m4a fragmentados (DASH) que MCI no puede abrir; en Linux hace lo mismo con
`playbin`, que también abre URLs http de streaming. El resto de la ventana usa la misma interfaz
(`_Slot`) en los dos sistemas.

Transiciones: cada canción es un `_Slot` (un reproductor con su volumen propio). Pueden sonar dos
a la vez; `update_fades` (se llama cada ~40 ms) mueve sus volúmenes con curvas de potencia
constante (seno/coseno: sin² + cos² = 1), de modo que un crossfade no baja ni sube el volumen
percibido. El temporizador para dormir usa otra curva, «dim» ((1 - p)²): baja desde el principio,
poco a poco, en vez de aguantar arriba y caer al final. `prepare` abre una canción por adelantado,
de modo que cargarla después con la misma clave empieza a sonar casi al instante (condición para
un paso sin hueco).
"""
import math
import sys
import time
from datetime import timedelta

from .uri import file_uri

MAX_PREPARED = 14
PAUSE_FADE_MS = 180   # fundido al pausar y reanudar: evita el corte seco
MIN_INTRO_MS = 150    # entrada mínima cuando el que sonaba ya había terminado
LINGER_S = 1.0        # máximo que se deja terminar una canción en un paso sin hueco

if sys.platform == "win32":
    from winrt.windows.foundation import Uri
    from winrt.windows.media.core import MediaSource
    from winrt.windows.media.playback import MediaPlayer

    _Gst = None
    _GST_ERROR: Exception | None = None
else:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as _Gst

        _Gst.init(None)
        _GST_ERROR = None
    except (ImportError, ValueError) as exc:  # sin python3-gi/GStreamer la ventana aún abre
        _Gst = None
        _GST_ERROR = exc

GST_MISSING = (
    "El reproductor en Linux necesita GStreamer: instala python3-gi, gstreamer1.0-plugins-base, "
    "gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad y gstreamer1.0-plugins-ugly."
)


def ramp_gain(kind: str, p: float) -> float:
    """Ganancia de un fundido a mitad de camino (`p` de 0 a 1)."""
    if kind == "in":
        return math.sin(p * math.pi / 2)
    if kind == "out":
        return math.cos(p * math.pi / 2)
    return (1 - p) ** 2  # "dim"


def backend_available() -> bool:
    """¿Se puede reproducir audio? WinRT va con Windows; en Linux hace falta GStreamer."""
    if sys.platform == "win32":
        return True
    return _Gst is not None and _Gst.ElementFactory.find("playbin") is not None


class _WinBackend:
    """MediaPlayer de WinRT con la interfaz que usa `_Slot`."""

    def __init__(self, uri: str, slot: "_Slot") -> None:
        self.mp = MediaPlayer()
        self.mp.auto_play = False
        try:  # las teclas multimedia van a la ventana (media_keys), no a cada reproductor
            self.mp.command_manager.is_enabled = False
        except Exception:  # noqa: BLE001 - Windows sin esa API: se queda como estaba
            pass
        self.mp.add_media_ended(lambda *_: setattr(slot, "ended", True))
        # error_message suele venir vacío: guardamos siempre un texto no vacío
        self.mp.add_media_failed(lambda _s, a: setattr(
            slot, "failed", a.error_message or f"error de medios {int(a.error)}"))
        self.mp.source = MediaSource.create_from_uri(Uri(uri))

    def play(self) -> None:
        self.mp.play()

    def pause(self) -> None:
        self.mp.pause()

    def close(self) -> None:
        try:
            self.mp.close()
        except Exception:  # noqa: BLE001 - ya cerrado
            pass

    def poll(self) -> None:
        """En WinRT los eventos llegan solos; no hay nada que consultar."""

    def set_volume(self, value: float) -> None:
        self.mp.volume = value

    @property
    def position(self) -> int:
        return int(self.mp.playback_session.position.total_seconds() * 1000)

    @property
    def duration(self) -> int:
        return int(self.mp.playback_session.natural_duration.total_seconds() * 1000)

    def seek(self, ms: int) -> None:
        self.mp.playback_session.position = timedelta(milliseconds=int(ms))


class _GstBackend:
    """`playbin` de GStreamer con la misma interfaz que el reproductor de WinRT.

    La ventana no corre un bucle de GLib, así que los mensajes del bus (fin de canción y errores)
    se recogen sin bloquear en `poll`, que llaman `position` y `finished` en cada tick.
    """

    def __init__(self, uri: str, slot: "_Slot") -> None:
        if _Gst is None:
            raise RuntimeError(GST_MISSING) from _GST_ERROR
        self._gst = _Gst
        self._slot = slot
        self._playbin = _Gst.ElementFactory.make("playbin", "sound-slot")
        if self._playbin is None:
            raise RuntimeError(GST_MISSING)
        self._bus = self._playbin.get_bus()
        self._playbin.set_property("uri", uri)

    def play(self) -> None:
        self._playbin.set_state(self._gst.State.PLAYING)

    def pause(self) -> None:
        self._playbin.set_state(self._gst.State.PAUSED)

    def close(self) -> None:
        self._playbin.set_state(self._gst.State.NULL)

    def poll(self) -> None:
        if self._bus is None:
            return
        wanted = self._gst.MessageType.EOS | self._gst.MessageType.ERROR
        while True:
            msg = self._bus.timed_pop_filtered(0, wanted)
            if msg is None:
                return
            if msg.type == self._gst.MessageType.EOS:
                self._slot.ended = True
            elif msg.type == self._gst.MessageType.ERROR:
                err, _debug = msg.parse_error()
                self._slot.failed = err.message or "error de medios"

    def set_volume(self, value: float) -> None:
        self._playbin.set_property("volume", value)

    @property
    def position(self) -> int:
        self.poll()
        ok, ns = self._playbin.query_position(self._gst.Format.TIME)
        return int(ns // 1_000_000) if ok else 0

    @property
    def duration(self) -> int:
        self.poll()
        ok, ns = self._playbin.query_duration(self._gst.Format.TIME)
        return int(ns // 1_000_000) if ok else 0

    def seek(self, ms: int) -> None:
        flags = self._gst.SeekFlags.FLUSH | self._gst.SeekFlags.ACCURATE
        self._playbin.seek_simple(self._gst.Format.TIME, flags, int(ms) * 1_000_000)


class _Slot:
    """Un reproductor con su propio estado: los eventos de uno preparado no afectan al actual."""

    def __init__(self, uri: str) -> None:
        self.ended = False
        self.failed: str | None = None
        self.gain = 1.0          # 0..1, lo mueve el fundido; el volumen final es gain * volumen general
        self.track_gain = 1.0    # ganancia fija de esta canción (ReplayGain)
        self.ramp: dict | None = None
        self.linger_until = 0.0  # paso sin hueco: no se cierra antes de esta hora (monotonic)
        self._backend = _make_backend(uri, self)

    def play(self) -> None:
        self._backend.play()

    def pause(self) -> None:
        self._backend.pause()

    def poll(self) -> None:
        self._backend.poll()

    def set_volume(self, value: float) -> None:
        self._backend.set_volume(value)

    @property
    def position(self) -> int:
        return self._backend.position

    @property
    def duration(self) -> int:
        return self._backend.duration

    def seek(self, ms: int) -> None:
        self._backend.seek(ms)

    def start_ramp(self, kind: str, ms: float, after=None) -> None:
        """Fundido de entrada ("in"), salida ("out") o salida lenta ("dim") desde la ganancia
        actual, sin saltos."""
        g = min(1.0, max(0.0, self.gain))
        if kind == "in":
            done = math.asin(g) / (math.pi / 2)
        elif kind == "out":
            done = math.acos(g) / (math.pi / 2)
        else:
            done = 1 - math.sqrt(g)
        ms = max(1.0, ms)
        self.ramp = {"t0": time.monotonic() - done * ms / 1000, "ms": ms, "kind": kind, "after": after}

    def close(self) -> None:
        self.ramp = None
        self._backend.close()


def _make_backend(uri: str, slot: _Slot):
    if sys.platform == "win32":
        return _WinBackend(uri, slot)
    return _GstBackend(uri, slot)


class Player:
    def __init__(self) -> None:
        self._cur: _Slot | None = None
        self._prepared: dict[str, _Slot] = {}
        self._fading: list[_Slot] = []   # canciones que están saliendo (se cierran al terminar el fundido)
        self._volume = 0.8
        self._next_gain = 1.0  # ganancia de la próxima canción (ReplayGain), lineal
        self.loaded = False
        self.paused = False
        self.length = 0  # ms

    # ---------- carga ----------
    def load(self, path: str, key: str | None = None, fade_ms: int = 0, overlap: bool = False) -> None:
        slot = self._take(key)
        self._play(slot or _Slot(file_uri(path)), fade_ms, overlap)

    def load_url(self, url: str, key: str | None = None, fade_ms: int = 0,
                 overlap: bool = False) -> None:
        """Reproduce una URL; si `key` fue preparada antes, reutiliza esa conexión ya abierta."""
        slot = self._take(key)
        self._play(slot or _Slot(url), fade_ms, overlap)

    def _take(self, key: str | None) -> _Slot | None:
        slot = self._prepared.pop(key, None) if key else None
        if slot is not None and slot.failed:
            slot.close()
            return None
        return slot

    def prepare(self, key: str, url: str) -> None:
        self._prepare(key, url)

    def prepare_path(self, key: str, path: str) -> None:
        self._prepare(key, file_uri(path))

    def _prepare(self, key: str, uri: str) -> None:
        if key in self._prepared:
            return
        while len(self._prepared) >= MAX_PREPARED:
            self._prepared.pop(next(iter(self._prepared))).close()  # la más antigua
        self._prepared[key] = _Slot(uri)

    def is_prepared(self, key: str) -> bool:
        return key in self._prepared

    def forget(self, key: str) -> None:
        """Descarta la versión preparada de `key` (su archivo cambió en disco)."""
        slot = self._prepared.pop(key, None)
        if slot is not None:
            slot.close()

    def _play(self, slot: _Slot, fade_ms: int = 0, overlap: bool = False) -> None:
        """Empieza `slot`. Con `fade_ms` entra con fundido y la actual sale a la vez (crossfade).
        Con `overlap` la actual no se corta: termina sola mientras arranca la nueva (gapless)."""
        old = self._cur
        if old is not None:
            if fade_ms > 0 and not old.ended and not self.paused:
                old.start_ramp("out", fade_ms)
                self._fading.append(old)
            elif overlap and not old.ended and not self.paused:
                old.linger_until = time.monotonic() + LINGER_S
                self._fading.append(old)
            else:
                old.close()
                if old.ended or self.paused:
                    fade_ms = min(fade_ms, MIN_INTRO_MS)  # no hay nada con lo que mezclar
        self._cur = slot
        self.loaded, self.paused, self.length = True, False, 0
        slot.track_gain, self._next_gain = self._next_gain, 1.0
        slot.gain = 0.0 if fade_ms > 0 else 1.0
        self._apply(slot)  # antes de sonar: evita un golpe a volumen máximo
        slot.play()
        if fade_ms > 0:
            slot.start_ramp("in", fade_ms)

    # ---------- fundidos ----------
    def _apply(self, slot: _Slot) -> None:
        slot.set_volume(max(0.0, min(1.0, self._volume * slot.gain * slot.track_gain)))

    def update_fades(self) -> None:
        """Avanza los fundidos en curso. Barato si no hay ninguno: se llama cada ~40 ms."""
        if not self._fading and not (self._cur and self._cur.ramp):
            return
        now = time.monotonic()
        for slot in [s for s in self._fading if not s.ramp and s.ended]:
            slot.close()  # terminó sola (paso sin hueco)
            self._fading.remove(slot)
        for slot in [s for s in self._fading if not s.ramp and now > s.linger_until]:
            slot.close()  # por si el evento de fin no llegó
            self._fading.remove(slot)
        for slot in ([self._cur] if self._cur else []) + self._fading:
            ramp = slot.ramp
            if not ramp:
                continue
            p = min(1.0, (now - ramp["t0"]) * 1000 / ramp["ms"])
            slot.gain = ramp_gain(ramp["kind"], p)
            self._apply(slot)
            if p >= 1.0:
                slot.ramp = None
                if ramp["after"]:
                    ramp["after"]()
        for slot in [s for s in self._fading if s.ramp is None and now > s.linger_until]:
            slot.close()  # el fundido de salida terminó
            self._fading.remove(slot)

    def fading(self) -> bool:
        return bool(self._fading) or bool(self._cur and self._cur.ramp)

    # ---------- estado ----------
    def _refresh_length(self) -> None:
        if not self.length and self._cur:
            length = self._cur.duration
            if length > 0:
                self.length = length

    def check(self) -> None:
        """Lanza RuntimeError si el archivo no pudo abrirse."""
        if self._cur:
            self._cur.poll()
            if self._cur.failed:
                raise RuntimeError(self._cur.failed)

    def toggle(self, fade_ms: int = PAUSE_FADE_MS) -> None:
        """Pausa o reanuda con un fundido corto."""
        slot = self._cur
        if slot is None:
            return
        if self.paused:
            self.paused = False
            slot.play()
            if fade_ms > 0:
                slot.start_ramp("in", fade_ms)
        else:
            self.paused = True
            if fade_ms > 0:
                slot.start_ramp("out", fade_ms, after=slot.pause)
            else:
                slot.pause()

    def fade_to_pause(self, ms: int) -> None:
        """Baja poco a poco hasta el silencio y pausa (temporizador para dormir). Se cuenta como
        pausada desde ya: reanudar a mitad vuelve a subir desde donde iba."""
        slot = self._cur
        if slot is None or self.paused:
            return
        self.paused = True
        slot.start_ramp("dim", ms, after=slot.pause)

    def seek(self, ms: int) -> None:
        if self.loaded:
            self._cur.seek(ms)

    def position(self) -> int:
        if not self.loaded or self._cur is None:
            return 0
        self._refresh_length()
        return self._cur.position

    def finished(self) -> bool:
        if self._cur is not None:
            self._cur.poll()
        return bool(self.loaded and self._cur is not None and self._cur.ended)

    def set_volume(self, pct: float) -> None:
        self._volume = max(0.0, min(1.0, float(pct) / 100))
        for slot in ([self._cur] if self._cur else []) + self._fading:
            self._apply(slot)

    def set_gain_db(self, db: float) -> None:
        """Ganancia fija de la canción actual (0 = sin normalizar)."""
        gain = 10 ** (max(-24.0, min(24.0, float(db))) / 20)
        if self._cur is not None:
            self._cur.track_gain = gain
            self._apply(self._cur)

    def set_next_gain_db(self, db: float) -> None:
        """Ganancia que tendrá la canción que se cargue a continuación."""
        self._next_gain = 10 ** (max(-24.0, min(24.0, float(db))) / 20)

    def stop(self, fade_ms: int = 0) -> None:
        """Detiene la canción actual (con fundido si se pide); las preparadas se conservan."""
        slot = self._cur
        self._cur, self.loaded, self.paused, self.length = None, False, False, 0
        if slot is None:
            return
        if fade_ms > 0 and not slot.ended:
            slot.start_ramp("out", fade_ms)
            self._fading.append(slot)
        else:
            slot.close()

    def close(self) -> None:
        self.stop()
        for slot in self._fading + list(self._prepared.values()):
            slot.close()
        self._fading.clear()
        self._prepared.clear()
