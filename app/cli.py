import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests

from . import cli_library
from .core import config, downloader, genres, health, importer, library, log, loudness, prompt, ytsearch
from .core.errors import friendly

GB = 1024**3


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sound")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("buscar", help="buscar canciones")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=10)

    sub.add_parser("generos", help="listar los géneros oficiales de YouTube Music")

    s = sub.add_parser("genero", help="descargar canciones variadas de un género")
    s.add_argument("genero")
    s.add_argument("-n", type=int, default=20, help="cuántas canciones nuevas")
    s.add_argument("--por-artista", type=int, default=3, help="máximo de canciones por artista")
    s.add_argument("--ver", action="store_true", help="mostrar la selección sin descargar")
    s.add_argument("--incluir-versiones", action="store_true",
                   help="incluir mixes, covers, directos y otras versiones no originales")

    s = sub.add_parser("prompt", help="pedir música con una frase, p. ej. 'música de los 80 en español'")
    s.add_argument("frase")
    s.add_argument("-n", type=int, default=50, help="cuántas canciones nuevas (por defecto 50)")
    s.add_argument("--por-artista", type=int, default=3, help="máximo de canciones por artista")
    s.add_argument("--ver", action="store_true", help="mostrar la selección sin descargar")
    s.add_argument("--incluir-versiones", action="store_true",
                   help="incluir mixes, covers, directos y otras versiones no originales")

    s = sub.add_parser("importar", help="importar un álbum, playlist o artista desde una URL")
    s.add_argument("url", help="enlace de YouTube Music o identificador (PL…, MPREb_…, UC…, vídeo)")
    s.add_argument("-n", type=int, default=200, help="máximo de canciones (por defecto 200)")
    s.add_argument("--genero", help="carpeta de género (por defecto, sueltas)")
    s.add_argument("--ver", action="store_true", help="mostrar la selección sin descargar")

    sub.add_parser("reanudar", help="continuar la cola pendiente")
    sub.add_parser("reintentar", help="volver a intentar las descargas fallidas")
    sub.add_parser("estado", help="ver cuántas hay pendientes, hechas y fallidas")
    s = sub.add_parser("exportar", help="exportar la biblioteca a una lista M3U")
    s.add_argument("ruta", nargs="?", default="biblioteca.m3u", help="archivo de destino")
    sub.add_parser("limpiar", help="quitar de la biblioteca las canciones cuyo archivo ya no existe")
    s = sub.add_parser("normalizar", help="medir el volumen de la biblioteca (necesita ffmpeg)")
    s.add_argument("-n", type=int, help="medir solo las primeras N canciones")
    sub.add_parser("actualizar", help="actualizar yt-dlp y ytmusicapi (se rompen cuando YouTube cambia)")
    sub.add_parser("diagnostico", help="comprobar Node, ffmpeg, internet, cookies, datos y espacio")
    s = sub.add_parser("recuperar", help="volver a encolar pistas de un informe de recuperación")
    s.add_argument("--desde", default=str(config.DATA_DIR / "recuperacion-biblioteca.json"),
                   help="informe JSON con reproducciones (por defecto, el de la carpeta de datos)")
    s.add_argument("--si", action="store_true", help="no pedir confirmación")
    cli_library.add_commands(sub)  # historial, top, duplicados, editar

    args = p.parse_args(argv)
    if args.cmd in cli_library.COMMANDS:
        return cli_library.run(args)

    if args.cmd == "buscar":
        for t in ytsearch.search_songs(args.query, args.n):
            print(f"{t['video_id']}  {t['artist']} - {t['title']}")
    elif args.cmd == "generos":
        print("\n".join(genres.sidebar_genres()))  # solo los géneros del sidebar
    elif args.cmd == "genero":
        if genres.resolve(args.genero) is None:
            print(f"'{args.genero}' no es un género oficial de YouTube Music; uso la búsqueda libre.")
        discarded: list = []
        known_ids, known_songs = genres.known_from_library()  # para traer solo novedades
        label, tracks = genres.select_tracks(
            args.genero, args.n, args.por_artista, strict=not args.incluir_versiones,
            rejected=discarded, skip_ids=known_ids, skip_songs=known_songs,
        )
        artists = {t["artist"] for t in tracks}
        print(f"Género: {label} - {len(tracks)} canciones nuevas de {len(artists)} artistas")
        if len(tracks) < args.n:
            print(f"Pediste {args.n} y hay {len(tracks)}: el resto ya lo tienes o no es la versión original.")
        if discarded:
            reasons = Counter(reason.split(":")[0] for _, reason in discarded)
            summary = ", ".join(f"{r}: {c}" for r, c in reasons.most_common())
            print(f"Descartadas {len(discarded)} que no son la versión original ({summary})")
        if args.ver:
            for t in tracks:
                print(f"  {t['artist']} - {t['title']}")
            for t, reason in discarded:
                print(f"  [descartada: {reason}] {t['artist']} - {t['title']}")
            return

        added = library.enqueue(tracks, label)
        print(f"{added} nuevas en la cola ({len(tracks) - added} ya estaban)")
        run()
    elif args.cmd == "prompt":
        print(f"Entendí  ->  {prompt.parse(args.frase).describe()}")
        known_ids, known_songs = genres.known_from_library()  # para traer solo novedades

        def progress(found: int, wanted: int, done: int, total: int) -> None:
            print(f"  búsqueda {done}/{total}: {found}/{wanted} canciones", flush=True)

        result = prompt.select_by_prompt(
            args.frase, args.n, args.por_artista, strict=not args.incluir_versiones,
            skip_ids=known_ids, skip_songs=known_songs, on_progress=progress,
        )
        print(result.summary(args.n))
        if not result.tracks:
            print("No encontré canciones que cumplan. Prueba con otra frase o sin tantas condiciones.")
            return
        if args.ver:
            for t in result.tracks:
                print(f"  {t.get('year') or '    '}  {t['artist']} - {t['title']}")
            return
        added = library.enqueue(result.tracks, result.label)
        print(f"{added} nuevas en la cola ({len(result.tracks) - added} ya estaban)")
        run()
    elif args.cmd == "importar":
        try:
            label, tracks = importer.fetch(args.url, args.n)
        except importer.ImportFailure as exc:
            raise SystemExit(f"Error: {exc}") from None
        print(f"{label}: {len(tracks)} canciones")
        if args.ver:
            for t in tracks:
                print(f"  {t['artist']} - {t['title']}")
            return
        added = library.enqueue(tracks, args.genero)
        print(f"{added} nuevas en la cola ({len(tracks) - added} ya estaban)")
        run()
    elif args.cmd == "reanudar":
        run()
    elif args.cmd == "reintentar":
        print(f"{library.retry_failed()} fallidas vuelven a la cola")
        run()
    elif args.cmd == "estado":
        counts = library.queue_counts()
        print("  ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "cola vacía")
        for row in library.failed():
            print(f"  FALLÓ {row['artist']} - {row['title']} ({row['attempts']} intentos): {row['error']}")
        no_cover = library.without_cover()
        if no_cover:
            print(f"{len(no_cover)} canciones descargadas sin carátula, por ejemplo:")
            for row in no_cover[:5]:
                print(f"  {row['artist']} - {row['title']}")
        missing = library.missing_files()
        if missing:
            print(f"{len(missing)} canciones ya no están en disco (límpialas con: limpiar)")
    elif args.cmd == "exportar":
        try:
            count = library.export_m3u(Path(args.ruta))
        except OSError as exc:
            raise SystemExit(f"Error: no se pudo escribir {args.ruta}: {exc}") from None
        print(f"{count} canciones exportadas a {args.ruta}")
    elif args.cmd == "limpiar":
        count = library.remove_missing()
        print(f"{count} canciones quitadas de la biblioteca" if count
              else "Todas las canciones tienen su archivo")
    elif args.cmd == "normalizar":
        if not loudness.available():
            raise SystemExit("Error: ffmpeg no está en el PATH; sin él no se puede medir el volumen")
        tracks = library.all_tracks()
        if args.n:
            tracks = tracks[:args.n]
        pending = [t for t in tracks
                   if loudness.cached_gain(t["video_id"], Path(t["path"])) is None]
        print(f"Midiendo {len(pending)} canciones…" if pending else "Todas ya estaban medidas")
        if pending:
            def report(track, gain):
                print(f"  {gain:+5.1f} dB  {track['artist']} - {track['title']}", flush=True)
            print(f"{loudness.scan(pending, report)} canciones medidas")
    elif args.cmd == "actualizar":
        sys.exit(health.update_components())
    elif args.cmd == "diagnostico":
        for check in health.diagnostics(cookies=downloader.cookies_browser()):
            icon = {"ok": "OK ", "warn": "AVISO", "fail": "FALLA"}.get(check["status"], "?")
            print(f"{icon:5} {check['name']}: {check['detail']}")
        print(f"\nDatos: {config.DATA_DIR}\nMúsica: {config.MUSIC_DIR}\nRegistro: {config.LOG_PATH}")
    elif args.cmd == "recuperar":
        _recuperar(args)


def _recuperar(args: argparse.Namespace) -> None:
    """Reencola las reproducciones de un informe de recuperación (p. ej. tras una migración)."""
    path = Path(args.desde)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: no se pudo leer {path}: {exc}") from None
    seen: set[str] = set()
    tracks = []
    for row in data.get("reproducciones", []) if isinstance(data, dict) else []:
        video_id = row.get("video_id") if isinstance(row, dict) else None
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        tracks.append({"video_id": video_id,
                       "title": row.get("title") or "Sin título",
                       "artist": row.get("artist") or "Artista desconocido"})
    if not tracks:
        print("El informe no tiene reproducciones que se puedan reencolar.")
        return
    if not args.si:
        answer = input(f"¿Encolar {len(tracks)} canciones del informe? [s/N] ")
        if answer.strip().lower() not in {"s", "si", "sí", "y", "yes"}:
            print("Cancelado.")
            return
    added = library.enqueue(tracks, None)
    print(f"{added} canciones en la cola ({len(tracks) - added} ya estaban). "
          "Continúa con: sound reanudar")


def run() -> None:
    for problem in health.problems():
        print(f"Aviso: {problem}", flush=True)

    pending = len(library.pending())
    needed, free = pending * downloader.EST_TRACK_BYTES, downloader.free_bytes()
    if pending and free < needed + downloader.MIN_FREE_BYTES:
        print(f"Aviso: hacen falta ~{needed / GB:.1f} GB y hay {free / GB:.1f} GB libres. "
              "La cola se detendrá a tiempo y podrás reanudarla.", flush=True)

    def report(t: dict, path, err) -> None:
        status = "OK " if not err else "ERR"
        detail = f" ({friendly(err)})" if err else ""
        print(f"{status} {t['artist']} - {t['title']}{detail}", flush=True)

    def retrying(t: dict, attempt: int, delay: float, err: Exception) -> None:
        print(f"... reintento {attempt} de {t['title']} en {delay:.0f}s "
              f"({friendly(err)[:80]})", flush=True)

    try:
        counts = downloader.run_queue(on_done=report, on_retry=retrying)
    except KeyboardInterrupt:
        # Lo que no terminó sigue 'pending' en la base; los hilos en espera no se pueden cortar limpio.
        print("\nPausado. Continúa con: reanudar", flush=True)
        os._exit(130)
    print("Resumen:", "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if counts.get("pending") and downloader.free_bytes() < downloader.MIN_FREE_BYTES:
        print(f"Se detuvo por poco espacio en disco ({counts['pending']} pendientes). "
              "Libera espacio y usa: reanudar")
    if counts.get("failed"):
        print("Ver fallidas: estado   |   volver a intentarlas: reintentar")
        if health.looks_outdated(row["error"] or "" for row in library.failed()):
            print("Los errores parecen de un yt-dlp desactualizado. Prueba: actualizar, y luego: reintentar")


def entry() -> None:
    try:
        config.prepare()
    except OSError as exc:
        print(f"Error: no se pudo crear la carpeta de datos {config.DATA_DIR}: {exc}",
              file=sys.stderr)
        raise SystemExit(1) from None
    log.setup()
    try:
        report = config.migrate_legacy()
    except Exception as exc:  # noqa: BLE001 - una migración rota no debe impedir usar la CLI
        log.failure(log.get("start"), "la migración de datos falló", exc)
        report = {"moved": [], "failed": [str(exc)]}
    for path in report["moved"]:
        log.get("start").info("migrado a la carpeta de datos: %s", path)
    for failure in report["failed"]:
        log.get("start").warning("no se pudo migrar: %s", failure)
    log.session_header()
    try:
        main()
    except requests.RequestException as exc:
        print("No pude conectar con YouTube Music (¿sin internet?). "
              "Inténtalo de nuevo cuando vuelva la conexión.", file=sys.stderr)
        print(f"  Detalle: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 - la CLI no debe terminar con una traza críptica
        log.failure(log.get("cli"), "fallo no controlado", exc)
        print(f"Error: {friendly(exc)}", file=sys.stderr)
        print(f"  Detalle completo en: {config.LOG_PATH}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    entry()
