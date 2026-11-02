"""Descarta lo que no es la grabación original de estudio: mixes, covers, directos, karaokes...

Es una heurística sobre título, álbum, artista y tipo de pista. No puede detectar un cover
publicado como canción normal por otro artista, sin ninguna pista en los metadatos.
"""
import re
import unicodedata


def fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar 'Versión' con 'version'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def base_title(title: str) -> str:
    """'Song - Remastered 2011' y 'Song (Live)' cuentan como la misma canción."""
    return fold(re.split(r"\s[-(\[]", title)[0])


def song_key(artist: str, title: str) -> tuple[str, str]:
    """Identidad aproximada de una canción: (artista principal, título base), sin tildes."""
    return fold(artist.split(",")[0]), base_title(title)


# Casi nunca forman parte del nombre real de una canción: se buscan en cualquier parte.
_STRONG = re.compile(
    r"\b(karaoke|megamix|mix|mashup|nightcore|sped up|slowed|8d audio|made famous"
    r"|in the style of|originally performed|backing track|tribute to|tributo a"
    r"|cover version|covers)\b"
)
# Pueden ser parte del título ('Live Forever', 'Cover Me'): solo cuentan como decoración,
# es decir, entre paréntesis o corchetes, o tras ' - '.
_DECORATION = re.compile(
    r"\b(live|en vivo|ao vivo|en directo|unplugged|concert|concierto|remix|medley|popurri"
    r"|potpourri|enganchados?|instrumental|acoustic|acustic[oa]|demo|cover|tribute|tributo"
    r"|homenaje|sessions?)\b"
)
_VERSION = re.compile(r"\b([a-z0-9]+) version\b")
# '(Single Version)' o '(1994 Remastered Version)' siguen siendo la grabación original.
_ORIGINAL_QUALIFIERS = {
    "single", "album", "original", "remaster", "remastered", "remasterizado", "mono",
    "stereo", "deluxe", "explicit", "clean", "radio", "studio", "lp", "video",
}
_ALBUM = re.compile(
    r"\b(live|en vivo|ao vivo|en directo|unplugged|concert|concierto|remixes|mixes|tribute"
    r"|tributo|homenaje|karaoke|covers|sessions?)\b"
)
_ARTIST = re.compile(r"karaoke|tribut|\bcovers?\b|various artists|varios artistas|hit crew|party tyme")
_BAD_TYPES = {"MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"}


# 'Version Salsa' (orden en español): rechaza salvo 'Version Original', 'Version Remasterizada'...
_VERSION_AFTER = re.compile(
    r"\bversion (?!(?:original|remaster\w*|album|single|mono|stereo|deluxe|explicit\w*"
    r"|clean|radio|video|studio)\b)([a-z0-9]+)"
)


def _decorations(title: str) -> list[str]:
    inside = re.findall(r"[\(\[]([^)\]]*)[\)\]]", title)
    after_dash = title.split(" - ", 1)[1:]
    return inside + after_dash


def reject_reason(track: dict) -> str | None:
    """Motivo por el que la canción no es 'normal', o None si pasa el filtro."""
    if track.get("video_type") in _BAD_TYPES:
        return "subida de usuario"

    title = fold(track.get("title", "")).replace("original mix", "")
    if m := _STRONG.search(title):
        return m.group(1)

    parts = _decorations(title)
    decorations = " ".join(parts)
    if m := _DECORATION.search(decorations):
        return m.group(1)
    if any(part.strip() == "version" for part in parts):
        return "version alterna"  # en reggae, '(Version)' es un dub o instrumental
    for m in _VERSION.finditer(decorations):
        qualifier = m.group(1)
        if qualifier not in _ORIGINAL_QUALIFIERS and not qualifier.isdigit():
            return f"{qualifier} version"
    if m := _VERSION_AFTER.search(decorations):
        return f"version {m.group(1)}"

    album = fold(track.get("album") or "")
    if m := _ALBUM.search(album):
        return f"álbum: {m.group(1)}"
    if m := _ARTIST.search(fold(track.get("artist", ""))):
        return f"artista: {m.group(0)}"
    return None
