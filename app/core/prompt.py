"""Pedir música con una frase ("música de los ochenta en español"), sin IA.

Se entiende la frase con reglas (década o año, idioma, géneros) y se busca así:
  1. Búsquedas de canciones de catálogo combinando género, década e idioma.
  2. Se descarta lo que no es la grabación original, lo que ya tienes y lo que tiene otro idioma.
  3. De lo que queda se verifica el AÑO REAL en YouTube Music (la consulta cara, en paralelo).
  4. Se para al llegar a la cantidad pedida.

Límites honestos: el idioma es una estimación por palabras del título (ver language.py), y el año
es el del álbum en que está la canción: una canción de los 80 incluida solo en un recopilatorio
posterior se descarta. Sin década ni idioma en la frase se usa el selector de géneros.
"""
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import requests
from ytmusicapi.exceptions import YTMusicError

from . import filters, genres, language, ytsearch

SEARCH_DEPTH = 50  # resultados que se piden por búsqueda
MAX_LOOKUPS = 600  # tope de consultas de año por petición
LOOKUP_WORKERS = 8
SEARCH_ATTEMPTS = 3  # una petición larga hace ~20 búsquedas: un corte suelto no debe tirarla entera
LANG_NAMES = {"es": "español", "en": "inglés", "pt": "portugués", "fr": "francés", "it": "italiano"}
_LANG_WORDS = {
    "es": r"espanol|castellano|spanish",
    "en": r"ingles|english",
    "pt": r"portugues|portuguese",
    "fr": r"frances|french",
    "it": r"italiano|italian",
}
_LANG_QUERY = {"es": "en español", "pt": "em português", "fr": "en français", "it": "in italiano"}
_DECADE_WORDS = {
    "sesenta": 1960, "setenta": 1970, "ochenta": 1980, "noventa": 1990,
    "sixties": 1960, "seventies": 1970, "eighties": 1980, "nineties": 1990,
}
_VALID_SHORT = {50, 60, 70, 80, 90, 0, 10, 20}
_GENRES = (  # (palabra en la frase, término de búsqueda); las de varias palabras primero
    ("new wave", "new wave"), ("synth pop", "synth pop"), ("hip hop", "hip hop"),
    ("rock and roll", "rock and roll"), ("baladas", "baladas"), ("balada", "baladas"),
    ("romanticas", "románticas"), ("romantica", "románticas"), ("romanticos", "románticas"),
    ("romantico", "románticas"), ("rock", "rock"), ("pop", "pop"), ("salsa", "salsa"),
    ("cumbia", "cumbia"), ("bachata", "bachata"), ("merengue", "merengue"), ("vallenato", "vallenato"),
    ("reggaeton", "reggaeton"), ("reguetón", "reggaeton"), ("rap", "rap"), ("trap", "trap"),
    ("electronica", "electrónica"), ("disco", "disco"), ("funk", "funk"), ("soul", "soul"),
    ("jazz", "jazz"), ("blues", "blues"), ("country", "country"), ("reggae", "reggae"), ("ska", "ska"),
    ("punk", "punk"), ("metal", "metal"), ("indie", "indie"), ("folk", "folk"), ("bolero", "bolero"),
    ("tango", "tango"), ("ranchera", "ranchera"), ("mariachi", "mariachi"), ("norteña", "norteña"),
    ("techno", "techno"), ("house", "house"), ("dance", "dance"),
)
_STOP = frozenset(
    "musica cancion canciones temas tema exitos exito de del los las el la y e en un una unos unas para "
    "con que quiero quisiera dame ponme busca buscar escuchar me gustaria algo mejor mejores mas "
    "the of and in for with some good best songs song music años anos decada decadas".split()
)
_STYLES_ES = ("", "pop", "rock", "baladas", "románticas", "baile", "new wave", "éxitos", "clásicos")
_STYLES_EN = ("", "pop", "rock", "hits", "classics", "ballads", "dance", "new wave")


