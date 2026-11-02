"""Traducción de los errores de yt-dlp a mensajes cortos y accionables.

La UI y la CLI nunca deben mostrar el volcado en inglés con códigos ANSI: el usuario necesita
saber si el vídeo ya no existe, si YouTube le está pidiendo cookies o si toca actualizar yt-dlp.
El texto original sigue completo en `sound.log`.
"""
import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SPACES = re.compile(r"\s+")

BOT_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm youre not a bot",
    "confirm you're not a bot",
    "confirm youre not a bot",
)
AGE_MARKERS = ("sign in to confirm your age", "age-restricted", "inappropriate for some users")
UNAVAILABLE_MARKERS = (
    "video unavailable", "video is unavailable", "video is not available", "video is private",
    "private video", "has been removed", "no longer available",
    "not available in your country", "members-only", "has been terminated",
)
THROTTLE_MARKERS = ("http error 403", "http error 429", "too many requests")
OUTDATED_MARKERS = (
    "unable to extract", "nsig extraction", "signature extraction",
    "no video formats found", "requested format is not available",
)
OFFLINE_MARKERS = (
    "timed out", "timeout", "connection", "network is unreachable", "temporary failure",
    "name or service not known", "getaddrinfo", "sin internet", "ssl",
)
COOKIE_HINT = ("Abre Descargas → «Cookies de YouTube» y elige el navegador donde tengas la "
               "sesión iniciada.")


def clean(text: object) -> str:
    """Quita colores y espacios repetidos del mensaje de yt-dlp."""
    return _SPACES.sub(" ", _ANSI.sub("", str(text or ""))).strip()


def is_bot_check(error: object) -> bool:
    text = clean(error).lower()
    return any(marker in text for marker in BOT_MARKERS)


def needs_cookies(error: object) -> bool:
    text = clean(error).lower()
    return is_bot_check(text) or any(marker in text for marker in AGE_MARKERS)


def friendly(error: object) -> str:
    """Mensaje en español para el usuario. El original se conserva en la cola y en el log."""
    text = clean(error)
    low = text.lower()
    if is_bot_check(low):
        return "YouTube pide verificar que no eres un bot. " + COOKIE_HINT
    if any(marker in low for marker in AGE_MARKERS):
        return "YouTube pide iniciar sesión para este vídeo. " + COOKIE_HINT
    if any(marker in low for marker in THROTTLE_MARKERS):
        return "YouTube está limitando las descargas. Espera unos minutos y reintenta."
    if any(marker in low for marker in OUTDATED_MARKERS):
        return ("yt-dlp está desactualizado para los cambios de YouTube. "
                "Pulsa «Actualizar yt-dlp» en Descargas.")
    if any(marker in low for marker in UNAVAILABLE_MARKERS):
        return "El vídeo ya no está disponible (privado, borrado o restringido por país)."
    if any(marker in low for marker in OFFLINE_MARKERS):
        return "No hay conexión con YouTube. Comprueba internet y reintenta."
    if "dura " in low and "se esperaban" in low:
        return "El archivo no coincide con la canción anunciada. Se volverá a intentar."
    text = text.removeprefix("ERROR: ").strip()
    return text[:200] if text else "La descarga falló por un motivo desconocido."
