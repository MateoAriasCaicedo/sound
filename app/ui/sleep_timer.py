"""Temporizador para dormir: la música se apaga sola al cabo de un rato o al terminar la canción.

- Con minutos, al llegar la hora baja poco a poco durante `FADE_MS` y se pausa: al pulsar play
  sigue donde estaba, con su volumen de siempre. Si el fundido te pilla despierto, «Seguir
  escuchando» (o play) lo deshace.
- «Al terminar la canción» deja que suene entera, sin mezclarse con la siguiente, y para. Play
  empieza la siguiente de la cola.
- El botón de la luna, en la barra de abajo, lleva los minutos que quedan. No se guarda entre
  sesiones: es para esta vez.
"""
from __future__ import annotations

import math
import time
import tkinter as tk

from .widgets import IconButton

CHOICES = (15, 30, 45, 60, 90)  # minutos
EXTEND_MIN = 15
FADE_MS = 12_000  # lo que tarda en apagarse del todo: acaba justo a la hora elegida

_clock = time.monotonic  # las pruebas lo sustituyen


def choice_label(minutes: int) -> str:
    """«15 minutos», «1 hora», «1 hora y media»."""
    if minutes < 60:
        return f"{minutes} minutos"
    hours, rest = divmod(minutes, 60)
    text = "1 hora" if hours == 1 else f"{hours} horas"
    if rest == 30:
        return f"{text} y media"
    return f"{text} y {rest} minutos" if rest else text


def remaining_text(seconds: float) -> str:
    """«23 min», «1 h 5 min», «menos de un minuto»."""
    if seconds < 60:
        return "menos de un minuto"
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def badge_text(seconds: float) -> str:
    """Los minutos que quedan, redondeando hacia arriba: «1» durante el último."""
    return str(max(1, math.ceil(seconds / 60)))


