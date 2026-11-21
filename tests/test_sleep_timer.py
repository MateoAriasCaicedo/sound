"""Temporizador para dormir, con una ventana y un reproductor de mentira (no suena nada)."""
import math
import time

import pytest

from app.ui import sleep_timer
from app.ui.player import _Slot, ramp_gain
from app.ui.sleep_timer import (
    FADE_MS,
    SleepTimerMixin,
    badge_text,
    choice_label,
    remaining_text,
)


def test_choice_labels_read_naturally():
    assert choice_label(15) == "15 minutos"
    assert choice_label(60) == "1 hora"
    assert choice_label(90) == "1 hora y media"
    assert choice_label(120) == "2 horas"
    assert choice_label(75) == "1 hora y 15 minutos"


def test_remaining_text_and_badge_round_up():
    assert remaining_text(30) == "menos de un minuto"
    assert remaining_text(60) == "1 min"
    assert remaining_text(61) == "2 min"
    assert remaining_text(3600) == "1 h"
    assert remaining_text(3900) == "1 h 5 min"
    assert badge_text(1) == "1" and badge_text(59.5) == "1" and badge_text(60.5) == "2"


def test_dim_fade_goes_down_from_the_start_and_ends_silent():
    assert ramp_gain("dim", 0) == 1 and ramp_gain("dim", 1) == 0
    # a mitad ya ha bajado bastante (la curva de crossfade aún estaría en 0.71)
    assert ramp_gain("dim", 0.5) == pytest.approx(0.25)
    assert ramp_gain("out", 0.5) == pytest.approx(math.cos(math.pi / 4))


@pytest.mark.parametrize("kind", ["in", "out", "dim"])
def test_a_ramp_continues_from_the_current_gain_without_a_jump(kind):
    slot = _Slot.__new__(_Slot)  # sin MediaPlayer: solo la cuenta del fundido
    slot.gain = 0.4
    slot.start_ramp(kind, 10_000)
    ramp = slot.ramp
    p = (time.monotonic() - ramp["t0"]) * 1000 / ramp["ms"]
    assert ramp_gain(kind, p) == pytest.approx(0.4, abs=0.01)


# ---------- la mezcla, con una ventana de mentira ----------
class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Player:
    def __init__(self):
        self.loaded = True
        self.paused = False
        self.faded: list[int] = []
        self.stopped = 0

    def fade_to_pause(self, ms):
        self.paused = True
        self.faded.append(ms)

    def stop(self, fade_ms=0):
        self.loaded = self.paused = False
        self.stopped += 1

    def toggle(self):
        self.paused = not self.paused


class _Widget:
    def __init__(self):
        self.text = ""
        self.on = False
        self.badge = None

    def configure(self, text=""):
        self.text = text

    def set_on(self, on):
        self.on = on

    def set_badge(self, text, font=None, muted=False):
        self.badge = text


class _Toast:
    def __init__(self):
        self.shown: list[tuple] = []

    def show(self, text, action="", on_action=None, on_expire=None):
        self.shown.append((text, action, on_action))


class _FakeApp(SleepTimerMixin):
    def __init__(self):
        self._init_sleep()
        self.player = _Player()
        self.btn_play = _Widget()
        self.btn_sleep = _Widget()
        self.status = _Widget()
        self.toast = _Toast()
        self.fonts = {"badge": "badge"}
        self.cur_track = {"video_id": "a", "title": "Nana"}
        self.steps: list[int] = []
        self.transitioning_since = None
        self.auto_fired = None
        self.resume_position = 0

    # lo que la mezcla pide a la App: el mismo orden que en main.App
    def play_pause(self):
        if self._sleep_wake():
            self.step(1)
            return
        self.player.toggle()

    def step(self, d, auto=False):
        if auto and self._sleep_stops_queue():
            return
        self.steps.append(d)
        self.player.loaded, self.player.paused = True, False


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(sleep_timer, "_clock", c)
    return c


def test_minutes_count_down_on_the_button_and_then_fade_and_pause(clock):
    app = _FakeApp()
    app.set_sleep_timer(30)
    assert app.btn_sleep.on and app.btn_sleep.badge == "30"
    assert "30 minutos" in app.toast.shown[-1][0]
    clock.now += 20 * 60 + 30
    app._sleep_tick()
    assert app.btn_sleep.badge == "10" and not app.player.faded
    # el fundido empieza antes de la hora, para acabar en silencio justo a la hora elegida
    clock.now = app.sleep_deadline - FADE_MS / 1000 + 0.1
    app._sleep_tick()
    assert app.player.faded == [FADE_MS] and app.player.paused
    assert app.sleep_mode is None and not app.btn_sleep.on and app.btn_sleep.badge is None
    assert app.btn_play.text == "▶"
    text, action, on_action = app.toast.shown[-1]
    assert action == "Seguir escuchando"
    on_action()  # despierto: vuelve a sonar
    assert not app.player.paused and not app._sleep_hold


def test_a_song_that_ends_while_fading_does_not_start_the_next(clock):
    app = _FakeApp()
    app.set_sleep_timer(15)
    clock.now = app.sleep_deadline
    app._sleep_tick()
    app.step(1, auto=True)  # la canción acabó a mitad del fundido
    assert app.steps == [] and app.player.stopped == 1
    app.play_pause()  # por la mañana: sigue con la siguiente
    assert app.steps == [1]


def test_after_the_track_plays_it_whole_and_stops(clock):
    app = _FakeApp()
    app.sleep_after_track()
    assert app.sleep_mode == "track" and app.btn_sleep.on and app.btn_sleep.badge is None
    assert "Nana" in app.toast.shown[-1][0]
    assert app._sleep_blocks_transition()  # nada de crossfade con la siguiente
    app.step(1, auto=True)  # terminó
    assert app.steps == [] and not app.player.loaded and app.btn_play.text == "▶"
    assert app.sleep_mode is None and not app.btn_sleep.on
    app.play_pause()
    assert app.steps == [1]
    app.step(1, auto=True)  # lo siguiente ya sigue con normalidad
    assert app.steps == [1, 1]


def test_after_the_track_needs_something_playing():
    app = _FakeApp()
    app.player.loaded = False
    app.sleep_after_track()
    assert app.sleep_mode is None and not app.toast.shown


def test_cancel_and_extend(clock):
    app = _FakeApp()
    app.set_sleep_timer(15)
    clock.now += 10 * 60
    app.extend_sleep()
    assert app.btn_sleep.badge == "20"
    app.cancel_sleep()
    assert app.sleep_mode is None and not app.btn_sleep.on and app.btn_sleep.badge is None
    assert app.toast.shown[-1][0] == "Temporizador desactivado"
    clock.now += 60 * 60
    app._sleep_tick()
    assert not app.player.faded  # cancelado: no se apaga


def test_when_already_paused_the_timer_just_ends(clock):
    app = _FakeApp()
    app.set_sleep_timer(15)
    app.player.paused = True
    clock.now = app.sleep_deadline
    app._sleep_tick()
    assert not app.player.faded and app.sleep_mode is None and not app._sleep_hold
    app.step(1, auto=True)
    assert app.steps == [1]  # sin temporizador pendiente, la cola sigue como siempre
