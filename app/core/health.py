"""Comprobaciones del entorno. yt-dlp y ytmusicapi dependen de lo que YouTube cambie."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable

from . import config, errors
from .atomic import write_text_atomic

COMPONENTS = ("yt-dlp", "ytmusicapi")
OUTDATED_MARKERS = errors.OUTDATED_MARKERS
PYPI_YTDLP = "https://pypi.org/pypi/yt-dlp/json"
MIN_FREE_GB = 1.0


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def problems(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Problemas del entorno que harán fallar descargas. Lista vacía si todo está en orden."""
    found = []
    if not which("node"):
        found.append(
            "Node.js no está instalado o no está en el PATH: YouTube necesita un entorno "
            "JavaScript para resolver los enlaces y muchas descargas fallarán."
        )
    return found


def looks_outdated(errors_: Iterable[str]) -> bool:
    """¿Alguno de los errores es de los que se arreglan actualizando yt-dlp?"""
    return any(marker in error.lower() for error in errors_ for marker in OUTDATED_MARKERS)


def needs_cookies(errors_: Iterable[object]) -> bool:
    """¿Alguno de los errores se resuelve con cookies de un navegador?"""
    return any(errors.needs_cookies(error) for error in errors_)


def update_components(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    dry_run: bool = False,
    frozen: bool | None = None,
) -> int:
    """Actualiza yt-dlp y ytmusicapi. Devuelve el código de salida (0 = bien).

    En desarrollo usa pip, que es lo natural en un entorno editable. En la app empaquetada no
    hay pip: se descarga el wheel oficial de yt-dlp en la carpeta de datos y se carga al
    reiniciar (ver `config.activate_vendor`). ytmusicapi cambia mucho menos y va incluido.
    """
    if (is_frozen() if frozen is None else frozen):
        return 0 if update_ytdlp_vendor(dry_run=dry_run) else 1
    command = [sys.executable, "-m", "pip", "install", "--upgrade", *COMPONENTS]
    if dry_run:
        command.append("--dry-run")
    return run(command).returncode


def update_ytdlp_vendor(
    fetch: Callable[..., object] | None = None,
    dry_run: bool = False,
) -> str | None:
    """Descarga el wheel más reciente de yt-dlp y lo deja listo para el próximo arranque.

    Devuelve la versión instalada, o None si no se pudo. No toca la instalación actual: escribe
    una carpeta nueva y mueve el puntero `current.json` de forma atómica.
    """
    import requests

    fetch = fetch or requests.get
    try:
        response = fetch(PYPI_YTDLP, timeout=30)
        response.raise_for_status()
        data = response.json()
        version = str(data["info"]["version"])
        wheel = next(
            item for item in data["urls"]
            if item.get("packagetype") == "bdist_wheel"
            and str(item.get("filename", "")).endswith("py3-none-any.whl")
        )
        url = wheel["url"]
        digest = (wheel.get("digests") or {}).get("sha256")
        if dry_run:
            return version
        download = fetch(url, timeout=120)
        download.raise_for_status()
        payload = download.content
    except (KeyError, StopIteration, TypeError, ValueError, OSError, requests.RequestException):
        return None
    if digest and hashlib.sha256(payload).hexdigest() != digest:
        return None

    destination = config.VENDOR_DIR / f"yt_dlp-{version}-{int(time.time())}"
    try:
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as tmp:
            wheel_path = Path(tmp) / "yt-dlp.whl"
            wheel_path.write_bytes(payload)
            with zipfile.ZipFile(wheel_path) as archive:
                archive.extractall(tmp)
            package = Path(tmp) / "yt_dlp"
            if not (package / "__init__.py").exists():
                return None
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package, destination / "yt_dlp")
        write_text_atomic(config.VENDOR_DIR / "current.json",
                          json.dumps({"path": str(destination), "version": version}))
    except (OSError, zipfile.BadZipFile):
        return None
    _clean_vendor(destination)
    return version


def _clean_vendor(current: Path) -> None:
    """Borra versiones anteriores, salvo la que acaba de quedar activa."""
    try:
        for item in config.VENDOR_DIR.iterdir():
            if item.is_dir() and item != current:
                shutil.rmtree(item, ignore_errors=True)
    except OSError:
        pass


def _is_online(timeout: float = 3.0) -> bool:
    try:
        socket.create_connection(("music.youtube.com", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def _versions() -> tuple[str, str]:
    try:
        import yt_dlp

        ytdlp = yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        ytdlp = "no disponible"
    try:
        import ytmusicapi

        ytmusic = getattr(ytmusicapi, "__version__", "instalada")
    except Exception:  # noqa: BLE001
        ytmusic = "no disponible"
    return ytdlp, ytmusic


def diagnostics(
    which: Callable[[str], str | None] = shutil.which,
    online: Callable[[], bool] | None = None,
    cookies: str | None = None,
    data_dir: Path | None = None,
) -> list[dict]:
    """Lista de comprobaciones para la pantalla de diagnóstico.

    `status` es "ok", "warn" o "fail": la UI pinta el color y muestra `detail`.
    """
    from .. import __version__

    online = online or _is_online
    data_dir = Path(data_dir or config.DATA_DIR)
    ytdlp, ytmusic = _versions()
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("Sound", "ok", f"versión {__version__} · Python {sys.version.split()[0]}")
    add("yt-dlp", "ok" if ytdlp != "no disponible" else "fail", ytdlp)
    add("ytmusicapi", "ok" if ytmusic != "no disponible" else "fail", ytmusic)
    node = which("node")
    add("Node.js", "ok" if node else "fail",
        node or "falta: YouTube lo necesita para resolver los enlaces")
    ffmpeg = which("ffmpeg")
    add("ffmpeg", "ok" if ffmpeg else "warn",
        ffmpeg or "opcional: sin él no hay forma de onda real ni normalización de volumen")
    connected = online()
    add("Internet", "ok" if connected else "fail",
        "conexión con YouTube Music" if connected else "sin conexión con YouTube Music")
    if cookies:
        add("Cookies de YouTube", "ok", f"se leen de {cookies}")
    else:
        add("Cookies de YouTube", "warn",
            "no configuradas: necesarias si YouTube pide iniciar sesión")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        add("Carpeta de datos", "ok", str(data_dir))
    except OSError as exc:
        add("Carpeta de datos", "fail", f"no se puede escribir en {data_dir}: {exc}")
    try:
        free = shutil.disk_usage(data_dir).free / 1024**3
        add("Espacio libre", "ok" if free >= MIN_FREE_GB else "warn", f"{free:.1f} GB")
    except OSError:
        add("Espacio libre", "warn", "no se pudo consultar")
    return checks


def open_path(path: str | os.PathLike) -> bool:
    """Abre una carpeta o un archivo con el programa predeterminado. False si no se pudo."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False
