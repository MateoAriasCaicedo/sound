"""Iconos de línea fina dibujados con Pillow (retícula de 24): los glifos de fuente no combinan."""
import math

from PIL import Image, ImageDraw

SS = 6  # sobremuestreo: Tk no suaviza bordes, se suaviza aquí


def _line(d, pts, w) -> None:
    pts = [(x * SS, y * SS) for x, y in pts]
    d.line(pts, fill=255, width=w, joint="curve")
    r = w / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)


def _poly(d, pts) -> None:
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=255)


def _curve(fn, a: float, b: float, n: int = 40) -> list:
    return [fn(a + (b - a) * i / n) for i in range(n + 1)]


def _arc(cx, cy, r, a0, a1) -> list:
    return _curve(lambda a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))),
                  a0, a1, 16)


def _prev(d, w):
    _poly(d, [(19, 5.5), (19, 18.5), (8.5, 12)])
    _line(d, [(5.5, 6), (5.5, 18)], w)


def _next(d, w):
    _poly(d, [(5, 5.5), (5, 18.5), (15.5, 12)])
    _line(d, [(18.5, 6), (18.5, 18)], w)


def _shuffle(d, w):
    _line(d, [(3.5, 7), (7.5, 7), (15.5, 17), (20, 17)], w)
    _line(d, [(3.5, 17), (7.5, 17), (15.5, 7), (20, 7)], w)
    _line(d, [(17.5, 4.5), (20.5, 7), (17.5, 9.5)], w)
    _line(d, [(17.5, 14.5), (20.5, 17), (17.5, 19.5)], w)


def _repeat(d, w):
    _line(d, [(4.5, 11.5), *_arc(8, 9.5, 3.5, 180, 270), (19.5, 6)], w)
    _line(d, [(16.5, 3), (19.5, 6), (16.5, 9)], w)
    _line(d, [(19.5, 12.5), *_arc(16, 14.5, 3.5, 0, 90), (4.5, 18)], w)
    _line(d, [(7.5, 15), (4.5, 18), (7.5, 21)], w)


def _repeat1(d, w):
    _repeat(d, w)
    _line(d, [(11, 10.5), (12.3, 9.5), (12.3, 14.5)], w)


def _infinity(d, w):
    def lem(t):
        den = 1 + math.sin(t) ** 2
        return (12 + 9 * math.cos(t) / den, 12 + 9 * math.sin(t) * math.cos(t) / den * 1.1)
    _line(d, _curve(lem, 0, 2 * math.pi, 80), w)


def _xfade(d, w):
    """Dos círculos que se solapan: una canción que se mezcla con la siguiente."""
    _line(d, _arc(9, 12, 6, 0, 360), w)
    _line(d, _arc(15, 12, 6, 0, 360), w)


def _queue(d, w):
    _line(d, [(4, 6.5), (20, 6.5)], w)
    _line(d, [(4, 12), (13, 12)], w)
    _line(d, [(4, 17.5), (13, 17.5)], w)
    _poly(d, [(16, 11.5), (16, 19), (21.5, 15.25)])


def _volume(d, w):
    _poly(d, [(3.5, 9.5), (7.5, 9.5), (12.5, 5.5), (12.5, 18.5), (7.5, 14.5), (3.5, 14.5)])
    _line(d, _arc(12.5, 12, 3.8, -45, 45), w)
    _line(d, _arc(12.5, 12, 7.3, -45, 45), w)


def _search(d, w):
    _line(d, _arc(10.5, 10.5, 6.5, 0, 360), w)
    _line(d, [(15.5, 15.5), (20.5, 20.5)], w)


def _theme(d, w):
    _line(d, _arc(12, 12, 8, 0, 360), w)
    d.pieslice((4 * SS, 4 * SS, 20 * SS, 20 * SS), 90, 270, fill=255)


def _note(d, w):
    d.ellipse((5 * SS, 14 * SS, 11.5 * SS, 20 * SS), fill=255)
    _line(d, [(10.8, 17), (10.8, 4.5)], w)
    _line(d, _curve(lambda x: (x, 4.5 + 5 * ((x - 10.8) / 7.7) ** 1.6), 10.8, 18.5), w)


def _downloads(d, w):
    _line(d, [(12, 4), (12, 15)], w)
    _line(d, [(7.5, 10.5), (12, 15), (16.5, 10.5)], w)
    _line(d, [(4.5, 19.5), (19.5, 19.5)], w)


def _back(d, w):
    _line(d, [(19, 5.5), (11.5, 12), (19, 18.5)], w)
    _line(d, [(11.5, 12), (20.5, 12)], w)


def _link(d, w):
    _line(d, _arc(8.5, 15.5, 4.2, 35, 300), w)
    _line(d, _arc(15.5, 8.5, 4.2, 215, 480), w)
    _line(d, [(9.5, 14.5), (14.5, 9.5)], w)


_ICONS = dict(prev=_prev, next=_next, shuffle=_shuffle, repeat=_repeat, repeat1=_repeat1,
              infinity=_infinity, xfade=_xfade, queue=_queue, volume=_volume, search=_search,
              theme=_theme, downloads=_downloads, note=_note, back=_back, link=_link)


def names() -> list[str]:
    return list(_ICONS)


def icon_mask(name: str, size: int, stroke: float = 1.5) -> Image.Image:
    """Máscara en escala de grises (L) del icono; se colorea al pintarlo."""
    img = Image.new("L", (24 * SS, 24 * SS), 0)
    _ICONS[name](ImageDraw.Draw(img), max(1, round(stroke * SS)))
    return img.resize((size, size), Image.LANCZOS)
