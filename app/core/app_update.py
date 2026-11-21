"""Versiones nuevas de Sound: buscarlas en GitHub, descargar el instalador y abrirlo.

Solo se consulta la lista pública de versiones del proyecto en GitHub, como mucho una vez al día
y si está activado en Ajustes. No se envía nada de la biblioteca ni del equipo: la petición lleva
la versión instalada en el User-Agent y nada más.

El instalador se comprueba con su suma SHA-256, publicada junto a él en la misma versión
(`Sound-X.Y.Z-setup.exe.sha256`). Si no coincide, se borra y no se instala; si la versión no
trae suma, no se instala sola (se ofrece la página de la versión para descargarla a mano).

Al instalar, Sound abre el instalador en modo silencioso con `/RELAUNCH`, se cierra para que
pueda sustituir los archivos, y el instalador lo vuelve a abrir al terminar (installer.iss).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "MateoAriasCaicedo/sound"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_INTERVAL_S = 24 * 3600
TIMEOUT_S = 15
CHUNK = 256 * 1024
# opciones del instalador (Inno Setup): sin preguntas, cierra Sound si sigue abierto y lo reabre
INSTALL_FLAGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH")

_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+){0,3})\s*$", re.IGNORECASE)
_INSTALLER = re.compile(r"^Sound-(.+)-setup\.exe$", re.IGNORECASE)
_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")


class UpdateError(Exception):
    """Algo salió mal al buscar o descargar; el mensaje se puede enseñar tal cual."""


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page_url: str
    installer_url: str | None = None
    installer_name: str | None = None
    installer_size: int = 0
    checksum_url: str | None = None

    @property
    def installable(self) -> bool:
        """Se puede instalar sola: trae instalador y su suma de comprobación."""
        return bool(self.installer_url and self.installer_name and self.checksum_url)


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v1.3.0' → (1, 3, 0). None si no es una versión publicada normal (p. ej. '1.3.0-beta')."""
    found = _VERSION.match(text or "")
    return tuple(int(part) for part in found.group(1).split(".")) if found else None


def is_newer(candidate: str | None, current: str | None) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def parse_release(data: dict) -> Release | None:
    """La respuesta de GitHub como `Release`; None si es un borrador, una beta o no se entiende."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        return None
    version = ".".join(str(n) for n in parse_version(tag))
    installer = checksum = None
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    for asset in assets:
        found = _INSTALLER.match(str(asset.get("name") or ""))
        if found and parse_version(found.group(1)) == parse_version(version):
            installer = asset
    if installer is not None:
        wanted = {f"{installer['name']}.sha256".lower(), "sha256sums.txt"}
        checksum = next((a for a in assets if str(a.get("name") or "").lower() in wanted), None)
    return Release(
        version=version,
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        installer_url=installer.get("browser_download_url") if installer else None,
        installer_name=installer.get("name") if installer else None,
        installer_size=int(installer.get("size") or 0) if installer else 0,
        checksum_url=checksum.get("browser_download_url") if checksum else None,
    )


def _request(url: str, current: str, accept: str = "application/vnd.github+json"):
    return urllib.request.Request(url, headers={
        "User-Agent": f"Sound/{current} (actualizaciones)",  # nada más: ni usuario ni equipo
        "Accept": accept,
    })


def fetch_latest(current: str, opener=urllib.request.urlopen) -> Release | None:
    """La versión publicada más reciente si es más nueva que `current`; None si no la hay."""
    try:
        with opener(_request(LATEST_API, current), timeout=TIMEOUT_S) as response:
            data = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise UpdateError("No se pudo consultar GitHub (¿sin conexión?)") from exc
    except ValueError as exc:
        raise UpdateError("GitHub respondió algo que no se entiende") from exc
    release = parse_release(data)
    return release if release is not None and is_newer(release.version, current) else None


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # enlaces: solo el texto
    return re.sub(r"(\*\*|__|`)", "", text).strip()


def _headline(text: str, width: int) -> str:
    """Lo esencial de un punto: lo que va en negrita al principio o su primera frase."""
    lead = re.match(r"^\*\*(.+?)\*\*", text)
    if lead and len(lead.group(1).split()) >= 3:
        text = lead.group(1)
    text = _plain(text)
    cut = re.search(r"[.:;](\s|$)", text)
    if cut and cut.start() > 12:
        text = text[:cut.start() + (1 if text[cut.start()] == "." else 0)]
    text = text.rstrip(":;, ")
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def summary(notes: str, lines: int = 6, width: int = 110) -> str:
    """Un punto por línea de las novedades (las del CHANGELOG), sin Markdown, para Ajustes.

    Junta las líneas que continúan un punto, se salta títulos, sub-puntos y «Full Changelog»."""
    items: list[list[str]] = []
    current: list[str] | None = None
    for raw in (notes or "").splitlines():
        line = raw.strip()
        indented = raw[:1] in (" ", "\t")
        bullet = re.match(r"^[-*+]\s+(.*)", line)
        if not line or line.startswith(("#", "<!--", "---", "```")) or \
                _plain(line).lower().startswith("full changelog"):
            current = None
        elif bullet and not indented:
            current = [bullet.group(1)]
            items.append(current)
        elif bullet:
            continue  # sub-punto: no cabe en un resumen
        elif current is not None and indented:
            current.append(line)  # el punto sigue en la línea siguiente
        else:
            current = [line]
            items.append(current)
    out = [("• " if len(items) > 1 else "") + _headline(" ".join(parts), width)
           for parts in items[:lines]]
    return "\n".join(line for line in out if line.strip("• "))


def _expected_hash(text: str, name: str) -> str | None:
    """La suma de `name` en un archivo .sha256 o SHA256SUMS ('hash  nombre' por línea)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:  # primero la línea que nombra el archivo
        found = _HEX64.search(line)
        if found and name.lower() in line.lower():
            return found.group(1).lower()
    if len(lines) == 1 and (found := _HEX64.search(lines[0])):
        return found.group(1).lower()  # un .sha256 con solo la suma
    return None


