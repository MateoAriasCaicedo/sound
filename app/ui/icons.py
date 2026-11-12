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


def _more(d, w):
    """Tres puntos: el resto de acciones de la biblioteca."""
    for x in (5.5, 12, 18.5):
        d.ellipse(((x - 1.6) * SS, 10.4 * SS, (x + 1.6) * SS, 13.6 * SS), fill=255)


def _tools(d, w):
    """Caja de herramientas: las acciones de mantenimiento de la biblioteca."""
    d.rounded_rectangle((4 * SS, 9 * SS, 20 * SS, 20 * SS), radius=2.4 * SS,
                        outline=255, width=w)
    _line(d, [(8.5, 9), (8.5, 6.5), (15.5, 6.5), (15.5, 9)], w)
    _line(d, [(4, 14), (20, 14)], w)
    d.rounded_rectangle((10.4 * SS, 12.2 * SS, 13.6 * SS, 15.8 * SS), radius=1 * SS, fill=255)


def _settings(d, w):
    """Engranaje de ocho dientes: ajustes y atajos."""
    _line(d, _arc(12, 12, 7.5, 0, 360), w)
    _line(d, _arc(12, 12, 3, 0, 360), w)
    for angle in range(0, 360, 45):
        a = math.radians(angle)
        x0, y0 = 12 + 7.5 * math.cos(a), 12 + 7.5 * math.sin(a)
        x1, y1 = 12 + 10 * math.cos(a), 12 + 10 * math.sin(a)
        _line(d, [(x0, y0), (x1, y1)], w)


def _grid(d, w):
    """Cuatro baldosas: la vista de cuadrícula."""
    for x, y in ((4, 4), (13, 4), (4, 13), (13, 13)):
        d.rounded_rectangle((x * SS, y * SS, (x + 7) * SS, (y + 7) * SS),
                            radius=1.6 * SS, outline=255, width=w)


def _list(d, w):
    """Filas con su viñeta: la vista de lista."""
    for y in (6.5, 12, 17.5):
        d.ellipse(((4 - 1.2) * SS, (y - 1.2) * SS, (4 + 1.2) * SS, (y + 1.2) * SS), fill=255)
        _line(d, [(8.5, y), (20, y)], w)


def _mini(d, w):
    """Ventana con un recuadro en su esquina: el mini reproductor."""
    d.rounded_rectangle((3 * SS, 5 * SS, 21 * SS, 19 * SS), radius=2.4 * SS, outline=255, width=w)
    d.rounded_rectangle((11.5 * SS, 11.5 * SS, 18 * SS, 16.5 * SS), radius=1.2 * SS, fill=255)


def _expand(d, w):
    """Dos flechas hacia las esquinas: volver a la ventana completa."""
    _line(d, [(13.5, 4.5), (19.5, 4.5), (19.5, 10.5)], w)
    _line(d, [(19.5, 4.5), (13.5, 10.5)], w)
    _line(d, [(10.5, 19.5), (4.5, 19.5), (4.5, 13.5)], w)
    _line(d, [(4.5, 19.5), (10.5, 13.5)], w)


def _shrink(d, w):
    """Dos flechas hacia el centro: salir de la pantalla completa."""
    _line(d, [(4.5, 10.5), (10.5, 10.5), (10.5, 4.5)], w)
    _line(d, [(10.5, 10.5), (4.5, 4.5)], w)
    _line(d, [(19.5, 13.5), (13.5, 13.5), (13.5, 19.5)], w)
    _line(d, [(13.5, 13.5), (19.5, 19.5)], w)


def _stage(d, w):
    """Un recuadro con una portada y dos líneas de texto: «Reproduciendo» en grande."""
    d.rounded_rectangle((3 * SS, 5 * SS, 21 * SS, 19 * SS), radius=2.4 * SS, outline=255, width=w)
    d.rounded_rectangle((6 * SS, 8.5 * SS, 12 * SS, 15.5 * SS), radius=1.2 * SS, fill=255)
    _line(d, [(14.5, 10), (18, 10)], w)
    _line(d, [(14.5, 14), (16.8, 14)], w)


def _chevron_down(d, w):
    """Cerrar la pantalla «Reproduciendo»: vuelve hacia el reproductor, abajo."""
    _line(d, [(5.5, 9), (12, 15.5), (18.5, 9)], w)


def _heart_points() -> list:
    """Contorno clásico de corazón, escalado a la retícula de 24."""
    def point(t):
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        return 12 + 0.5 * x, 10.8 - 0.5 * y
    return _curve(point, 0, 2 * math.pi, 96)


def _heart(d, w):
    """«Me gusta» sin marcar."""
    _line(d, _heart_points(), w)


def _heart_fill(d, w):
    """«Me gusta» marcado."""
    _poly(d, _heart_points())


_ICONS = dict(prev=_prev, next=_next, shuffle=_shuffle, repeat=_repeat, repeat1=_repeat1,
              infinity=_infinity, xfade=_xfade, queue=_queue, volume=_volume, search=_search,
              theme=_theme, downloads=_downloads, note=_note, back=_back, link=_link,
              more=_more, settings=_settings, tools=_tools, grid=_grid, list=_list,
              heart=_heart, heart_fill=_heart_fill, mini=_mini, expand=_expand,
              shrink=_shrink, stage=_stage, chevron_down=_chevron_down)


def names() -> list[str]:
    return list(_ICONS)


def icon_mask(name: str, size: int, stroke: float = 1.5) -> Image.Image:
    """Máscara en escala de grises (L) del icono; se colorea al pintarlo."""
    img = Image.new("L", (24 * SS, 24 * SS), 0)
    _ICONS[name](ImageDraw.Draw(img), max(1, round(stroke * SS)))
    return img.resize((size, size), Image.LANCZOS)
