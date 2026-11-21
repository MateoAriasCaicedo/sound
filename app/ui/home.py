"""«Inicio»: estanterías con lo que escuchas, lo que más suena y lo nuevo de tu biblioteca, y
«Descubre en YouTube» con canciones que aún no tienes (ver core/discover.py).

Funciones puras (sin Tk ni base de datos) para poder probarlas: la ventana les pasa las listas
ya leídas del historial, de «Me gusta» y de la biblioteca.
"""
import random

SHELVES = ("Seguir escuchando", "Lo que más escuchas", "Añadidas hace poco", "Tus Me gusta",
           "Todavía sin escuchar")
DISCOVER = "Descubre en YouTube"


def greeting(hour: int) -> str:
    """Título de «Inicio» según la hora del día."""
    if 5 <= hour < 12:
        return "Buenos días"
    if 12 <= hour < 20:
        return "Buenas tardes"
    return "Buenas noches"


def shelves(by_id: dict[str, dict], recent: list[dict], top: list[dict], library: list[dict],
            liked: list[dict], counts: dict[str, int], per: int,
            seed: int, discover: list[dict] | tuple = ()) -> tuple[list[dict], list[tuple[int, str]]]:
    """Filas de «Inicio» y sus secciones [(primera fila, título)].

    Cada estantería lleva como mucho `per` canciones y cada canción sale una sola vez, en la
    primera estantería donde cabe: así no se repite lo mismo cinco veces. Solo entran canciones
    que siguen en la biblioteca (`by_id`), con sus datos actuales. «Todavía sin escuchar» es un
    sorteo que cambia con `seed` (la ventana usa el día: estable durante la jornada).

    `discover` (canciones de YouTube que aún no tienes) va justo después de «Seguir escuchando»:
    tal cual, sin pasar por la biblioteca, y sin las que ya estén en ella.
    """
    rows: list[dict] = []
    sections: list[tuple[int, str]] = []
    seen: set[str] = set()

    def shelf(title: str, tracks) -> None:
        picked = []
        for t in tracks:
            vid = t.get("video_id")
            if not vid or vid in seen or vid not in by_id:
                continue
            seen.add(vid)
            picked.append(by_id[vid])
            if len(picked) >= per:
                break
        if picked:
            sections.append((len(rows), title))
            rows.extend(picked)

    recent_title, top_title, added_title, liked_title, unheard_title = SHELVES
    shelf(recent_title, recent)
    new = [t for t in discover if t.get("video_id") and t["video_id"] not in by_id
           and t["video_id"] not in seen][:per]
    if new:
        seen.update(t["video_id"] for t in new)
        sections.append((len(rows), DISCOVER))
        rows.extend(new)
    shelf(top_title, top)
    shelf(added_title, sorted(library, key=lambda t: t.get("added_at") or "", reverse=True))
    shelf(liked_title, liked)
    unheard = [t for t in library if not counts.get(t["video_id"])]
    random.Random(seed).shuffle(unheard)
    shelf(unheard_title, unheard)
    return rows, sections


def section_of(sections: list[tuple[int, str]], total: int, row: int) -> tuple[int, int, str]:
    """(inicio, fin, título) de la sección que contiene la fila `row`."""
    start, title = 0, ""
    end = total
    for i, (first, name) in enumerate(sections):
        if first <= row:
            start, title = first, name
            end = sections[i + 1][0] if i + 1 < len(sections) else total
    return start, end, title