@dataclass
class Parsed:
    text: str
    years: list[tuple[int, int]] = field(default_factory=list)  # rangos de años, ya fusionados
    language: str | None = None
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def constrained(self) -> bool:
        """¿Pide década o idioma? Si no, no hay nada que verificar y basta el selector de géneros."""
        return bool(self.years or self.language)

    def year_ok(self, year: int) -> bool:
        return any(start <= year <= end for start, end in self.years)

    @property
    def label(self) -> str:
        """Nombre corto para la carpeta y los mensajes: 'Rock · 80s · español'."""
        parts = [genre.capitalize() for genre in self.genres]
        parts += [_years_label(start, end) for start, end in self.years]
        if self.language:
            parts.append(LANG_NAMES.get(self.language, self.language))
        return " · ".join(parts) if parts else self.text.strip()[:40]

    def describe(self) -> str:
        years = ", ".join(_years_label(a, b) for a, b in self.years) or "cualquiera"
        lang = LANG_NAMES.get(self.language, self.language) if self.language else "cualquiera"
        genres_ = ", ".join(self.genres) or "cualquiera"
        return f"años: {years} · idioma: {lang} · géneros: {genres_}"


def _years_label(start: int, end: int) -> str:
    if start == end:
        return str(start)
    if start % 10 == 0 and end == start + 9:
        return f"{str(start)[2:]}s"
    return f"{start}-{end}"


def _short_to_decade(token: str) -> int | None:
    value = int(token[:2])
    if value not in _VALID_SHORT:
        return None
    return 1900 + value if value >= 50 else 2000 + value


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(ranges)):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _add(ranges: list[tuple[int, int]], start: int | None) -> None:
    if start is not None:
        ranges.append((start, start + 9))


def parse(text: str) -> Parsed:
    """Entiende la frase con reglas: década(s) o año(s), idioma y géneros. Lo demás son palabras clave."""
    t = " " + filters.fold(text).replace("’", "'").replace("–", "-") + " "
    ranges: list[tuple[int, int]] = []

    def take(pattern: str, handler) -> None:
        nonlocal t
        for match in list(re.finditer(pattern, t)):
            handler(match)
        t = re.sub(pattern, " ", t)

    def decades(match) -> None:
        for token in re.findall(r"\d0", match[1]):
            _add(ranges, _short_to_decade(token))

    # "1980-1989", "de 1980 a 1989"
    take(r"\b((?:19|20)\d{2})\s*(?:-|a|al|hasta|to)\s*((?:19|20)\d{2})\b",
         lambda m: ranges.append((int(m[1]), int(m[2]))))
    # "1980s", "1990's"
    take(r"\b((?:19|20)\d)0'?s\b", lambda m: ranges.append((int(m[1] + "0"), int(m[1] + "0") + 9)))
    # "los 2000", "los 2010" (década completa; un "2010" suelto es un año)
    take(r"\blos\s+(20[012]0)\b", lambda m: ranges.append((int(m[1]), int(m[1]) + 9)))
    # "años 80", "los 80 y 90", "de los 70, 80", "the 80s"
    take(r"(?:anos?|los|decadas?(?:\s+de(?:\s+los)?)?|the|de)\s+"
         r"((?:\d0)(?!\d)(?:'?s)?(?:\s*(?:y|e|,|and|-|/)\s*\d0(?!\d)(?:'?s)?)*)", decades)
    # "80s", "80's"
    take(r"\b(\d0)'?s\b", lambda m: _add(ranges, _short_to_decade(m[1])))
    # "ochenta(s)", "eighties"
    take(r"\b(sesenta|setenta|ochenta|noventa|sixties|seventies|eighties|nineties)s?\b",
         lambda m: ranges.append((_DECADE_WORDS[m[1]], _DECADE_WORDS[m[1]] + 9)))
    take(r"\bdos\s?miles?\b", lambda m: ranges.append((2000, 2009)))
    # año suelto: "1985"
    take(r"\b(19[5-9]\d|20[0-2]\d)\b", lambda m: ranges.append((int(m[1]), int(m[1]))))

    detected = None
    for code, words in _LANG_WORDS.items():
        pattern = rf"\b(?:en\s+|in\s+)?(?:{words})\b"
        if re.search(pattern, t):
            detected = detected or code
            t = re.sub(pattern, " ", t)

    found_genres: list[str] = []
    for word, term in _GENRES:
        pattern = rf"\b{re.escape(filters.fold(word))}\b"
        if re.search(pattern, t):
            if term not in found_genres:
                found_genres.append(term)
            t = re.sub(pattern, " ", t)

    keywords = [w for w in re.findall(r"[a-z]{3,}", t) if w not in _STOP]
    return Parsed(text=text, years=_merge(ranges), language=detected, genres=found_genres, keywords=keywords)