def download(release: Release, folder: Path, current: str, progress=None, cancelled=None,
             opener=urllib.request.urlopen) -> Path:
    """Descarga el instalador a `folder`, comprueba su SHA-256 y devuelve su ruta.

    `progress(hechos, total)` se llama desde este hilo; `cancelled()` True corta la descarga.
    """
    if not release.installable:
        raise UpdateError("Esta versión no trae suma de comprobación: descárgala desde GitHub")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with opener(_request(release.checksum_url, current, "text/plain"),
                    timeout=TIMEOUT_S) as response:
            expected = _expected_hash(response.read(65536).decode("utf-8", "replace"),
                                      release.installer_name)
    except OSError as exc:
        raise UpdateError("No se pudo descargar la suma de comprobación") from exc
    if expected is None:
        raise UpdateError("La suma de comprobación publicada no se entiende")
    final = folder / release.installer_name
    part = final.with_name(final.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(_request(release.installer_url, current, "application/octet-stream"),
                    timeout=TIMEOUT_S) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or release.installer_size or 0)
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("Descarga cancelada")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
            out.flush()
            os.fsync(out.fileno())
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("Se cortó la descarga del instalador") from exc
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("El instalador descargado no coincide con el publicado: no se instala")
    os.replace(part, final)
    return final


def clean(folder: Path, keep: Path | None = None, older_than_s: float = 0) -> None:
    """Borra instaladores viejos o a medias de `folder` (salvo `keep`)."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    now = time.time()
    for entry in entries:
        if keep is not None and entry == keep:
            continue
        if not entry.name.lower().endswith((".exe", ".part")):
            continue
        try:
            if now - entry.stat().st_mtime >= older_than_s:
                entry.unlink()
        except OSError:
            pass  # en uso o sin permiso: se intentará la próxima vez


def install_command(path: Path, autostart: bool | None = None) -> list[str]:
    """La orden del instalador. `autostart` respeta «Iniciar con Windows» tal como está ahora
    (si no, la actualización repetiría lo que se eligió al instalar la primera vez)."""
    command = [str(path), *INSTALL_FLAGS]
    if autostart is not None:
        command.append("/MERGETASKS=" + ("autostart" if autostart else "!autostart"))
    return command


def launch_installer(path: Path, autostart: bool | None = None) -> None:
    """Abre el instalador por su cuenta: sigue aunque Sound se cierre enseguida."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(install_command(path, autostart), creationflags=flags, close_fds=True,
                     cwd=str(path.parent))
