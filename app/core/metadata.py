"""Edición de etiquetas de una canción ya descargada: título, artista, álbum, género y año.

Cambia a la vez las etiquetas del archivo .m4a y la fila de la biblioteca. No renombra ni mueve el
archivo: su nombre y carpeta siguen siendo los de la descarga.
"""
from contextlib import closing
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp4 import MP4

from . import library
from . import log as _log

log = _log.get("metadata")

FIELDS = ("title", "artist", "album", "genre", "year")
# campo -> átomo MP4
_ATOMS = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "genre": "\xa9gen",
          "year": "\xa9day"}
MIN_YEAR, MAX_YEAR = 1000, 2100


class MetadataError(Exception):
    """Datos inválidos o archivo inaccesible. El mensaje es apto para mostrarlo."""


def _validate(changes: dict) -> dict:
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise MetadataError(f"campos no editables: {', '.join(sorted(unknown))}")
    clean = {}
    for field, value in changes.items():
        if field in ("title", "artist"):
            value = (value or "").strip()
            if not value:
                raise MetadataError(f"{'el título' if field == 'title' else 'el artista'} no puede estar vacío")
        elif field == "year":
            if value in (None, ""):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise MetadataError(f"año inválido: {value!r}") from None
                if not MIN_YEAR <= value <= MAX_YEAR:
                    raise MetadataError(f"año fuera de rango ({MIN_YEAR}-{MAX_YEAR}): {value}")
        else:  # álbum y género: vacío = quitar la etiqueta
            value = (value or "").strip() or None
        clean[field] = value
    return clean


def _write_tags(path: Path, changes: dict) -> None:
    try:
        audio = MP4(path)
        for field, value in changes.items():
            atom = _ATOMS[field]
            if value is None:
                audio.pop(atom, None)
            else:
                audio[atom] = str(value)
        if "artist" in changes:
            audio["aART"] = changes["artist"].split(",")[0]  # artista del álbum, como al descargar
        audio.save()
    except (MutagenError, OSError) as exc:
        raise MetadataError(f"no se pudieron escribir las etiquetas de {path.name}: {exc}") from exc


def edit(video_id: str, changes: dict, db_path: Path | None = None) -> dict:
    """Aplica `changes` ({campo: valor}) a la canción y devuelve su fila ya actualizada.

    Primero se escribe el archivo y, solo si salió bien, la base de datos: un fallo de etiquetas no
    deja la biblioteca diciendo algo que el archivo no dice. Si `changes` está vacío no hace nada.
    """
    clean = _validate(changes)
    with closing(library.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT video_id, title, artist, album, genre, duration, path, year FROM tracks"
            " WHERE video_id = ?", (video_id,)).fetchone()
    if row is None:
        raise MetadataError(f"la canción {video_id} no está en la biblioteca")
    track = dict(row)
    if not clean:
        return track
    path = Path(track["path"])
    if not path.exists():
        raise MetadataError(f"el archivo ya no existe: {path}")

    _write_tags(path, clean)
    with closing(library.connect(db_path)) as conn, conn:
        conn.execute(
            f"UPDATE tracks SET {', '.join(f'{field} = ?' for field in clean)} WHERE video_id = ?",
            (*clean.values(), video_id),
        )
    log.info("editada %s: %s", video_id, ", ".join(sorted(clean)))
    return {**track, **clean}
