"""Sound: descarga y reproduce música de YouTube Music en el escritorio."""

__version__ = "1.0.2"

# Debe correr antes de que cualquier submódulo importe yt_dlp: en la app empaquetada una
# actualización descargada por el usuario vive en su carpeta de datos y gana a la incluida.
try:  # pragma: no cover - solo cambia algo en el ejecutable congelado
    from .core.config import activate_vendor as _activate_vendor
    from .core.config import ensure_node_path as _ensure_node_path

    _ensure_node_path()
    _activate_vendor()
except Exception:  # noqa: BLE001 - nunca impedir que la app arranque por una actualización
    pass
