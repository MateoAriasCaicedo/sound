"""El buscador: lo tuyo al instante y, al dejar de escribir, YouTube Music.

- Con tres letras empieza a buscar en YouTube Music; mientras llega, los resultados ya vistos
  de búsquedas parecidas salen como pre-resultados (la lista nunca se queda vacía al escribir).
- La primera página (20 canciones) llega con una sola petición; el resto se completa después.
- Los géneros de YouTube del lateral se buscan aquí también, intercalados si se marcan varios.

Mixin de la ventana principal (``main.App``).
"""
from __future__ import annotations

import itertools
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..core import log as _log
from ..core.filters import fold

log = _log.get("ui")
SEARCH_DEBOUNCE_MS = 200  # búsqueda automática en YT Music al dejar de escribir
SEARCH_MIN_CHARS = 3      # menos que esto no merece una petición
SEARCH_FIRST_PAGE = 20    # lo que se pide primero: una sola petición, resultados ya
SEARCH_LIMIT = 40         # el resto se completa luego, sin hacer esperar


class SearchMixin:
    # ---------- buscador ----------
    def _on_query(self) -> None:
        q = self.query.get().strip()
        tracking = not self._nav_busy  # al volver a un sitio guardado no se apunta nada
        was = self._nav_searching
        self._nav_searching = bool(q)
        if tracking and q and not was:
            self._note_here(force=True)  # la lista aún enseña el sitio que se deja
        if q:
            self.close_views()  # buscar sale de la página de álbum o artista…
            self._hide_page()   # …y de Ajustes, Importar o Descargas
        if q and self.view_kind in ("home", "liked", "recent", "top", "playlist"):
            # buscar desde una vista especial busca en toda la biblioteca
            self.view_kind = "all"
            self.genre_filter = set()
            self.yt_sel = []
            self.sel_keys = {("all", None)}
            self._build_sidebar()
        self.search_id += 1  # lo que esté en vuelo ya no corresponde a lo que se está escribiendo
        self.searching = len(q) >= SEARCH_MIN_CHARS
        self._show_pre_results(q)
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        if tracking and was and not q and self._leave_search():
            return  # al borrar la búsqueda se vuelve a donde se estaba antes de buscar
        self.refresh_view()
        if len(q) >= SEARCH_MIN_CHARS:  # búsqueda automática en YT Music al dejar de escribir
            if len(q) == SEARCH_MIN_CHARS:
                # primera pista ya, mientras se sigue escribiendo: el resto se completa después
                self.search("songs", auto=True)
            self._qdebounce = self.after(
                SEARCH_DEBOUNCE_MS, lambda: self.search("songs", auto=True))
        if tracking:
            self._visited()

    def _show_pre_results(self, q: str) -> None:
        """Pinta al instante resultados ya vistos para esta búsqueda o para un prefijo suyo.

        Así la lista nunca se queda vacía ni parpadea al escribir: los pre-resultados ocupan
        su sitio y se sustituyen cuando llega la respuesta definitiva de YouTube.
        """
        if not q:
            if self.remote_src in ("songs", "genre"):
                self.remote, self.remote_src, self.remote_q = [], None, ""
            return
        from ..core import ytsearch
        hit = ytsearch.cached_songs(q)
        if hit is not None:
            if self.remote_src not in ("songs", "genre", None):
                return  # hay una vista abierta (géneros, importación): no se pisa
            _query, tracks = hit
            self.remote, self.remote_src, self.remote_q = tracks, "songs", q
            self.searching = False  # ya hay algo que enseñar
            return
        if self.remote_src not in ("songs", "genre"):
            return
        if len(q) < SEARCH_MIN_CHARS:
            # Sin búsqueda automática no habría quien sustituya estos resultados: se quitan.
            self.remote, self.remote_src, self.remote_q = [], None, ""
            return
        if self._related_query(q, self.remote_q):
            # La consulta nueva continúa (o recorta) la anterior: sus resultados aún sirven.
            self.remote_q = q
            return
        self.remote, self.remote_src, self.remote_q = [], None, ""

    @staticmethod
    def _related_query(a: str, b: str) -> bool:
        """¿Una consulta es prefijo de la otra? Entonces lo que ya se vio sigue siendo útil."""
        a, b = a.casefold().strip(), (b or "").casefold().strip()
        return bool(a and b and (a.startswith(b) or b.startswith(a)))

    @staticmethod
    def _warm_search() -> None:
        from ..core import ytsearch
        ytsearch.warm_up()

    def _rebuild_search_cache(self) -> None:
        self._search_cache = {
            t["video_id"]: fold(f"{t['title']} {t['artist']} {t.get('album') or ''}")
            for t in self.library
        }

    def _search_text(self, t: dict) -> str:
        """Texto normalizado de una fila; se memoriza porque la búsqueda corre en cada tecla."""
        vid = t.get("video_id")
        if vid and vid in self._search_cache:
            return self._search_cache[vid]
        text = fold(f"{t['title']} {t['artist']} {t.get('album') or ''}")
        if vid:
            self._search_cache[vid] = text
        return text


    def _focus_search(self, _e=None) -> str:
        """Ctrl+F: el buscador queda tapado por la vista en grande; primero se cierra."""
        self.close_now_view()
        self.entry.focus_set()
        return "break"

    # ---------- búsqueda en YT Music ----------
    def _load_yt_genres(self) -> None:
        def work() -> None:
            try:
                from ..core import genres
                # géneros fijos del sidebar
                self.q.put(("genres", genres.sidebar_genres()))
            except Exception as exc:  # noqa: BLE001 - sin internet
                _log.failure(log, "no se pudieron cargar los géneros de YT Music", exc)
                self.q.put(("genres", None))

        threading.Thread(target=work, daemon=True).start()

    def _tag_results(self, tracks: list[dict], genre: str | None) -> list[dict]:
        for t in tracks:
            t["_genre"] = genre
        return tracks

    def _search_in_flight(self, q: str) -> bool:
        """¿Ya hay una petición en curso para esta misma consulta? No se duplica."""
        running = self._remote_inflight
        return bool(running and running[0] == q and time.monotonic() - running[1] < 15)

    def search(self, kind: str = "songs", auto: bool = False) -> None:
        q = self.query.get().strip()
        if not q:
            return
        if kind == "songs" and self._search_in_flight(q):
            return
        if self.active_page is not None:
            self.close_page()
        if self._qdebounce:
            self.after_cancel(self._qdebounce)
            self._qdebounce = None
        self.searching = True
        self.status.configure(text="Buscando en YouTube…")
        self.search_id += 1
        sid = self.search_id
        marker = None
        if kind == "songs":
            marker = self._remote_inflight = (q, time.monotonic())

        def work() -> None:
            try:
                if sid != self.search_id:  # mientras se lanzaba el hilo se escribió otra cosa
                    return
                from ..core import genres, ytsearch
                if kind == "genre":
                    # solo canciones nuevas: lo que ya está descargado no ocupa sitio en los 100
                    skip_ids, skip_songs = genres.known_from_library(include_queue=False)
                    label, tracks = genres.select_tracks(
                        q, 100, skip_ids=skip_ids, skip_songs=skip_songs)
                    self._tag_results(tracks, label)
                    self.q.put(("results", sid, tracks, kind, q, True))
                    return
                # Primero una sola página (una petición): resultados visibles cuanto antes.
                tracks = self._tag_results(ytsearch.search_songs(q, SEARCH_FIRST_PAGE), None)
                if sid != self.search_id:
                    return
                if len(tracks) < SEARCH_FIRST_PAGE or SEARCH_LIMIT <= SEARCH_FIRST_PAGE:
                    self.q.put(("results", sid, tracks, kind, q, True))
                    return
                self.q.put(("results", sid, tracks, kind, q, False))
                if sid != self.search_id:
                    return
                full = self._tag_results(ytsearch.search_songs(q, SEARCH_LIMIT), None)
                self.q.put(("results", sid, full, kind, q, True))
            except Exception as exc:  # noqa: BLE001 - sin internet, etc.
                _log.failure(log, f"búsqueda fallida ({kind}: {q!r})", exc)
                self.q.put(("remote_error", sid, str(exc)))
            finally:
                if marker is not None and self._remote_inflight is marker:
                    self._remote_inflight = None

        threading.Thread(target=work, daemon=True, name="buscar-youtube").start()

    def _search_genres(self) -> None:
        names = list(self.yt_sel)
        if not names:
            return
        self.searching = True
        self.status.configure(text=f"Buscando {', '.join(names)} en YouTube…")
        self.search_id += 1
        sid = self.search_id

        def work() -> None:
            try:
                from ..core import genres
                per = max(10, math.ceil(100 / len(names)))
                skip_ids, skip_songs = genres.known_from_library(include_queue=False)
                with ThreadPoolExecutor(3) as ex:
                    results = list(ex.map(lambda g: (g, genres.select_tracks(
                        g, per, skip_ids=skip_ids, skip_songs=skip_songs)[1]), names))
                lists = [self._tag_results(tr, g) for g, tr in results]
                mixed, seen = [], set()
                for batch in itertools.zip_longest(*lists):  # intercala los géneros
                    for t in batch:
                        if t and t["video_id"] not in seen:
                            seen.add(t["video_id"])
                            mixed.append(t)
                self.q.put(("results", sid, mixed, "genres", ""))
            except Exception as exc:  # noqa: BLE001
                _log.failure(log, f"búsqueda por géneros fallida ({names})", exc)
                self.q.put(("remote_error", sid, str(exc)))

        threading.Thread(target=work, daemon=True).start()
