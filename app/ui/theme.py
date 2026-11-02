"""Paletas claro/oscuro, color derivado de la portada y detalles de ventana de Windows.

Base neutra y un único acento. El acento y el tinte de la ventana salen de la canción que suena:
de su portada o, si no tiene, de un color estable calculado a partir de su identificador.
"""
import colorsys
import hashlib

LIGHT = dict(bg="#f8f8f9", panel="#f0f0f2", raised="#ffffff", fg="#17171a", dim="#6b6b74",
             faint="#9c9ca6", accent="#e0482b", accent_fg="#ffffff", sel="#e7e7eb",
             hover="#efeff2", line="#e2e2e6", field="#ffffff", danger="#c93a3a")
DARK = dict(bg="#101013", panel="#17171b", raised="#202026", fg="#eeeef2", dim="#92929c",
            faint="#63636e", accent="#ff6a4d", accent_fg="#16110f", sel="#232329",
            hover="#1a1a1f", line="#26262d", field="#1b1b20", danger="#ee6a66")

FONT_FAMILY = "Segoe UI"


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


# ---------- color ----------
def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(r: float, g: float, b: float) -> str:
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


def blend(a: str, b: str, t: float) -> str:
    """Mezcla dos colores #rrggbb: t=0 da `a`, t=1 da `b`."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def luminance(color: str) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """Contraste WCAG entre dos colores (1 a 21)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(color: str, bg: str, ratio: float = 4.5) -> str:
    """Aclara u oscurece `color` lo justo para que se lea sobre `bg`."""
    target = "#ffffff" if luminance(bg) < 0.5 else "#000000"
    for i in range(11):
        c = blend(color, target, i / 10)
        if contrast(c, bg) >= ratio:
            return c
    return target


def text_on(color: str) -> str:
    """Negro o blanco, lo que mejor se lea sobre `color`."""
    return "#ffffff" if contrast("#ffffff", color) >= contrast("#111114", color) else "#111114"


def vivid(color: str) -> str:
    """Sube saturación y brillo: el promedio de una portada apagada daría un acento sucio."""
    r, g, b = _rgb(color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return _hex(*(c * 255 for c in colorsys.hsv_to_rgb(h, max(s, 0.55), max(v, 0.85))))


def seed_color(seed: str) -> str:
    """Color vivo y estable para una canción sin portada."""
    hue = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:6], 16) / 0xffffff
    return _hex(*(v * 255 for v in colorsys.hsv_to_rgb(hue, 0.62, 0.92)))


def dominant_color(img) -> str | None:
    """Color más vivo y abundante de una imagen de Pillow. None si es casi gris."""
    data = img.convert("RGB").resize((24, 24)).tobytes()
    buckets: dict[int, list[float]] = {}
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        weight = s * s * v
        if weight < 0.04:
            continue
        b_ = buckets.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0.0])
        b_[0] += weight
        b_[1] += r * weight
        b_[2] += g * weight
        b_[3] += b * weight
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda x: x[0])
    if best[0] < 1.0:  # unos pocos píxeles de color en una portada gris
        return None
    return _hex(best[1] / best[0], best[2] / best[0], best[3] / best[0])


# ---------- Windows ----------
def enable_dpi_awareness() -> None:
    """Texto nítido en pantallas escaladas. Hay que llamarlo antes de crear la ventana."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001 - no es Windows o ya estaba fijado
        pass


def system_is_dark() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def style_titlebar(window, dark: bool, caption: str | None = None) -> None:
    """Barra de título acorde al tema y, en Windows 11, del mismo color que la ventana."""
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 en builds antiguas)
            if set_attr(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        if caption:
            r, g, b = _rgb(caption)
            colorref = ctypes.c_int(r | g << 8 | b << 16)
            set_attr(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # DWMWA_CAPTION_COLOR
    except Exception:  # noqa: BLE001 - cosmético: no es Windows o no hay DWM
        pass
