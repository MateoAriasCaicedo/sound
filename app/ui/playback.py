"""Mixin de reproducción y transiciones para la ventana principal.

Se extrae aquí la lógica que decide qué suena después y cuándo se mezcla, para que
``main.App`` no acumule todo en un solo archivo. El mixin asume que ``self`` es la
instancia de ``App`` (acceso a ``player``, ``pq``, ``cur_track``, etc.).
"""
import time
from pathlib import Path

from ..core import waveform

FADE_TICK_MS = 40
PREPARE_WINDOW_MS = 12_000
LEAD_MS = 60
GAPLESS_LEAD_MS = 100


def fmt(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


class PlaybackMixin:
    # ---------- transiciones ----------
    def _peek_next(self) -> dict | None:
        """La canción que `step(1)` elegiría ahora, sin cambiar nada."""
        return self.pq.peek_next(self.cur_track)

    def _track_energy(self, track: dict | None) -> float | None:
        """Intensidad media de una canción (0 a 1), si su onda ya se midió."""
        vid = (track or {}).get("video_id")
        if not vid:
            return None
        cache = self._energy_cache
        if vid not in cache:
            levels = waveform.load(vid)
            cache[vid] = waveform.summary(levels)["energy"] if levels else None
        return cache[vid]

    def _xfade_for(self, cur: dict | None, nxt: dict) -> int:
        """Duración del crossfade entre dos canciones. 0 = paso sin hueco (gapless)."""
        x = self.xfade_ms
        if x <= 0 or cur is None:
            return 0
        if cur.get("album") and cur.get("album") == nxt.get("album"):
            return 0  # pistas del mismo álbum: suelen enlazar entre sí, no se pisan
        x = self._adapt_xfade(x, cur, nxt)
        for ms in (self.player.length, (nxt.get("duration") or 0) * 1000):
            if ms:
                x = min(x, int(ms * 0.3))  # una canción corta no se pierde en la mezcla
        return max(x, 0)

    def _adapt_xfade(self, x: int, cur: dict, nxt: dict) -> int:
        """Ajusta la mezcla a la energía: más larga entre canciones tranquilas y más corta
        cuando el salto de intensidad es grande. Sin datos de onda, se respeta lo configurado."""
        if x <= 0:
            return x
        a, b = self._track_energy(cur), self._track_energy(nxt)
        if a is None or b is None:
            return x
        if a < 0.35 and b < 0.35:
            return int(x * 1.25)
        if abs(a - b) > 0.45:
            return max(1000, int(x * 0.7))
        return x

    def _ensure_ready(self, t: dict) -> bool:
        """Deja la siguiente abierta en el reproductor para que arranque sin demora."""
        vid = t["video_id"]
        if self.player.is_prepared(vid):
            return True
        local = t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")
        try:
            if local and Path(local).exists():
                self._ensure_playable(vid, local)
                self.player.prepare_path(vid, local)
                return True
            from ..core import streaming
            url = streaming.cached(vid)
            if url:
                self.player.prepare(vid, url)
                return True
        except Exception as exc:  # noqa: BLE001 - sin preparar, la transición simplemente no se anticipa
            from ..core import log as _log
            _log.failure(_log.get("ui"), f"no se pudo dejar lista {vid}", exc)
            return False
        self._resolve_ahead([vid])
        return False

    # ---------- archivos que Windows no sabe reproducir ----------
    def _ensure_playable(self, vid: str, path: str) -> None:
        """Windows da por terminado al empezar el .m4a fragmentado que entrega YouTube (sin
        ffmpeg se guarda así): antes de sonar se convierte a MP4 normal, sin recodificar."""
        from ..core import mp4fix
        if mp4fix.is_fragmented(path):
            self.player.forget(vid)  # una versión ya preparada del archivo viejo no serviría
            if mp4fix.defragment(path):
                from ..core import log as _log
                _log.get("ui").info("convertida a MP4 normal antes de sonar: %s", Path(path).name)

    def _fix_fragmented(self) -> None:
        """Al arrancar, en segundo plano: toda la biblioteca pasa a MP4 normal (una sola vez;
        luego ya no queda ninguna fragmentada y el repaso solo lee cabeceras)."""
        from ..core import log as _log
        from ..core import mp4fix
        fixed = 0
        for track in list(self.library):
            try:
                if mp4fix.is_fragmented(track["path"]) and mp4fix.defragment(track["path"]):
                    fixed += 1
            except Exception as exc:  # noqa: BLE001 - una canción rara no detiene el repaso
                _log.failure(_log.get("ui"), f"no se pudo revisar {track.get('path')}", exc)
        if fixed:
            _log.get("ui").info("%d canciones convertidas a MP4 normal para Windows", fixed)

    def _impossible_end(self, position_ms: int) -> bool:
        """¿La canción «llegó» más lejos de lo que da el tiempo que lleva sonando? Pasa cuando
        Windows no puede leer el audio: la da por terminada al instante y, sin este freno, la
        cola saltaba de canción en canción (al azar, con el orden aleatorio)."""
        ref_ms, since = self._clock_ref
        elapsed_ms = (time.monotonic() - since) * 1000
        return position_ms > ref_ms + elapsed_ms * 1.5 + 4000

    def _halt_impossible(self) -> None:
        """Para en vez de encadenar saltos; si era un archivo por convertir, lo convierte y
        vuelve a intentarlo una vez."""
        t = self.cur_track or {}
        vid = t.get("video_id")
        local = (t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")) if vid else None
        self.player.stop()
        self.transitioning_since = None
        self.auto_fired = None
        from ..core import log as _log
        from ..core import mp4fix
        if vid and local and vid not in self._fix_retried and mp4fix.is_fragmented(local):
            self._fix_retried.add(vid)
            self._play_track(t, "cut")  # _start la convierte antes de cargarla
            return
        self.btn_play.configure(text="▶")
        title = t.get("title") or "la canción"
        _log.get("ui").warning("Windows terminó %s (%s) nada más empezar; se detiene la cola",
                               title, vid)
        self.status.configure(text=f"No se pudo reproducir «{title}»")
        self.toast.show(f"Windows no pudo reproducir «{title}»: se detuvo para no saltar "
                        "entre canciones", "Reintentar", lambda: self._play_track(t))

    def _track_truncated(self, actual_ms: int) -> bool:
        """¿La canción local que suena dura mucho menos de lo anunciado? Los streams no se juzgan."""
        if self.stream_t is not None or self.cur_track is None:
            return False
        from ..core import media
        return media.is_truncated(self.cur_track.get("duration"), actual_ms)

    def _stop_damaged(self, actual_ms: int) -> None:
        """Detiene una canción incompleta en vez de encadenar saltos a la siguiente."""
        t = self.cur_track or {}
        vid = t.get("video_id")
        expected = t.get("duration") or 0
        local = (t.get("path") or (self.lib_by_id.get(vid) or {}).get("path")) if vid else None
        from ..core import log as _log
        _log.get("ui").warning(
            "audio incompleto: %s (%s) dura %s de %s s%s; se detiene para no saltar",
            t.get("title"), vid, _fmt_ms(actual_ms), _fmt_s(expected),
            f" — {local}" if local else "")
        self.player.stop()
        self.transitioning_since = None
        self.auto_fired = None
        self.btn_play.configure(text="▶")
        if vid:
            self.damaged.add(vid)
            self._set_state(vid, "✗")
            if local:
                _log.get("ui").warning(
                    "audio incompleto: se conserva %s; no se borra automáticamente", local)
            if self.online and getattr(self.dl, "service", None) is not None:
                try:
                    # El archivo se aparta como «dañado» antes de volver a descargarlo: nunca se
                    # destruye la copia del usuario sin que lo haya pedido.
                    redownload = dict(t)
                    redownload.pop("path", None)
                    from ..core import library
                    library.requeue([vid])  # antes de despertar al servicio
                    self.dl.start_download(redownload, quiet=True)
                    self._set_state(vid, "en cola")
                except Exception:  # noqa: BLE001
                    pass
        self.status.configure(
            text=f"Archivo incompleto: «{t.get('title', vid)}» "
                 f"({fmt(actual_ms / 1000)} de {fmt(expected)}). Se conserva; puedes redescargarla.")

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
        if self._impossible_end(pos):  # Windows la «terminó» de golpe: no se encadena otra
            self._halt_impossible()
            return
        if self._track_truncated(p.length):
            self._stop_damaged(p.length)
            return
        nxt = self._peek_next()
        if nxt is None:
            return
        ready = self._ensure_ready(nxt)
        if ready:
            self._preview_ambient(nxt)  # la ventana vira al color de la siguiente antes de mezclar
        x = self._xfade_for(self.cur_track, nxt)
        if ready and rem <= max(x, 0) + (LEAD_MS if x else GAPLESS_LEAD_MS):
            self.auto_fired = self.playing_vid
            self.step(1, auto=True)


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.0f}"


def _fmt_s(seconds: float | int) -> str:
    return f"{seconds:.0f}"