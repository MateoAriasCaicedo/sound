"""Radio en directo: emisoras del directorio libre Radio Browser (radio-browser.info).

- Sin cuenta ni clave: se pregunta a sus servidores públicos (si uno falla, se prueba otro).
- A Radio Browser solo se le envía lo que se busca (país, género o nombre); nada de la
  biblioteca. Al escuchar una emisora, el equipo se conecta al servidor de esa emisora, como
  cualquier reproductor de radio.
- Las emisoras favoritas se guardan en la base de datos (tabla `radio_favorites`).
- Las emisoras no se graban ni se descargan: son en directo.
"""
from __future__ import annotations

import ctypes
import json
import locale
import random
import re
import urllib.parse
import urllib.request
from contextlib import closing

from .. import __version__
from . import library

DIRECTORY = "https://all.api.radio-browser.info"  # reparte entre los servidores que funcionan
USER_AGENT = f"Sound/{__version__}"
TIMEOUT_S = 12
LIMIT = 60
MAX_TAGS = 4
LOGO_MAX_BYTES = 1024 * 1024  # un logo mayor no es un logo
_hosts: list[str] = []


class RadioError(RuntimeError):
    """No se pudo hablar con el directorio de emisoras."""


# ---------- directorio ----------
def _open(url: str, opener=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S)


def _servers(opener=None) -> list[str]:
    """Los servidores del directorio, en orden aleatorio (así se reparte la carga)."""
    if _hosts:
        return list(_hosts)
    try:
        with _open(f"{DIRECTORY}/json/servers", opener) as resp:
            names = {item["name"] for item in json.load(resp) if item.get("name")}
    except Exception:  # noqa: BLE001 - sin lista, el nombre común también reparte
        names = set()
    hosts = [f"https://{name}" for name in sorted(names)]
    random.shuffle(hosts)
    _hosts[:] = hosts + [DIRECTORY]
    return list(_hosts)


def _get(path: str, params: dict | None = None, opener=None) -> list[dict]:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    last: Exception | None = None
    for host in _servers(opener):
        try:
            with _open(f"{host}{path}{query}", opener) as resp:
                data = json.load(resp)
            if isinstance(data, list):
                return data
        except Exception as exc:  # noqa: BLE001 - se prueba con el siguiente servidor
            last = exc
    raise RadioError(f"no responde el directorio de emisoras ({last})")


def normalize(raw: dict) -> dict | None:
    """Lo que Sound necesita de una emisora; None si no tiene dirección para escucharla."""
    url = (raw.get("url_resolved") or raw.get("url") or "").strip()
    uuid = raw.get("stationuuid") or raw.get("uuid")
    name = " ".join((raw.get("name") or "").split())
    if not url or not uuid or not name:
        return None
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "uuid": uuid,
        "name": name,
        "url": url,
        "favicon": (raw.get("favicon") or "").strip() or None,
        "homepage": (raw.get("homepage") or "").strip() or None,
        "country": raw.get("country") or "",
        "countrycode": (raw.get("countrycode") or "").upper(),
        "state": (raw.get("state") or "").strip(),
        "tags": list(tags)[:MAX_TAGS],
        "codec": (raw.get("codec") or "").upper(),
        "bitrate": int(raw.get("bitrate") or 0),
    }


def dedupe(stations: list[dict]) -> list[dict]:
    """El directorio repite emisoras (la misma dirección o el mismo nombre): una de cada."""
    out, urls, names = [], set(), set()
    for station in stations:
        name = station["name"].casefold()
        if station["url"] in urls or name in names:
            continue
        urls.add(station["url"])
        names.add(name)
        out.append(station)
    return out


def _stations(params: dict, opener=None) -> list[dict]:
    base = {"hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": LIMIT}
    raw = _get("/json/stations/search", {**base, **params}, opener)
    return dedupe([s for s in map(normalize, raw) if s])


def by_country(code: str, opener=None) -> list[dict]:
    """Las más escuchadas de un país (código ISO de dos letras)."""
    return _stations({"countrycode": code.upper()}, opener)


def popular(opener=None) -> list[dict]:
    """Las más escuchadas del mundo."""
    return _stations({}, opener)


