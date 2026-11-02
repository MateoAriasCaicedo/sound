"""Comprobaciones del entorno. yt-dlp y ytmusicapi dependen de lo que YouTube cambie."""
import shutil
import subprocess
import sys
from typing import Callable, Iterable

COMPONENTS = ("yt-dlp", "ytmusicapi")

# Mensajes típicos de yt-dlp cuando YouTube cambió algo y la versión instalada ya no sirve.
OUTDATED_MARKERS = (
    "unable to extract",
    "nsig extraction",
    "signature extraction",
    "no video formats found",
    "requested format is not available",
)


def problems(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Problemas del entorno que harán fallar descargas. Lista vacía si todo está en orden."""
    found = []
    if not which("node"):
        found.append(
            "Node.js no está instalado o no está en el PATH: YouTube necesita un entorno "
            "JavaScript para resolver los enlaces y muchas descargas fallarán."
        )
    return found


def looks_outdated(errors: Iterable[str]) -> bool:
    """¿Alguno de los errores es de los que se arreglan actualizando yt-dlp?"""
    return any(marker in error.lower() for error in errors for marker in OUTDATED_MARKERS)


def update_components(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run, dry_run: bool = False
) -> int:
    """Actualiza yt-dlp y ytmusicapi en el entorno que ejecuta el programa. Devuelve el código de salida."""
    command = [sys.executable, "-m", "pip", "install", "--upgrade", *COMPONENTS]
    if dry_run:
        command.append("--dry-run")
    return run(command).returncode
