"""Navegador del que se leen las cookies de YouTube, compartido por descargas y streaming.

Un único estado evita que la ventana use un navegador para bajar y otro para reproducir. Cada
cambio sube la generación: los hilos que ya tienen una instancia de yt-dlp la recrean con las
cookies nuevas sin reiniciar la aplicación.
"""
import threading

from .config import COOKIES_FROM_BROWSER

_lock = threading.Lock()
_browser = (COOKIES_FROM_BROWSER or "").strip().lower()
_generation = 0


def configure(browser: str | None) -> None:
    """Cambia el navegador de cookies para las próximas peticiones."""
    global _browser, _generation
    with _lock:
        _browser = (browser or "").strip().lower()
        _generation += 1


def browser() -> str:
    with _lock:
        return _browser


def generation() -> int:
    """Sube cada vez que cambia el navegador: sirve para invalidar instancias de yt-dlp."""
    with _lock:
        return _generation


def ydl_options() -> dict:
    """Parámetros de yt-dlp para leer las cookies del navegador, si hay uno elegido."""
    current = browser()
    return {"cookiesfrombrowser": (current,)} if current else {}
