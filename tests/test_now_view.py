"""Vista «Reproduciendo»: composición, fondo y colores (funciones puras, sin Tk)."""
from PIL import Image

from app.ui import theme
from app.ui.now_view import _percentile, artwork, layout, scene_colors, tone_backdrop, wrap_two


class _Font:
    """Fuente de mentira: cada carácter mide 10 px."""

    def measure(self, text: str) -> int:
        return 10 * len(text)


def test_wrap_two_keeps_short_titles_and_cuts_the_second_line():
    font = _Font()
    assert wrap_two(font, "Dreams", 200) == ["Dreams"]
    assert wrap_two(font, "Go Your Own Way", 100) == ["Go Your", "Own Way"]
    lines = wrap_two(font, "The Chain Never Breaks Again Tonight", 100)
    assert len(lines) == 2 and lines[1].endswith("…")
    assert all(font.measure(line) <= 100 for line in lines)
    assert wrap_two(font, "Supercalifragilístico", 80)[0].endswith("…")


def test_layout_keeps_cover_and_panel_apart_and_inside_the_window():
    for w, h, k in ((1100, 560, 1.0), (1280, 690, 1.25), (1920, 1100, 1.0)):
        text_h, tab_h = int(150 * k), int(50 * k)
        L = layout(w, h, k, text_h, tab_h)
        assert L["x0"] >= L["pad"] and L["panel_x1"] <= w
        assert L["x0"] + L["size"] + L["gap"] == L["panel_x0"]
        assert L["text_y"] + text_h <= h  # el texto bajo la portada no se sale
        assert L["list_top"] < L["list_bottom"]
        assert L["size"] <= 720 * k


def test_bigger_windows_get_a_bigger_cover():
    small = layout(1100, 560, 1.0, 150, 50)["size"]
    big = layout(1920, 1100, 1.0, 150, 50)["size"]
    assert big > small


def _cover(color):
    img = Image.new("RGB", (60, 60), color)
    img.paste(Image.new("RGB", (30, 30), "#ffffff"), (0, 0))  # una zona muy clara
    return img


def test_dark_backdrop_is_dark_enough_for_light_text_everywhere():
    for color in ("#ffffff", "#ffe066", "#3060ff", "#101010"):
        small = tone_backdrop(_cover(color), None, (1200, 600), True, theme.DARK["bg"])
        upper = small.crop((0, 0, small.width, int(small.height * 0.7))).convert("L")
        assert _percentile(upper, 0.95) <= 90


def test_light_backdrop_is_light_enough_for_dark_text_everywhere():
    for color in ("#000000", "#203080", "#ff3030", "#f0f0f0"):
        small = tone_backdrop(_cover(color), None, (1200, 600), False, theme.LIGHT["bg"])
        upper = small.crop((0, 0, small.width, int(small.height * 0.7))).convert("L")
        assert _percentile(upper, 0.05) >= 180


def test_backdrop_fades_into_the_window_at_the_bottom():
    small = tone_backdrop(_cover("#ff0000"), None, (1200, 600), True, theme.DARK["bg"])
    bottom = small.getpixel((small.width // 2, small.height - 1))
    assert all(abs(a - b) <= 12 for a, b in zip(bottom, theme._rgb(theme.DARK["bg"])))


def test_backdrop_without_cover_uses_the_song_color():
    small = tone_backdrop(None, "abc123", (800, 400), True, theme.DARK["bg"])
    assert small.size[0] == 72


def test_scene_colors_meet_contrast_on_both_themes():
    for dark, pal in ((True, theme.DARK), (False, theme.LIGHT)):
        for color in ("#ffffff", "#ffe066", "#3060ff", "#101010"):
            small = tone_backdrop(_cover(color), None, (1200, 600), dark, pal["bg"])
            c = scene_colors(small, pal, dark, theme.vivid(color))
            assert theme.contrast(c["fg"], c["bg"]) >= 7
            assert theme.contrast(c["dim"], c["bg"]) >= 4.5
            assert theme.contrast(c["accent"], c["bg"]) >= 4.5


def test_artwork_has_room_for_its_shadow():
    img, pad = artwork(_cover("#ff0000"), None, 300, True, 20)
    assert pad == 2 * min(round(300 * 0.045), 20)
    assert img.width == 300 + 2 * pad and img.height > img.width  # la sombra cae hacia abajo
    assert img.getpixel((pad + 150, pad + 150))[3] == 255
    assert img.getpixel((0, 0))[3] < 20
