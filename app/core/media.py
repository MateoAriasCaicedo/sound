"""Comprobaciones sobre el audio ya descargado: duración real frente a la anunciada.

Un archivo truncado se abre sin error y «termina» a los pocos segundos. Sin esta comprobación el
reproductor lo daría por acabado y saltaría a la siguiente canción en cadena.

Las tolerancias viven aquí para que la descarga, el arranque y la reproducción no usen márgenes
distintos: un archivo que se considera bueno al descargarlo no debe borrarse después por otra
regla más estricta.
"""
from pathlib import Path
from time import time

SLACK_S = 15.0       # margen absoluto: los metadatos de YouTube bailan unos segundos
SLACK_PCT = 0.20     # margen relativo: un 20 % de la duración esperada


def duration_mismatch(expected_seconds: float | None, actual_seconds: float | None) -> bool:
    """¿La duración real se aleja demasiado de la anunciada?

    Se usa tanto al terminar una descarga como al revisar un archivo ya existente. Solo se
    pronuncia cuando hay datos de los dos lados; si falta cualquiera, se deja pasar.
    """
    try:
        expected = float(expected_seconds or 0)
        actual = float(actual_seconds or 0)
    except (TypeError, ValueError):
        return False
    if expected <= 0 or actual <= 0:
        return False
    return abs(actual - expected) > max(SLACK_S, expected * SLACK_PCT)


def is_truncated(expected_seconds: float | None, actual_ms: int | None) -> bool:
    """¿El audio real es mucho más corto que la duración que anuncia la canción?

    Solo importa que falte audio, no que sobre: un archivo algo más largo puede incluir una
    introducción o un silencio final y no está dañado.
    """
    try:
        expected_ms = float(expected_seconds or 0) * 1000
        actual = int(actual_ms or 0)
    except (TypeError, ValueError):
        return False
    if expected_ms <= 0 or actual <= 0:
        return False
    return expected_ms - actual > max(SLACK_S * 1000, expected_ms * SLACK_PCT)


def quarantine(path: str | Path) -> Path | None:
    """Aparta un archivo dudoso en vez de borrarlo. Devuelve la ruta nueva, o None si no pudo.

    Nunca se destruyen datos del usuario de forma automática: si un archivo no supera la
    comprobación se renombra con la marca `.damaged-<marca>` y se deja junto al original. Así la
    biblioteca puede volver a descargarlo sin perder la copia anterior y el usuario decide cuándo
    borrarla.
    """
    path = Path(path)
    stamp = int(time())
    for attempt in range(100):
        suffix = f".damaged-{stamp}" + (f"-{attempt}" if attempt else "")
        target = path.with_name(f"{path.stem}{suffix}{path.suffix}")
        if target.exists():  # nunca pisar una copia anterior
            continue
        try:
            path.rename(target)
            return target
        except FileExistsError:
            continue
        except OSError:
            return None
    return None
