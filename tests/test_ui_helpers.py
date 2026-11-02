"""Funciones puras de la interfaz: color, curvas de animación e iconos."""
from PIL import Image

from app.ui import anim, icons, theme


def test_blend_endpoints_and_middle():
    assert theme.blend("#000000", "#ffffff", 0) == "#000000"
    assert theme.blend("#000000", "#ffffff", 1) == "#ffffff"
    assert theme.blend("#000000", "#646464", 0.5) == "#323232"


def test_readable_on_reaches_contrast_on_both_themes():
    for bg in (theme.DARK["bg"], theme.LIGHT["bg"]):
        for color in ("#101010", "#808080", "#ffff00", "#0000ff", "#ff6a4d"):
            assert theme.contrast(theme.readable_on(color, bg), bg) >= 4.5


def test_text_on_picks_the_most_legible():
    assert theme.text_on("#ffffff") == "#111114"
    assert theme.text_on("#000000") == "#ffffff"


def test_dominant_color_of_solid_image():
    r, g, b = theme._rgb(theme.dominant_color(Image.new("RGB", (40, 40), (200, 30, 30))))
    assert r > 180 and g < 50 and b < 50


def test_dominant_color_ignores_grey_covers():
    assert theme.dominant_color(Image.new("RGB", (40, 40), (120, 120, 120))) is None


def test_vivid_lifts_dull_colors_and_keeps_the_hue():
    r, g, b = theme._rgb(theme.vivid("#817550"))  # oliva apagado
    assert max(r, g, b) >= 0.85 * 255 * 0.99
    assert (max(r, g, b) - min(r, g, b)) / max(r, g, b) >= 0.54
    assert r > g > b


def test_seed_color_is_stable_and_differs_between_songs():
    assert theme.seed_color("abc") == theme.seed_color("abc")
    assert theme.seed_color("abc") != theme.seed_color("xyz")


def test_easings_start_at_zero_and_end_at_one():
    for fn in (anim.ease_out, anim.ease_in_out):
        assert fn(0) == 0
        assert abs(fn(1) - 1) < 1e-9


def test_every_icon_draws_something():
    for name in icons.names():
        assert icons.icon_mask(name, 24).getbbox() is not None, name


def test_standard_wave_is_the_same_for_every_song_and_not_flat():
    from app.ui import widgets

    assert widgets.STANDARD_LEVELS == widgets._standard_wave()
    assert len(widgets.STANDARD_LEVELS) == widgets.MAX_BARS
    assert min(widgets.STANDARD_LEVELS) > widgets.FLAT_LEVEL
    assert max(widgets.STANDARD_LEVELS) - min(widgets.STANDARD_LEVELS) > 0.3
