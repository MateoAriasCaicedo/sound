"""«Tus listas»: abrir, crear, renombrar, ordenar, exportar y borrar listas, y guardar como lista
una playlist importada de YouTube.

Mixin de la ventana principal (``main.App``): usa su estado (``playlists``, ``playlist_id``,
``rows``…) y sus piezas (``toast``, ``tree``, ``genres``…).
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from ..core import library
from ..core import log as _log
from ..core import playlists as playlists_store

log = _log.get("ui")


class PlaylistsMixin:
    # ---------- tus listas ----------
    def _in_playlist(self) -> bool:
        """¿Se está viendo una lista (sin búsqueda ni página encima)?"""
        return (self.view_kind == "playlist" and self.playlist_id is not None
                and self.sub_view is None and not self.query.get().strip())

    def _can_reorder(self) -> bool:
        """Subir y bajar solo tienen sentido viendo la lista en su orden, sin filtros."""
        return self._in_playlist() and self.sort is None and self.decade is None

    def _playlist_name(self, pid: int | None) -> str:
        return next((pl["name"] for pl in self.playlists if pl["id"] == pid), "")

    def _playlist_rows(self, pid: int | None) -> list[dict]:
        """Las canciones de una lista; las descargadas, con los datos de la biblioteca."""
        if pid is None:
            return []
        try:
            stored = playlists_store.tracks(pid)
        except Exception as exc:  # noqa: BLE001 - una lista ilegible se ve vacía, no rompe nada
            _log.failure(log, f"no se pudo leer la lista {pid}", exc)
            return []
        return [self.lib_by_id.get(t["video_id"]) or t for t in stored
                if t["video_id"] not in self._hidden]

    def _playlist_status(self, rows: list[dict]) -> str:
        if not rows:
            return ""
        n = len(rows)
        minutes = round(sum(t.get("duration") or 0 for t in rows) / 60)
        parts = [f"{n} {'canción' if n == 1 else 'canciones'}"]
        if minutes:
            parts.append(f"{minutes // 60} h {minutes % 60} min" if minutes >= 60
                         else f"{minutes} min")
        missing = sum(1 for t in rows if t["video_id"] not in self.lib_by_id)
        if missing:
            parts.append(f"{missing} sin descargar")
        return "  ·  ".join(parts)

    def _playlist_menu(self, parent, tracks: list[dict]) -> tk.Menu:
        """Submenú «Añadir a lista»: tus listas (✓ si ya tienen todo) y «Nueva lista…»."""
        menu = self._menu(parent)
        try:
            inside = playlists_store.containing([t["video_id"] for t in tracks])
        except Exception:  # noqa: BLE001 - solo es la marca
            inside = set()
        for pl in self.playlists:
            menu.add_command(label=pl["name"] + ("  ✓" if pl["id"] in inside else ""),
                             command=lambda pid=pl["id"]: self.add_to_playlist(pid, tracks))
        if self.playlists:
            menu.add_separator()
        menu.add_command(label="Nueva lista…", command=lambda: self.new_playlist(tracks))
        return menu

    def _playlist_index(self, pid: int) -> int | None:
        return next((i for i, (kind, n, _l) in enumerate(self.side_items)
                     if kind == "pl" and n == pid), None)

    def _select_playlist(self, pid: int) -> None:
        """Abre la lista como si se hubiera pulsado en el lateral."""
        if "lists" in self._side_folded:  # plegada, la lista nueva no estaría a la vista
            self._side_folded.discard("lists")
            self.save_prefs()
        self._build_sidebar()
        index = self._playlist_index(pid)
        if index is None:
            return
        self.genres.selection_clear()
        self.genres.selection_set(index)
        self.genres.see(index)
        self._pick_genre()

    def new_playlist(self, tracks: list[dict] | None = None) -> None:
        """Crea una lista y deja escribir su nombre ahí mismo, en el lateral. Con `tracks`
        (desde «Añadir a lista → Nueva lista…»), la lista nace con ellas y la vista no cambia."""
        try:
            pid = playlists_store.create(playlists_store.unique_name())
            if tracks:
                playlists_store.add(pid, tracks)
        except Exception as exc:  # noqa: BLE001 - se avisa y la ventana sigue
            _log.failure(log, "no se pudo crear la lista", exc)
            self.toast.show("No se pudo crear la lista")
            return
        if tracks:
            self._build_sidebar()
            n = len(tracks)
            self.toast.show(f"{n} {'canción añadida' if n == 1 else 'canciones añadidas'} "
                            "a la lista nueva")
        else:
            self._select_playlist(pid)
        self.after_idle(lambda: self.rename_playlist(pid))

    def rename_playlist(self, pid: int) -> None:
        """Un campo de texto sobre la fila de la lista: Intro guarda, Esc deja el nombre."""
        index = self._playlist_index(pid)
        if index is None:
            return
        side, k, p = self.genres, self.k, self.pal
        y, h = side.pos[index]
        if y < side.off or y + h > side.off + side.winfo_height():
            side._apply_scroll(y - side.winfo_height() / 3)
        old = self._playlist_name(pid)
        entry = tk.Entry(side, font=self.fonts["body"], relief="flat", bd=0, bg=p["raised"],
                         fg=p["fg"], insertbackground=p["fg"], selectbackground=p["sel"],
                         selectforeground=p["fg"], highlightthickness=max(1, int(k)),
                         highlightcolor=self.scheme.get("accent", p["accent"]),
                         highlightbackground=p["line"])
        entry.insert(0, old)
        entry.select_range(0, "end")
        entry.icursor("end")
        entry.place(x=int(6 * k), y=int(y - side.off + 2 * k), height=h - int(4 * k),
                    width=max(int(60 * k), side.winfo_width() - int(12 * k)))
        finished = []

        def finish(save: bool) -> None:
            if finished:
                return
            finished.append(True)
            name = entry.get().strip()
            entry.destroy()
            if save and name and name != old:
                playlists_store.rename(pid, name)
            self._build_sidebar()
            if self.view_kind == "playlist" and self.playlist_id == pid and self.active_page is None:
                self.header.set_title(self._view_title())
            (self.grid if self.view_mode == "grid" else self.tree).focus_set()

        entry.bind("<Return>", lambda e: (finish(True), "break")[1])
        entry.bind("<KP_Enter>", lambda e: (finish(True), "break")[1])
        entry.bind("<Escape>", lambda e: (finish(False), "break")[1])
        entry.bind("<FocusOut>", lambda e: finish(True))
        entry.focus_set()

    def add_to_playlist(self, pid: int, tracks: list[dict]) -> None:
        name = self._playlist_name(pid)
        try:
            present = {t["video_id"] for t in playlists_store.tracks(pid)}
            new = [t for t in tracks if t["video_id"] not in present]
            playlists_store.add(pid, new)
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudo añadir a la lista", exc)
            self.toast.show(f"No se pudo añadir a «{name}»")
            return
        self._build_sidebar()
        if self._in_playlist() and self.playlist_id == pid:
            self.refresh_view()
        if not new:
            self.toast.show(f"Ya {'estaba' if len(tracks) == 1 else 'estaban'} en «{name}»")
            return
        what = f"«{new[0]['title']}»" if len(new) == 1 else f"{len(new)} canciones"
        vids = [t["video_id"] for t in new]
        self.toast.show(f"{what} {'añadida' if len(new) == 1 else 'añadidas'} a «{name}»",
                        "Deshacer", on_action=lambda: self._undo_playlist_add(pid, vids))

    def _undo_playlist_add(self, pid: int, vids: list[str]) -> None:
        playlists_store.remove(pid, vids)
        self._build_sidebar()
        if self._in_playlist() and self.playlist_id == pid:
            self.refresh_view()

    def remove_from_playlist(self) -> None:
        """Quita lo seleccionado de la lista abierta (con «Deshacer»); el archivo no se toca."""
        pid, picks = self.playlist_id, self._selected()
        if pid is None or not picks:
            return
        before = [t["video_id"] for t in playlists_store.tracks(pid)]
        playlists_store.remove(pid, [t["video_id"] for t in picks])
        self.tree.selection_remove(*self.tree.selection())
        self._build_sidebar()
        self.refresh_view()
        what = f"«{picks[0]['title']}»" if len(picks) == 1 else f"{len(picks)} canciones"
        self.toast.show(f"{what} {'quitada' if len(picks) == 1 else 'quitadas'} de "
                        f"«{self._playlist_name(pid)}»", "Deshacer",
                        on_action=lambda: self._undo_playlist_remove(pid, picks, before))

    def _undo_playlist_remove(self, pid: int, picks: list[dict], before: list[str]) -> None:
        playlists_store.add(pid, picks)
        playlists_store.reorder(pid, before)
        self._build_sidebar()
        if self._in_playlist() and self.playlist_id == pid:
            self.refresh_view()
            self._reselect([t["video_id"] for t in picks])

    def _reselect(self, vids: list[str]) -> None:
        iids = [self.vid_iid[vid] for vid in vids if vid in self.vid_iid]
        if not iids:
            return
        self.tree.selection_set(iids)
        self._on_select()
        if self.view_mode == "grid":
            self.grid.see_row(int(iids[0]))
        else:
            self.tree.see(iids[0])

    def move_in_playlist(self, delta: int) -> None:
        """Sube (-1) o baja (+1) un puesto las canciones elegidas."""
        if not self._can_reorder():
            return
        picked = [t["video_id"] for t in self._selected()]
        order = [t["video_id"] for t in self.rows]
        new = playlists_store.moved(order, set(picked), delta)
        if new == order:
            return
        playlists_store.reorder(self.playlist_id, new)
        self.refresh_view()
        self._reselect(picked)

    def _move_key(self, delta: int) -> str | None:
        if not self._can_reorder():
            return None
        self.move_in_playlist(delta)
        return "break"

    def _play_playlist(self, pid: int) -> None:
        rows = self._playlist_rows(pid)
        if rows:
            self.play_tracks(rows, 0, label=f"Lista «{self._playlist_name(pid)}»")
        else:
            self.toast.show("La lista está vacía")

    def delete_playlist(self, pid: int) -> None:
        """Borra la lista (no las canciones); «Deshacer» la recupera con todo."""
        name = self._playlist_name(pid)
        try:
            tracks = playlists_store.tracks(pid)
            playlists_store.delete(pid)
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudo eliminar la lista", exc)
            self.toast.show(f"No se pudo eliminar «{name}»")
            return
        self.nav.forget(lambda place: ("pl", pid) in place.section)
        self._build_sidebar()  # también si era la que se veía: si no, seguía en el lateral
        if self.view_kind == "playlist" and self.playlist_id == pid:
            self.go_home()
        self._sync_nav()
        self.toast.show(f"Lista «{name}» eliminada", "Deshacer",
                        on_action=lambda: self._restore_playlist(name, tracks))

    def _restore_playlist(self, name: str, tracks: list[dict]) -> None:
        pid = playlists_store.create(name)
        playlists_store.add(pid, tracks)
        self._select_playlist(pid)

    def export_playlist(self, pid: int) -> None:
        name = self._playlist_name(pid) or "lista"
        dest = filedialog.asksaveasfilename(
            parent=self, title=f"Exportar «{name}»", defaultextension=".m3u",
            initialfile=re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") + ".m3u",
            filetypes=[("Lista M3U", "*.m3u"), ("Todos los archivos", "*.*")])
        if not dest:
            return
        try:
            count = library.export_m3u(Path(dest), tracks=self._playlist_rows(pid))
        except OSError as exc:
            self.toast.show(f"No se pudo exportar: {exc.strerror or exc}")
            return
        self.toast.show(f"{count} canciones exportadas a {Path(dest).name}")

    def _side_context(self, i: int, x_root: int, y_root: int) -> None:
        """Clic derecho en el lateral: las acciones de una lista."""
        if not 0 <= i < len(self.side_items):
            return
        kind, pid, _label = self.side_items[i]
        if kind != "pl":
            return
        m = self._menu()
        m.add_command(label="Reproducir", command=lambda: self._play_playlist(pid))
        m.add_command(label="Renombrar", command=lambda: self.rename_playlist(pid))
        m.add_command(label="Exportar a M3U…", command=lambda: self.export_playlist(pid))
        m.add_separator()
        m.add_command(label="Eliminar lista", command=lambda: self.delete_playlist(pid))
        try:
            m.tk_popup(x_root, y_root)
        finally:
            m.grab_release()

    # ---------- importar ----------
    def import_loaded(self, label: str, tracks: list[dict], source: str | None = None) -> None:
        """Muestra lo importado en la biblioteca y encola solo las canciones nuevas. Con
        `source` (una playlist de YouTube), la guarda además como lista propia y la abre."""
        if source and self._import_as_playlist(label, tracks, source):
            return
        with self._navigating():
            self._hide_page()
            self.close_views()
            self._show_imported(label, tracks)

    def _import_as_playlist(self, label: str, tracks: list[dict], source: str) -> bool:
        """Guarda la playlist importada en «Tus listas» (o completa la que ya se importó) y la
        abre. False si no se pudo: entonces se enseña como una importación normal."""
        try:
            pid, added, created = playlists_store.import_list(label, source, tracks)
        except Exception as exc:  # noqa: BLE001 - la importación sigue, sin lista
            _log.failure(log, "no se pudo guardar la playlist importada como lista", exc)
            self.toast.show(f"No se pudo guardar «{label}» en tus listas")
            return False
        new = [t for t in tracks if t["video_id"] not in self.lib_by_id]
        if new:
            self.dl.submit(new, None)
        self._select_playlist(pid)
        self.refresh_view()  # si ya estaba abierta, _select_playlist no la repinta
        name = self._playlist_name(pid) or label
        downloading = f", {len(new)} en descarga" if new else ""
        if created:
            n = len(tracks)
            self.toast.show(f"Lista «{name}» guardada en tus listas: "
                            f"{n} {'canción' if n == 1 else 'canciones'}{downloading}")
        elif added:
            self.toast.show(f"«{name}» ya estaba en tus listas: "
                            f"{added} {'canción nueva' if added == 1 else 'canciones nuevas'}"
                            f"{downloading}")
        else:
            self.toast.show(f"«{name}» ya estaba en tus listas y no hay canciones nuevas")
        return True

    def _show_imported(self, label: str, tracks: list[dict]) -> None:
        new = [t for t in tracks if t["video_id"] not in self.lib_by_id]
        self.view_kind = "all"
        self.genre_filter = set()
        self.yt_sel = []
        self.sel_keys = {("all", None)}
        known = {t["video_id"] for t in tracks}
        self.remote = tracks + [t for t in self.remote if t["video_id"] not in known]
        self.remote_src, self.remote_q = "import", ""
        self._build_sidebar()
        self.refresh_view()
        self._fade_rows()
        if new:
            self.dl.submit(new, None)
            self.status.configure(text=f"{label}: {len(new)} canciones en descarga")
        else:
            self.status.configure(text=f"{label}: ya estaban todas en tu biblioteca")
