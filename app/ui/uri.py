"""URI de archivo que Windows (WinRT MediaPlayer) abre aunque la ruta tenga tildes o eñes."""
from pathlib import Path
from urllib.parse import quote


def file_uri(path: str | Path) -> str:
    """`file:///C:/…` con los caracteres especiales codificados y las letras no ASCII tal cual.

    `Path.as_uri()` codifica también 'á' como '%C3%A1', y el reproductor de Windows rechaza esa
    forma con «error de medios 4» (formato no soportado) aunque el archivo esté sano: las
    canciones con acentos en el nombre no sonaban. WinRT codifica por su cuenta lo que no es ASCII,
    así que solo se codifica lo que sí lo es y tiene significado en una URI (espacio, # % ? …).
    """
    posix = Path(path).resolve().as_posix()
    encoded = "".join(c if ord(c) > 127 else quote(c, safe="/:") for c in posix)
    if encoded.startswith("//"):  # ruta de red: \\servidor\recurso
        return "file:" + encoded
    return "file:///" + encoded