def by_tag(tag: str, country: str | None = None, opener=None) -> list[dict]:
    """Por género o tema («salsa», «news»…). Con país, primero las de ahí; si son pocas, se
    completan con las del resto del mundo."""
    found = _stations({"tag": tag, "countrycode": country.upper()}, opener) if country else []
    if len(found) < 12:
        found = dedupe(found + _stations({"tag": tag}, opener))
    return found


def search(text: str, opener=None) -> list[dict]:
    """Por nombre y, si salen pocas, también por género (así «vallenato» encuentra emisoras
    que no lo llevan en el nombre)."""
    text = text.strip()
    if not text:
        return []
    found = _stations({"name": text}, opener)
    if len(found) < 12:
        found = dedupe(found + _stations({"tag": text.lower()}, opener))
    return found


# ---------- país de Windows ----------
def country_code() -> str | None:
    """El país de la región de Windows («CO»); si no se sabe, el del idioma («es_CO»)."""
    try:
        buf = ctypes.create_unicode_buffer(16)
        if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, len(buf)) and len(buf.value) == 2:
            return buf.value.upper()
    except (AttributeError, OSError):  # Windows antiguo o no es Windows
        pass
    lang = locale.getlocale()[0] or ""
    code = lang.replace("-", "_").split("_")[-1] if "_" in lang.replace("-", "_") else ""
    return code.upper() if len(code) == 2 and code.isalpha() else None


def country_name(code: str) -> str:
    """Nombre del país en el idioma de Windows («Colombia»); si no, el código."""
    try:
        buf = ctypes.create_unicode_buffer(128)
        GEO_FRIENDLYNAME = 8
        if ctypes.windll.kernel32.GetGeoInfoEx(code, GEO_FRIENDLYNAME, buf, len(buf)):
            return buf.value or code
    except (AttributeError, OSError):
        pass
    return code


# ---------- favoritas ----------
def favorites(db_path=None) -> list[dict]:
    """Las emisoras favoritas, en el orden en que se marcaron."""
    with closing(library.connect(db_path)) as conn:
        rows = conn.execute("SELECT station FROM radio_favorites ORDER BY added_at, rowid")
        out = []
        for (data,) in rows.fetchall():
            try:
                station = json.loads(data)
            except ValueError:
                continue
            if isinstance(station, dict) and station.get("uuid") and station.get("url"):
                out.append(station)
    return out


def favorite_ids(db_path=None) -> set[str]:
    with closing(library.connect(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT uuid FROM radio_favorites")}


def add_favorite(station: dict, db_path=None) -> None:
    with closing(library.connect(db_path)) as conn, conn:
        # otra vez la misma: se actualizan sus datos sin moverla de sitio
        conn.execute("INSERT INTO radio_favorites (uuid, station) VALUES (?, ?) "
                     "ON CONFLICT(uuid) DO UPDATE SET station = excluded.station",
                     (station["uuid"], json.dumps(station, ensure_ascii=False)))


def remove_favorite(uuid: str, db_path=None) -> None:
    with closing(library.connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM radio_favorites WHERE uuid = ?", (uuid,))


def short_name(station: dict) -> str:
    """El nombre sin la coletilla técnica: «Caracol Radio (Bogotá) - 100.9 FM / 810 AM - HJGL…»
    queda en «Caracol Radio (Bogotá)». Lo largo entero se ve en su tarjeta."""
    name = station.get("name") or ""
    head = re.split(r"\s+[-|–—]\s+", name)[0].strip()
    return head if len(head) >= 3 else name


def fetch_logo(url: str | None, opener=None) -> bytes | None:
    """Descarga el logo de una emisora (None si no hay, no responde o no es una imagen)."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        with _open(url, opener) as resp:
            kind = (resp.headers.get("Content-Type") or "").lower() if hasattr(resp, "headers") \
                else ""
            data = resp.read(LOGO_MAX_BYTES + 1)
    except Exception:  # noqa: BLE001 - sin logo se dibuja uno propio
        return None
    if not data or len(data) > LOGO_MAX_BYTES or "svg" in kind or data.lstrip()[:5] == b"<?xml":
        return None
    return data


def subtitle(station: dict) -> str:
    """«Bogotá · Pop, Rock» o, sin ciudad, el país: lo que se lee bajo el nombre."""
    place = station.get("state") or station.get("country") or ""
    tags = ", ".join(t.capitalize() for t in station.get("tags", [])[:2])
    return " · ".join(part for part in (place, tags) if part)
