"""Comandos de la CLI sobre la biblioteca ya descargada: historial, duplicados y edición.

Van aparte de cli.py (que trata de descargar) para no mezclar las dos cosas.
"""
import argparse
from datetime import datetime, timezone

from .core import duplicates, history, library, metadata

COMMANDS = ("historial", "top", "duplicados", "editar")
MAX_CANDIDATES = 8


def add_commands(sub) -> None:
    s = sub.add_parser("historial", help="lo último que escuchaste")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--limpiar", action="store_true", help="borrar reproducciones antiguas")
    s.add_argument("--dias", type=int, default=730, help="con --limpiar: antigüedad a conservar")
    s = sub.add_parser("top", help="lo más escuchado")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--dias", type=int, help="solo los últimos N días")

    s = sub.add_parser("duplicados", help="buscar la misma canción repetida con distinto id")
    s.add_argument("--borrar", action="store_true", help="borrar las repetidas, dejando la mejor de cada grupo")
    s.add_argument("--si", action="store_true", help="con --borrar: no pedir confirmación")

    s = sub.add_parser("editar", help="cambiar las etiquetas de una canción descargada")
    s.add_argument("cancion", help="id o texto del título, artista o álbum")
    s.add_argument("--titulo")
    s.add_argument("--artista")
    s.add_argument("--album", help='con "" quita el álbum')
    s.add_argument("--genero", help='con "" quita el género')
    s.add_argument("--anio", help='con "" quita el año')


def run(args: argparse.Namespace) -> None:
    try:
        {"historial": _recent, "top": _top,
         "duplicados": _duplicates, "editar": _edit}[args.cmd](args)
    except metadata.MetadataError as exc:
        raise SystemExit(f"Error: {exc}") from None


def _label(track: dict) -> str:
    return f"{track['artist']} - {track['title']}"


def _pick(ref: str) -> dict | None:
    """Una canción de la biblioteca por id o por texto; avisa si no hay o hay varias."""
    found = library.find_tracks(ref)
    if len(found) == 1:
        return found[0]
    if not found:
        print(f"  no encontré '{ref}' en tu biblioteca")
        return None
    print(f"  '{ref}' coincide con {len(found)} canciones; usa el id:")
    for track in found[:MAX_CANDIDATES]:
        print(f"    {track['video_id']}  {_label(track)}")
    if len(found) > MAX_CANDIDATES:
        print(f"    … y {len(found) - MAX_CANDIDATES} más")
    return None


def _local(timestamp: str) -> str:
    """La hora de SQLite (UTC) en la zona horaria del usuario."""
    moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


# ---------- historial ----------
def _recent(args: argparse.Namespace) -> None:
    if getattr(args, "limpiar", False):
        removed = history.prune(args.dias)
        print(f"{removed} reproducciones antiguas borradas" if removed
              else "No había reproducciones tan antiguas")
        return
    rows = history.recent(args.n)
    if not rows:
        print("Todavía no hay reproducciones registradas.")
    for row in rows:
        times = f"  (x{row['plays']})" if row["plays"] > 1 else ""
        print(f"  {_local(row['last_played'])}  {row['artist']} - {row['title']}{times}")


def _top(args: argparse.Namespace) -> None:
    rows = history.most_played(args.n, args.dias)
    if not rows:
        print("Todavía no hay reproducciones registradas.")
    for row in rows:
        print(f"  {row['plays']:>4}x  {row['artist']} - {row['title']}")


# ---------- duplicados ----------
def _duplicates(args: argparse.Namespace) -> None:
    groups = duplicates.find()
    if not groups:
        print("No hay canciones repetidas.")
        return
    extras = sum(len(g.extras) for g in groups)
    for group in groups:
        print(_label(group.keep))
        print(f"  conservar  {group.keep['video_id']}  {_details(group.keep)}")
        for track in group.extras:
            print(f"  repetida   {track['video_id']}  {_details(track)}  {track['path']}")
    print(f"{extras} repetidas en {len(groups)} grupos")
    if not args.borrar:
        print("Para borrar las repetidas (y dejar la marcada como «conservar»): duplicados --borrar")
        return
    if not args.si and not _confirm(f"¿Borrar {extras} archivos repetidos del disco? [s/N] "):
        print("Cancelado, no se borró nada.")
        return
    removed, failed = duplicates.remove_extras(groups)
    print(f"{removed} borradas")
    for track, reason in failed:
        print(f"  no se pudo borrar {_label(track)}: {reason}")


def _details(track: dict) -> str:
    parts = [track.get("album") or "sin álbum", str(track.get("year") or "sin año"),
             "con carátula" if track.get("has_cover") else "sin carátula"]
    return ", ".join(parts)


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("s", "si", "sí", "y", "yes")
    except EOFError:  # sin terminal interactiva: ante la duda, no borrar
        return False


# ---------- edición ----------
def _edit(args: argparse.Namespace) -> None:
    track = _pick(args.cancion)
    if track is None:
        raise SystemExit(1)
    given = {"title": args.titulo, "artist": args.artista, "album": args.album,
             "genre": args.genero, "year": args.anio}
    changes = {field: value for field, value in given.items() if value is not None}
    if not changes:
        raise SystemExit("Error: indica qué cambiar (--titulo, --artista, --album, --genero, --anio)")
    before = metadata.edit(track["video_id"], {})
    after = metadata.edit(track["video_id"], changes)
    print(f"Actualizada: {_label(after)}")
    for field in changes:
        print(f"  {field}: {before.get(field) or '—'}  ->  {after.get(field) or '—'}")
