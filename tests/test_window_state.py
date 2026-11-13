"""Ventana recordada entre sesiones y teclas multimedia (funciones sin Tk)."""
from app.ui.main import App
from app.ui.window_state import fit, parse

MAIN = (0, 0, 1920, 1040)        # pantalla principal sin la barra de tareas
SIDE = (1920, 0, 3200, 984)      # un segundo monitor a la derecha


def test_parse_understands_tk_geometry():
    assert parse("1120x720+40+20") == (1120, 720, 40, 20)
    assert parse("1120x720+-1900+10") == (1120, 720, -1900, 10)
    assert parse("") is None and parse(None) is None and parse("grande") is None


def test_window_stays_where_it_was_if_it_still_fits():
    assert fit((1120, 720, 40, 20), [MAIN, SIDE], 880, 560) == (1120, 720, 40, 20)
    assert fit((1000, 700, 2000, 50), [MAIN, SIDE], 880, 560) == (1000, 700, 2000, 50)


def test_window_from_a_missing_monitor_comes_back_centered():
    w, h, x, y = fit((1000, 700, 2000, 50), [MAIN], 880, 560)
    assert (w, h) == (1000, 700)
    assert (x, y) == ((1920 - 1000) // 2, (1040 - 700) // 2)


def test_window_never_exceeds_the_work_area_nor_goes_below_the_minimum():
    assert fit((2500, 1400, 0, 0), [MAIN], 880, 560) == (1920, 1040, 0, 0)
    assert fit((300, 200, 100, 100), [MAIN], 880, 560)[:2] == (880, 560)
    w, h, x, y = fit((1120, 720, 1500, 900), [MAIN], 880, 560)  # se salía por abajo a la derecha
    assert x + w <= 1920 and y + h <= 1040


class _FakePlayer:
    def __init__(self, loaded, paused):
        self.loaded, self.paused = loaded, paused


class _FakeApp:
    def __init__(self, loaded=True, paused=False):
        self.player = _FakePlayer(loaded, paused)
        self.calls = []

    def step(self, d):
        self.calls.append(("step", d))

    def play_pause(self):
        self.calls.append("play_pause")


def test_media_keys_map_to_player_actions_without_toggling_twice():
    playing = _FakeApp(paused=False)
    for name in ("play", "pause", "next", "previous"):
        App._on_media_key(playing, name)
    assert playing.calls == ["play_pause", ("step", 1), ("step", -1)]  # «play» sonando: nada
    paused = _FakeApp(paused=True)
    App._on_media_key(paused, "pause")
    App._on_media_key(paused, "play")
    assert paused.calls == ["play_pause"]  # «pausa» ya en pausa: nada
