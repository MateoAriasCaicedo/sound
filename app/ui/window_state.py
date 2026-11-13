"""Tamaño, posición y estado (maximizada) de la ventana entre sesiones.

Al cerrar se guarda la geometría «normal» (la de antes de maximizar) y si estaba maximizada. Al
abrir se comprueba que siga cabiendo en alguna pantalla: si se desconectó el monitor donde
estaba, o cambió la resolución, la ventana vuelve a una pantalla visible en vez de abrirse
fuera de la vista.
"""
from __future__ import annotations

import re

Rect = tuple[int, int, int, int]  # izquierda, arriba, derecha, abajo

_GEOMETRY = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")


def parse(geometry: str | None) -> tuple[int, int, int, int] | None:
    """'1120x720+40+20' → (ancho, alto, x, y); None si no se entiende."""
    found = _GEOMETRY.match((geometry or "").strip())
    if not found:
        return None
    w, h, x, y = (int(v.replace("+-", "-")) for v in found.groups())
    return w, h, x, y


def fit(saved: tuple[int, int, int, int], areas: list[Rect], min_w: int,
        min_h: int) -> tuple[int, int, int, int]:
    """La geometría guardada, ajustada a las pantallas que hay ahora.

    Se queda en el monitor donde cae su barra de título; si no cae en ninguno, va centrada al
    primero. El tamaño nunca pasa del área de trabajo (sin la barra de tareas) ni baja del
    mínimo de la ventana.
    """
    w, h, x, y = saved
    if not areas:
        return max(w, min_w), max(h, min_h), x, y
    # el monitor donde está la barra de título (lo que hace falta ver para moverla)
    grip = (x + min(w, 200) // 2, y + 10)
    area = next((a for a in areas if a[0] <= grip[0] < a[2] and a[1] <= grip[1] < a[3]), None)
    lost = area is None
    area = area or areas[0]
    left, top, right, bottom = area
    w = max(min_w, min(w, right - left))
    h = max(min_h, min(h, bottom - top))
    if lost:
        x, y = left + (right - left - w) // 2, top + (bottom - top - h) // 2
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    return w, h, x, y


def work_areas() -> list[Rect]:
    """Áreas de trabajo de cada monitor (Windows); la principal primero. [] fuera de Windows."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    areas: list[tuple[bool, Rect]] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def callback(monitor, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            areas.append((bool(info.dwFlags & 1), (r.left, r.top, r.right, r.bottom)))
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    except Exception:  # noqa: BLE001 - sin la API, se usa la pantalla de Tk
        return []
    areas.sort(key=lambda item: not item[0])  # la principal primero
    return [rect for _primary, rect in areas]
