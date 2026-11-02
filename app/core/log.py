"""Registro de eventos en un archivo rotativo (sound.log), para diagnosticar fallos a posteriori.

Los errores que la interfaz o el servicio capturan para seguir funcionando (sin internet, vídeo
no disponible, API cambiada) dejan aquí su traza completa; si no, un fallo de programación se
confundiría con uno de red y no quedaría rastro.
"""
import logging
from logging.handlers import RotatingFileHandler

from .config import LOG_PATH

NAME = "sound"
_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{NAME}.{name}" if name else NAME)


def failure(logger: logging.Logger, what: str, exc: BaseException) -> None:
    """Registra un fallo que el programa absorbe para seguir funcionando.

    Un fallo de red es esperable (sin internet): basta una línea. Cualquier otro lleva la traza
    completa, porque lo más probable es un error de programación y no debe pasar desapercibido.
    """
    if isinstance(exc, OSError):  # incluye requests.RequestException y los cortes de conexión
        logger.info("%s: %s", what, exc)
    else:
        logger.warning("%s", what, exc_info=exc)


def setup(level: int = logging.INFO) -> logging.Logger:
    """Activa el registro en archivo. Se puede llamar varias veces: no duplica manejadores."""
    root = logging.getLogger(NAME)
    root.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        try:
            handler = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        except OSError:
            return root  # sin permiso de escritura: el programa funciona igual, sin registro
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    return root
