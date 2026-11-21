"""Atrás y Adelante, como en un navegador.

Un «sitio» (`Place`) es lo que enseña la ventana: la sección del lateral (Inicio, Todo, un género,
una lista…), las páginas de álbum o artista abiertas encima, una página como Ajustes y si se está
buscando. Al ir a otro sitio se apunta también por dónde ibas en el que dejas (desplazamiento,
selección y década), y al volver queda como estaba.

`History` es la pila de sitios con su posición, sin Tk (se prueba sola). `NavigationMixin` la
conecta con la ventana: `_navigating()` envuelve cada acción que cambia de sitio, `go_back` y
`go_forward` recorren la pila y `_go` deja la ventana en un sitio guardado.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace

LIMIT = 60  # sitios que se recuerdan; los más viejos se olvidan


@dataclass(frozen=True)
class Place:
    section: tuple = (("home", None),)  # claves (tipo, nombre) del lateral, ordenadas
    views: tuple = ()  # (tipo, clave, título, artista) de cada página de álbum o artista abierta
    page: str | None = None  # «settings», «import», «downloads»…
    query: str = ""
    # no cambian de sitio, pero se recuperan al volver
    decade: int | None = None
    scroll: float = 0.0
    selected: tuple = ()

    @property
    def searching(self) -> bool:
        return bool(self.query.strip())

    def same(self, other: Place | None) -> bool:
        """¿Es el mismo sitio? Afinar la búsqueda o cambiar de década no lleva a otro."""
        return other is not None and (
            self.section, _view_keys(self.views), self.page, self.searching) == (
            other.section, _view_keys(other.views), other.page, other.searching)


def _view_keys(views: tuple) -> tuple:
    return tuple((v[0], v[1]) for v in views)


def section_key(keys) -> tuple:
    """Las claves del lateral en un orden fijo, para comparar sitios."""
    return tuple(sorted(keys, key=lambda item: (str(item[0]), str(item[1]))))


class History:
    def __init__(self, limit: int = LIMIT) -> None:
        self.entries: list[Place] = []
        self.index = -1
        self.limit = limit

    @property
    def current(self) -> Place | None:
        return self.entries[self.index] if 0 <= self.index < len(self.entries) else None

    @property
    def can_back(self) -> bool:
        return self.index > 0

    @property
    def can_forward(self) -> bool:
        return 0 <= self.index < len(self.entries) - 1

    def peek_back(self) -> Place | None:
        return self.entries[self.index - 1] if self.can_back else None

    def peek_forward(self) -> Place | None:
        return self.entries[self.index + 1] if self.can_forward else None

    def visit(self, place: Place) -> bool:
        """Se llega a `place`. Si es el mismo sitio, solo se actualiza; si no, se apila y el
        «Adelante» que hubiera se pierde. Devuelve True si es un sitio nuevo."""
        if place.same(self.current):
            self.entries[self.index] = place
            return False
        del self.entries[self.index + 1:]
        self.entries.append(place)
        if len(self.entries) > self.limit:
            del self.entries[:len(self.entries) - self.limit]
        self.index = len(self.entries) - 1
        return True

    def note(self, **changes) -> None:
        """Apunta en el sitio actual por dónde se iba (desplazamiento, selección, década…)."""
        if self.current is not None:
            self.entries[self.index] = replace(self.current, **changes)

    def back(self) -> Place | None:
        if not self.can_back:
            return None
        self.index -= 1
        return self.entries[self.index]

    def forward(self) -> Place | None:
        if not self.can_forward:
            return None
        self.index += 1
        return self.entries[self.index]

    def forget(self, gone) -> bool:
        """Quita los sitios que ya no existen (una lista borrada) sin dejar dos iguales seguidos.

        Si el sitio actual desaparece, la posición pasa al anterior que quede. Devuelve True si
        el actual desapareció (quien llama debe llevar la ventana a otro sitio)."""
        kept: list[Place] = []
        index = -1
        lost = False
        for i, place in enumerate(self.entries):
            if gone(place):
                lost = lost or i == self.index
                continue
            if kept and kept[-1].same(place):
                if i == self.index:
                    kept[-1] = place  # el actual trae lo último apuntado
            else:
                kept.append(place)
            if i <= self.index:
                index = len(kept) - 1
        self.entries = kept
        self.index = index if kept else -1
        if self.index < 0 and kept:
            self.index = 0
        return lost


class NavigationMixin:
    """Historial de la ventana. Necesita de la App: `sel_keys`, `views`, `active_page`,
    `pages`, `query`, `decade`, `side_items` y los métodos que se usan abajo."""

    def _init_navigation(self) -> None:
        self.nav = History()
        self._nav_busy = False       # se está cambiando de sitio: no se apunta nada a medias
        self._nav_searching = False  # el buscador tenía texto en el último cambio
        self._nav_x_at = 0.0         # último botón lateral del ratón (contra repeticiones)

    # ---------- dónde se está ----------
    def _page_name(self) -> str | None:
        return next((name for name, page in self.pages.items() if page is self.active_page),
                    None)

    def _here(self) -> Place:
        return Place(
            section=section_key(self.sel_keys),
            views=tuple((v["kind"], v["key"], v.get("title") or "", v.get("artist") or "")
                        for v in self.views),
            page=self._page_name(),
            query=self.query.get(),
            decade=self.decade,
            scroll=self._scroll_fraction(),
            selected=tuple(t["video_id"] for t in self._selected()),
        )

    def _note_here(self, force: bool = False) -> None:
        """Apunta en el sitio actual por dónde se iba, antes de dejarlo.

        Solo si la ventana aún enseña ese sitio; con `force`, aunque el buscador ya haya
        cambiado (la lista que se ve sigue siendo la del sitio que se deja)."""
        current = self.nav.current
        if current is None:
            return
        here = self._here()
        if here.same(current):
            self.nav.note(query=here.query, decade=here.decade, scroll=here.scroll,
                          selected=here.selected)
        elif force:
            self.nav.note(decade=here.decade, scroll=here.scroll, selected=here.selected)

    def _visited(self) -> None:
        self.nav.visit(self._here())
        self._sync_nav()

    @contextmanager
    def _navigating(self):
        """Envuelve una acción que cambia de sitio: apunta el que se deja y apila el nuevo.
        Dentro de otra (o mientras se vuelve a un sitio guardado) no hace nada más."""
        if self._nav_busy:
            yield
            return
        self._nav_busy = True
        self._note_here()
        try:
            yield
        finally:
            self._nav_busy = False
        self._visited()

    # ---------- Atrás y Adelante ----------
    def go_back(self, _e=None) -> str:
        if self.now_open:  # lo que está encima se cierra primero
            self.close_now_view()
            return "break"
        self._note_here()
        place = self.nav.back()
        if place is not None:
            self._go(place)
        return "break"

    def go_forward(self, _e=None) -> str:
        if self.now_open:
            self.close_now_view()
        self._note_here()
        place = self.nav.forward()
        if place is not None:
            self._go(place)
        return "break"

    def _mouse_side(self, forward: bool) -> str:
        """Botones laterales del ratón (Tk los llama 4 y 5 en Windows)."""
        import time
        now = time.monotonic()
        if now - self._nav_x_at < 0.12:  # un mismo clic no debe contar dos veces
            return "break"
        self._nav_x_at = now
        return self.go_forward() if forward else self.go_back()

    def _leave_search(self) -> bool:
        """Al borrar la búsqueda se vuelve a donde se estaba antes de empezar a buscar."""
        current, previous = self.nav.current, self.nav.peek_back()
        if current is None or not current.searching or previous is None or previous.searching:
            return False
        self.nav.back()
        self._go(previous)
        return True

    def _go(self, place: Place) -> None:
        """Deja la ventana en un sitio guardado (sin apilar nada)."""
        self._nav_busy = True
        try:
            self._restore_place(place)
        finally:
            self._nav_busy = False
        self._nav_searching = place.searching
        self._sync_nav()

    # ---------- etiquetas ----------
    def _section_label(self, section: tuple) -> str:
        keys = set(section)
        labels = [label for kind, name, label in self.side_items if (kind, name) in keys]
        if not labels:
            return "Inicio"
        return labels[0] if len(labels) == 1 else f"{labels[0]} y {len(labels) - 1} más"

    def _place_label(self, place: Place | None) -> str:
        """Nombre corto de un sitio, para «Atrás: …» y el enlace de vuelta de las páginas."""
        if place is None:
            return ""
        if place.page:
            page = self.pages.get(place.page)
            return getattr(page, "page_title", "") or "Página"
        if place.views:
            return place.views[-1][2] or "Página"
        if place.searching:
            return f"«{place.query.strip()}»"
        return self._section_label(place.section)