class SleepTimerMixin:
    def _init_sleep(self) -> None:
        self.sleep_mode: str | None = None  # None, "time" o "track"
        self.sleep_deadline = 0.0           # con "time": cuándo queda en silencio (monotonic)
        self.sleep_minutes = 0              # lo elegido, para marcarlo en el menú
        self._sleep_hold = False            # se está apagando: la cola no sigue sola
        self._sleep_next_on_play = False    # paró al acabar la canción: play pone la siguiente

    def _build_sleep_button(self, parent) -> IconButton:
        self.btn_sleep = IconButton(parent, self.anim, self.k, "moon", self.open_sleep_menu,
                                    tip=self._sleep_tip)
        return self.btn_sleep

    # ---------- estado ----------
    def _sleep_left(self) -> float:
        return max(0.0, self.sleep_deadline - _clock())

    def _sleep_tip(self) -> str:
        if self.sleep_mode == "time":
            return f"La música se apaga en {remaining_text(self._sleep_left())}"
        if self.sleep_mode == "track":
            return "La música se apaga al terminar la canción"
        return "Temporizador para dormir"

    def _refresh_sleep_button(self) -> None:
        button = getattr(self, "btn_sleep", None)
        if button is None:
            return
        button.set_on(self.sleep_mode is not None)
        badge = badge_text(self._sleep_left()) if self.sleep_mode == "time" else None
        button.set_badge(badge, self.fonts["badge"])

    # ---------- menú ----------
    def open_sleep_menu(self) -> None:
        menu = self._menu()
        current = (str(self.sleep_minutes) if self.sleep_mode == "time"
                   else self.sleep_mode or "")
        self._sleep_var = tk.StringVar(master=self, value=current)  # vive mientras el menú
        # sin entradas desactivadas: Windows las pinta en relieve y sobre el fondo oscuro
        # resaltan más que las normales
        for minutes in CHOICES:
            menu.add_radiobutton(label=choice_label(minutes), value=str(minutes),
                                 variable=self._sleep_var,
                                 command=lambda m=minutes: self.set_sleep_timer(m))
        if self.player.loaded or self.sleep_mode == "track":
            menu.add_radiobutton(label="Al terminar la canción", value="track",
                                 variable=self._sleep_var, command=self.sleep_after_track)
        if self.sleep_mode:
            menu.add_separator()
            label = "Desactivar el temporizador"
            if self.sleep_mode == "time":
                menu.add_command(label=f"Añadir {EXTEND_MIN} minutos", command=self.extend_sleep)
                label = f"Desactivar (quedan {remaining_text(self._sleep_left())})"
            menu.add_command(label=label, command=self.cancel_sleep)
        button = self.btn_sleep
        menu.update_idletasks()
        y = button.winfo_rooty() - menu.winfo_reqheight()  # el botón está abajo: se abre hacia arriba
        try:
            menu.tk_popup(button.winfo_rootx(), max(0, y))
        finally:
            menu.grab_release()

    # ---------- acciones ----------
    def set_sleep_timer(self, minutes: int) -> None:
        self.sleep_mode = "time"
        self.sleep_minutes = int(minutes)
        self.sleep_deadline = _clock() + self.sleep_minutes * 60
        self._refresh_sleep_button()
        self.toast.show(f"La música se apagará dentro de {choice_label(self.sleep_minutes)}")

    def extend_sleep(self) -> None:
        if self.sleep_mode != "time":
            return
        self.sleep_deadline = max(self.sleep_deadline, _clock()) + EXTEND_MIN * 60
        self._refresh_sleep_button()
        self.toast.show(f"La música se apagará en {remaining_text(self._sleep_left())}")

    def sleep_after_track(self) -> None:
        if not self.player.loaded:
            self.status.configure(text="El temporizador «al terminar la canción» necesita que "
                                       "algo esté sonando")
            return
        self.sleep_mode = "track"
        self.sleep_minutes = 0
        self._refresh_sleep_button()
        title = (self.cur_track or {}).get("title")
        self.toast.show(f"La música se apagará al terminar «{title}»" if title
                        else "La música se apagará al terminar la canción")

    def cancel_sleep(self) -> None:
        if self.sleep_mode is None:
            return
        self.sleep_mode = None
        self._refresh_sleep_button()
        self.toast.show("Temporizador desactivado")

    # ---------- cada tick ----------
    def _sleep_tick(self) -> None:
        """Desde el tick de reproducción: la cuenta atrás del botón y, a la hora, el fundido."""
        if self.sleep_mode != "time":
            return
        if self._sleep_left() * 1000 <= FADE_MS:
            self._sleep_fire()
        else:
            self._refresh_sleep_button()

    def _sleep_fire(self) -> None:
        self.sleep_mode = None
        self._refresh_sleep_button()
        player = self.player
        if not player.loaded or player.paused:
            self.status.configure(text="El temporizador terminó: la música ya estaba parada")
            return
        self._sleep_hold = True
        player.fade_to_pause(FADE_MS)
        self.btn_play.configure(text="▶")
        self.toast.show("El temporizador terminó: la música se está apagando",
                        "Seguir escuchando", self._sleep_keep_playing)

    def _sleep_keep_playing(self) -> None:
        if self.player.loaded and self.player.paused:
            self.play_pause()

    # ---------- enganches con la reproducción ----------
    def _sleep_blocks_transition(self) -> bool:
        """Con «al terminar la canción» no se mezcla con la siguiente: suena entera."""
        return self.sleep_mode == "track" or self._sleep_hold

    def _sleep_stops_queue(self) -> bool:
        """Desde `step(auto=True)`: True si toca parar en vez de seguir con la siguiente."""
        if self.sleep_mode != "track" and not self._sleep_hold:
            return False
        by_track = self.sleep_mode == "track"
        self.sleep_mode = None
        self._sleep_hold = False
        self._sleep_next_on_play = True
        self.player.stop()
        self.transitioning_since = None
        self.auto_fired = None
        self.resume_position = 0
        self.btn_play.configure(text="▶")
        self._refresh_sleep_button()
        text = "La canción terminó y la música se detuvo: play sigue con la siguiente"
        if by_track:
            self.toast.show(text)
        else:
            self.status.configure(text=text)
        return True

    def _sleep_wake(self) -> bool:
        """Al pulsar play o elegir otra canción: se acabó el «apagándose». True si play debe
        empezar la siguiente (la anterior terminó con el temporizador)."""
        self._sleep_hold = False
        if self._sleep_next_on_play:
            self._sleep_next_on_play = False
            return not self.player.loaded
        return False
