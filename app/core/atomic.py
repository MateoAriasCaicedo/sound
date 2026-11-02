"""Escrituras atómicas de archivos pequeños de estado.

Un corte de luz a mitad de `write_text` deja un JSON truncado. Escribir en un temporal y
reemplazar el original evita perder preferencias, la sesión o la caché de volumen.
"""
import os
from pathlib import Path


def write_text_atomic(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
