"""Teclas multimedia del teclado y panel multimedia de Windows (SMTC).

Windows enseña lo que suena en el recuadro que aparece al cambiar el volumen (y en la pantalla
de bloqueo) y manda ahí las teclas ⏯ ⏭ ⏮ del teclado o de unos auriculares Bluetooth. Lo
gestiona «System Media Transport Controls».

Sound usa varios MediaPlayer a la vez (uno por canción, para las mezclas) y a cada uno Windows le
daría su propia sesión, que pausaría el audio por su cuenta sin que la ventana se enterase. Por eso
esos reproductores llevan el «command manager» apagado (ver `player._Slot`) y aquí se crea un
MediaPlayer aparte que nunca suena: solo presta sus controles del sistema. La ventana le dice qué
suena (`set_track`), si está en pausa (`set_playing`) y por dónde va (`set_timeline`); las teclas
llegan a `on_button` desde un hilo de WinRT, así que la ventana las pasa a su cola de eventos.

Si el paquete `winrt-Windows.Media` no está, o no es Windows, `SystemMedia.create()` devuelve
None y la app funciona igual, sin teclas multimedia.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import timedelta

from ..core import log as _log

log = _log.get("media-keys")

BUTTONS = ("play", "pause", "next", "previous", "stop")


class SystemMedia:
    @classmethod
    def create(cls, on_button, on_seek=None) -> SystemMedia | None:
        try:
            return cls(on_button, on_seek)
        except Exception as exc:  # noqa: BLE001 - sin SMTC la app sigue, solo sin teclas
            _log.failure(log, "sin teclas multimedia ni panel de Windows", exc)
            return None

    def __init__(self, on_button, on_seek=None) -> None:
        from winrt.windows.media import (
            MediaPlaybackStatus,
            MediaPlaybackType,
            SystemMediaTransportControlsButton,
            SystemMediaTransportControlsTimelineProperties,
        )
        from winrt.windows.media.playback import MediaPlayer

        self._status = MediaPlaybackStatus
        self._type = MediaPlaybackType
        self._timeline_cls = SystemMediaTransportControlsTimelineProperties
        self._names = {
            SystemMediaTransportControlsButton.PLAY: "play",
            SystemMediaTransportControlsButton.PAUSE: "pause",
            SystemMediaTransportControlsButton.NEXT: "next",
            SystemMediaTransportControlsButton.PREVIOUS: "previous",
            SystemMediaTransportControlsButton.STOP: "stop",
        }
        self.on_button, self.on_seek = on_button, on_seek
        self._player = MediaPlayer()  # nunca suena: solo presta sus controles del sistema
        self._player.command_manager.is_enabled = False
        smtc = self._smtc = self._player.system_media_transport_controls
        smtc.is_enabled = True
        smtc.is_play_enabled = smtc.is_pause_enabled = True
        smtc.is_next_enabled = smtc.is_previous_enabled = True
        smtc.playback_status = MediaPlaybackStatus.CLOSED
        self._tokens = [smtc.add_button_pressed(self._pressed)]
        if on_seek is not None:
            self._tokens.append(smtc.add_playback_position_change_requested(self._seek))
        self._track_key = None
        self._cover_job = 0

    # ---------- eventos de Windows (llegan en un hilo de WinRT) ----------
    def _pressed(self, _sender, args) -> None:
        name = self._names.get(args.button)
        if name:
            self.on_button(name)

    def _seek(self, _sender, args) -> None:
        try:
            self.on_seek(int(args.requested_playback_position.total_seconds() * 1000))
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "salto pedido desde Windows", exc)

    # ---------- lo que se enseña ----------
    def set_track(self, title: str, artist: str, album: str = "",
                  cover: bytes | None = None) -> None:
        """Título, artista, álbum y portada; la portada se prepara en segundo plano."""
        key = (title, artist, album, len(cover or b""))
        if key == self._track_key:
            return
        self._track_key = key
        updater = self._smtc.display_updater
        updater.type = self._type.MUSIC
        music = updater.music_properties
        music.title = title or ""
        music.artist = artist or ""
        try:
            music.album_title = album or ""
            music.album_artist = (artist or "").split(",")[0].strip()
        except Exception:  # noqa: BLE001 - versiones viejas de Windows no tienen estos campos
            pass
        updater.thumbnail = None
        updater.update()
        self._cover_job += 1
        if cover:
            job = self._cover_job
            threading.Thread(target=self._set_cover, args=(cover, job), daemon=True,
                             name="smtc-portada").start()

    def _set_cover(self, data: bytes, job: int) -> None:
        try:
            reference = asyncio.run(_stream_reference(data))
        except Exception as exc:  # noqa: BLE001 - sin portada en el panel, nada más
            _log.failure(log, "portada para el panel de Windows", exc)
            return
        if job != self._cover_job:  # ya suena otra canción
            return
        updater = self._smtc.display_updater
        updater.thumbnail = reference
        updater.update()

    def set_playing(self, playing: bool | None) -> None:
        """True: suena; False: en pausa; None: nada cargado."""
        status = (self._status.PLAYING if playing else self._status.PAUSED) if playing is not None \
            else self._status.STOPPED
        if self._smtc.playback_status != status:
            self._smtc.playback_status = status

    def set_timeline(self, position_ms: int, length_ms: int) -> None:
        """La barra de progreso del panel (y desde dónde se puede saltar)."""
        if length_ms <= 0:
            return
        props = self._timeline_cls()
        props.start_time = timedelta(0)
        props.min_seek_time = timedelta(0)
        props.end_time = props.max_seek_time = timedelta(milliseconds=length_ms)
        props.position = timedelta(milliseconds=max(0, min(position_ms, length_ms)))
        self._smtc.update_timeline_properties(props)

    def close(self) -> None:
        try:
            self._smtc.playback_status = self._status.CLOSED
            self._smtc.is_enabled = False
            self._player.close()
        except Exception:  # noqa: BLE001 - al cerrar da igual
            pass


async def _stream_reference(data: bytes):
    """La imagen en memoria como algo que Windows sabe enseñar."""
    from winrt.windows.storage.streams import (
        DataWriter,
        InMemoryRandomAccessStream,
        RandomAccessStreamReference,
    )

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    return RandomAccessStreamReference.create_from_stream(stream)
