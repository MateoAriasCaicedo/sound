"""Estimación del idioma de una canción por las palabras de su título y álbum.

YouTube Music no informa del idioma de una canción, así que esto es una heurística: cuenta
palabras muy frecuentes de cada idioma (artículos, preposiciones, pronombres), terminaciones
típicas del español ('-mente', '-ción') y letras propias (ñ, ¿, ¡, vocales con tilde). Sirve para
descartar lo que claramente está en otro idioma. Los títulos sin ninguna pista ('Africa') quedan
como desconocidos (None): no son ni aceptados ni descartados a ciegas, quien llama decide.

El título pesa el doble que el álbum, y el álbum solo cuenta si aporta al menos 2 palabras: un
grupo en español con un álbum llamado 'Big Bang' no debe leerse como inglés. El nombre del artista
no se usa: no dice nada fiable del idioma en que canta (para eso está `vote_by_artist`).
"""
import re
from collections import Counter

# Solo palabras que NO existen con el mismo significado en el otro idioma:
# 'no', 'me', 'sin', 'son', 'a', 'come', 'sol' se dejan fuera a propósito.
_ES = frozenset(
    "el la los las lo de del al un una unos unas y que con por para mi mis tu tus su sus te se les nos "
    "es soy eres está estás estoy como cuando donde quien quién porque pero más muy ya aquí ahí allá "
    "ayer hoy mañana siempre nunca todo todos nada algo amor corazón vida noche día tiempo cielo mar "
    "luna sueño sueños mujer hombre niña niño beso besos fuego llorar bailar quiero puedo tengo dame "
    "dime déjame vuelve vuelva volver hasta desde entre sobre bajo sí otra otro esta este eso esa "
    "mío mía tuyo tuya vamos hay fue ser tener hacer".split()
)
_EN = frozenset(
    "the you your yours my of and in on to is it its i'm i'll i've you're we they them our love baby "
    "girl boy day night heart down up never just don't can't won't with without from when where what "
    "why who how all one time life world dance dancing fire eyes tonight forever again away back "
    "go got get like want need know feel make take give call think say tell little big long hold "
    "let's there here this that these those into over under after before".split()
)
_ES_ENDINGS = ("mente", "ción", "sión", "iendo", "ando", "dad")  # solo en palabras largas
_SPANISH_LETTERS = set("ñ¿¡áéíóúü")
_WORD = re.compile(r"[\w']+", re.UNICODE)


def _score(text: str | None) -> tuple[int, int]:
    """(evidencia de español, evidencia de inglés) de un texto."""
    text = (text or "").lower()
    words = _WORD.findall(text)
    es = sum(1 for w in words if w in _ES)
    es += sum(1 for w in words if len(w) >= 6 and w.endswith(_ES_ENDINGS))
    en = sum(1 for w in words if w in _EN)
    if any(c in _SPANISH_LETTERS for c in text):
        es += 1 + ("ñ" in text or "¿" in text or "¡" in text)
    return es, en


def guess_language(title: str | None, album: str | None = None) -> str | None:
    """'es', 'en' o None cuando no hay evidencia suficiente."""
    title_es, title_en = _score(title)
    album_es, album_en = _score(album)
    album_es, album_en = (album_es if album_es >= 2 else 0), (album_en if album_en >= 2 else 0)
    es, en = 2 * title_es + album_es, 2 * title_en + album_en
    if es > en:
        return "es"
    if en > es:
        return "en"
    return None


def vote_by_artist(tracks: list[dict]) -> dict[str, str]:
    """Idioma probable de cada artista según sus propias canciones con idioma claro.

    Permite resolver las que no tienen pistas en el título ('Hotel California'): si otras del
    mismo artista salen claramente en inglés, esa también. Solo decide con mayoría clara.
    """
    votes: dict[str, Counter] = {}
    for track in tracks:
        language = guess_language(track.get("title"), track.get("album"))
        if language:
            votes.setdefault(_artist_key(track), Counter())[language] += 1
    result = {}
    for artist, counter in votes.items():
        (top, top_count), *rest = counter.most_common()
        if not rest or top_count > rest[0][1]:
            result[artist] = top
    return result


def _artist_key(track: dict) -> str:
    return (track.get("artist") or "").split(",")[0].strip().lower()


def language_of(track: dict, artist_votes: dict[str, str] | None = None) -> str | None:
    """Idioma de una canción: por su título y álbum o, si no hay pistas, por el voto de su artista."""
    return guess_language(track.get("title"), track.get("album")) or (artist_votes or {}).get(_artist_key(track))
