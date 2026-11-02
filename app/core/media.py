"""Comprobaciones sobre el audio ya descargado: duración real frente a la anunciada.

Un archivo truncado se abre sin error y «termina» a los pocos segundos. Sin esta comprobación el
reproductor lo daría por acabado y saltaría a la siguiente canción en cadena.
"""

TRUNCATED_SLACK_S = 15      # margen absoluto: metadatos de YouTube a veces bailan unos segundos
TRUNCATED_SLACK_PCT = 0.20  # margen relativo: un 20 % de la duración esperada


def is_truncated(expected_seconds: float | None, actual_ms: int | None) -> bool:
    """¿El audio real es mucho más corto que la duración que anuncia la canción?

    Solo se pronuncia cuando hay datos de los dos lados; si falta cualquiera, no hay nada que
    comparar y se deja pasar (mejor no parar una canción por una duda).
    """
    try:
        expected_ms = float(expected_seconds or 0) * 1000
        actual = int(actual_ms or 0)
    except (TypeError, ValueError):
        return False
    if expected_ms <= 0 or actual <= 0:
        return False
    slack = max(TRUNCATED_SLACK_S * 1000, expected_ms * TRUNCATED_SLACK_PCT)
    return expected_ms - actual > slack