def search_queries(parsed: Parsed) -> list[str]:
    """Búsquedas a probar, de más a menos específica. Combinan género, década e idioma."""
    phrases: list[str] = []
    for start, end in parsed.years:
        if start == end:
            phrases.append(str(start))
        elif start % 10 == 0 and end == start + 9:
            short = str(start)[2:]
            phrases += [f"{short}s", f"{start}s"] if parsed.language == "en" else [f"años {short}", f"{short}s"]
        else:
            phrases.append(f"{start} {end}")
    lang = _LANG_QUERY.get(parsed.language or "", "")
    styles = tuple(parsed.genres) or (_STYLES_EN if parsed.language == "en" else _STYLES_ES)

    queries = [parsed.text.strip()]
    for phrase in (phrases or [""]):
        queries += [" ".join(p for p in (style, phrase, lang) if p) for style in styles]
    for keyword in parsed.keywords[:2]:
        queries += [" ".join(p for p in (keyword, phrase, lang) if p) for phrase in (phrases or [""])]
    seen, unique = set(), []
    for query in queries:
        key = filters.fold(query)
        if query and key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


@dataclass
class PromptResult:
    label: str
    tracks: list[dict]
    parsed: Parsed
    stats: Counter = field(default_factory=Counter)
    note: str = ""

    def summary(self, n: int) -> str:
        s = self.stats
        parts = [f"{len(self.tracks)} de {n} pedidas"]
        if s["queries"]:
            searches = "búsqueda" if s["queries"] == 1 else "búsquedas"
            songs = "canción revisada" if s["examined"] == 1 else "canciones revisadas"
            parts.append(f"{s['queries']} {searches}, {s['examined']} {songs}")
        discarded = [(s["wrong_year"], "de otra época"), (s["wrong_language"], "de otro idioma"),
                     (s["no_year"], "sin año verificable"), (s["not_original"], "no originales")]
        shown = ", ".join(f"{count} {name}" for count, name in discarded if count)
        if shown:
            parts.append(f"descartadas: {shown}")
        if s["search_failed"]:
            parts.append(f"{s['search_failed']} búsquedas fallaron por la red y se saltaron")
        if self.note:
            parts.append(self.note)
        return " · ".join(parts)


