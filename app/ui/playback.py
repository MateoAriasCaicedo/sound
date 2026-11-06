"""Mixin de reproducción y transiciones para la ventana principal.

Se extrae aquí la lógica que decide qué suena después y cuándo se mezcla, para que
``main.App`` no acumule todo en un solo archivo. El mixin asume que ``self`` es la
instancia de ``App`` (acceso a ``player``, ``pq``, ``cur_track``, etc.).
"""
from pathlib import Path

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
            from ..core import log as _log
            _log.failure(_log.get("ui"), f"no se pudo dejar lista {vid}", exc)
            return False
        self._resolve_ahead([vid])
        return False

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
        if self._track_truncated(p.length):
            self._stop_damaged(p.length)
            return
        nxt = self._peek_next()
        if nxt is None:
            return
        ready = self._ensure_ready(nxt)
        x = self._xfade_for(self.cur_track, nxt)
        if ready and rem <= max(x, 0) + (LEAD_MS if x else GAPLESS_LEAD_MS):
            self.auto_fired = self.playing_vid
            self.step(1, auto=True)


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.0f}"


def _fmt_s(seconds: float | int) -> str:
    return f"{seconds:.0f}"