def select_by_prompt(
    text: str,
    n: int,
    per_artist: int = 3,
    strict: bool = True,
    skip_ids: set[str] | None = None,
    skip_songs: set[tuple[str, str]] | None = None,
    on_progress: Callable[[int, int, int, int], None] | None = None,
    search: Callable[[str, int], list[dict]] | None = None,
    details: Callable[[str], dict] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PromptResult:
    """Reúne hasta n canciones que cumplan la frase. `on_progress(halladas, n, búsquedas, total)`.

    `should_stop()` se consulta antes de cada búsqueda: si devuelve True se termina y se entrega lo
    ya encontrado (así un botón «Cancelar» no pierde el trabajo hecho). `search` y `details` se
    pueden sustituir (los tests no usan red). Si NINGUNA búsqueda o consulta de año funcionó, se
    avisa con ConnectionError en vez de devolver una lista vacía.
    """
    search = search or ytsearch.search_songs
    details = details or ytsearch.song_details
    parsed = parse(text)

    if not parsed.constrained:  # sin década ni idioma: el selector de géneros ya lo hace bien
        term = parsed.genres[0] if parsed.genres else text.strip()
        label, tracks = genres.select_tracks(
            term, n, per_artist, strict, skip_ids=skip_ids, skip_songs=skip_songs)
        return PromptResult(label=label, tracks=tracks, parsed=parsed,
                            note="sin década ni idioma en la frase: usé el selector de géneros")

    picker = genres.Picker(n, per_artist, strict, skip_ids, skip_songs)
    stats: Counter = Counter()
    seen = set(skip_ids or ())
    pool: list[dict] = []  # todo lo visto: sirve para votar el idioma de cada artista
    unsure: list[dict] = []  # sin pista de idioma: relleno si no se llega a n
    queries = search_queries(parsed)
    budget = [MAX_LOOKUPS]
    counts = {"ok": 0, "failed": 0}
    check_language = parsed.language in ("es", "en")

    def verify(candidates: list[dict]) -> list[dict]:
        """Confirma el año real. Sin década pedida no hay nada que verificar."""
        if not parsed.years:
            return candidates
        batch = candidates[: max(budget[0], 0)]
        budget[0] -= len(batch)

        def one(track: dict) -> tuple[dict, dict | None]:
            try:
                return track, details(track["video_id"])
            except (requests.RequestException, YTMusicError, KeyError, IndexError):
                return track, None

        with ThreadPoolExecutor(LOOKUP_WORKERS) as workers:
            answers = list(workers.map(one, batch))
        accepted = []
        for track, info in answers:
            if info is None:
                counts["failed"] += 1
                continue
            counts["ok"] += 1
            year = info.get("year")
            if not year:
                stats["no_year"] += 1
            elif not parsed.year_ok(year):
                stats["wrong_year"] += 1
            else:
                accepted.append({**track, "year": year, "album": track.get("album") or info.get("album")})
        return accepted

    def search_with_retry(query: str) -> list[dict] | None:
        for attempt in range(SEARCH_ATTEMPTS):
            try:
                return search(query, SEARCH_DEPTH)
            except (requests.RequestException, YTMusicError):
                if attempt + 1 < SEARCH_ATTEMPTS:
                    time.sleep(1 + attempt)
        return None  # sigue fallando: se salta esta búsqueda y se continúa con las demás

    stopped = False
    for index, query in enumerate(queries, 1):
        if picker.full or budget[0] <= 0:
            break
        if should_stop and should_stop():
            stopped = True
            break
        stats["queries"] += 1
        results = search_with_retry(query)
        if results is None:
            stats["search_failed"] += 1
            continue
        fresh = []
        for track in results:
            if track["video_id"] in seen:
                continue
            seen.add(track["video_id"])
            stats["examined"] += 1
            if strict and filters.reject_reason(track):
                stats["not_original"] += 1
                continue
            fresh.append(track)
        pool.extend(fresh)
        votes = language.vote_by_artist(pool)

        eligible = []
        for track in fresh:
            if check_language:
                guess = language.language_of(track, votes)
                if guess is None:
                    unsure.append(track)
                    continue
                if guess != parsed.language:
                    stats["wrong_language"] += 1
                    continue
            eligible.append(track)
        picker.take(verify(eligible), None)
        if on_progress:
            on_progress(len(picker.picked), n, index, len(queries))

    note = "cancelada: se entrega lo encontrado hasta ahora" if stopped else ""
    if not stopped and not picker.full and unsure and budget[0] > 0:  # relleno: sin idioma claro, pero de la época
        before = len(picker.picked)
        picker.take(verify(unsure), None)
        filled = len(picker.picked) - before
        if filled:
            stats["unsure_language"] = filled
            note = f"{filled} sin idioma confirmado (el título no da pistas)"
    if stats["queries"] and stats["search_failed"] == stats["queries"] and not stopped:
        raise ConnectionError("No pude buscar en YouTube Music (¿sin internet?)")
    if parsed.years and counts["ok"] == 0 and counts["failed"]:
        raise ConnectionError("No pude verificar los años en YouTube Music (¿sin internet?)")
    return PromptResult(label=parsed.label, tracks=picker.picked, parsed=parsed, stats=stats, note=note)
