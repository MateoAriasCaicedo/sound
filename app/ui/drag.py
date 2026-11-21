"""Arrastrar canciones de la lista: soltarlas en una lista del lateral o reordenar una lista.

- Desde cualquier vista en modo lista (Todo, una búsqueda, un álbum…), arrastrar lo elegido y
  soltarlo sobre una lista de «Tus listas» la añade ahí (con su aviso y «Deshacer»).
- Dentro de una lista, en su orden y sin filtros, soltar entre dos filas mueve ahí lo elegido.

Pulsar sobre una fila ya elegida (de varias) no deshace la selección hasta soltar sin moverse:
así se puede arrastrar un grupo entero. `new_order` es pura para poder probarla sin Tk.
"""
from __future__ import annotations

import tkinter as tk

from ..core import playlists as playlists_store
from .theme import blend


def new_order(order: list[str], picked: set[str], slot: int) -> list[str]:
    """El orden tras soltar las elegidas antes de la fila `slot` (len(order): al final)."""
    moving = [vid for vid in order if vid in picked]
    rest = [vid for vid in order if vid not in picked]
    at = sum(1 for vid in order[:slot] if vid not in picked)
    return rest[:at] + moving + rest[at:]


class DragMixin:
    def _init_drag(self) -> None:
        self._drag: dict | None = None
        self._drag_ghost: tk.Label | None = None
        self._drag_line: tk.Frame | None = None

    def _bind_drag(self) -> None:
        self.tree.bind("<ButtonPress-1>", self._drag_press, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._drag_release, add="+")

    @property
    def dragging(self) -> bool:
        return bool(self._drag and self._drag["active"])

    def _drag_press(self, e) -> str | None:
        iid = self.tree.identify_row(e.y)
        self._drag = None
        if not iid.isdigit() or self.tree.identify_region(e.x, e.y) != "cell":
            return None
        selection = self.tree.selection()
        keep = iid in selection and len(selection) > 1 and not e.state & 0x5  # sin Ctrl/Mayús
        self._drag = {"iid": iid, "x": e.x_root, "y": e.y_root, "active": False, "keep": keep,
                      "target": None}
        return "break" if keep else None  # la selección se decide al soltar

    def _drag_motion(self, e) -> str | None:
        d = self._drag
        if d is None:
            return None
        if not d["active"]:
            if abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) < int(8 * self.k):
                return "break" if d["keep"] else None
            if not self._selected():
                return None
            d["active"] = True
            self.tree_tip.hide()
            self._show_ghost()
        self._move_ghost(e.x_root, e.y_root)
        d["target"] = self._drop_target(e.x_root, e.y_root)
        return "break"  # que la tabla no siga ampliando la selección mientras se arrastra

    def _drag_release(self, e) -> str | None:
        d, self._drag = self._drag, None
        if d is None:
            return None
        if not d["active"]:
            if d["keep"]:  # un clic sin arrastrar sobre lo elegido: queda solo esa fila
                self.tree.selection_set(d["iid"])
                self.tree.focus(d["iid"])
            return None
        target = self._drop_target(e.x_root, e.y_root)
        self._end_drag_feedback()  # después: calcular el destino vuelve a dibujar la línea
        tracks = self._selected()
        if target is None or not tracks:
            return "break"
        if target[0] == "pl":
            self.add_to_playlist(target[1], tracks)
        elif target[0] == "slot" and self._can_reorder():
            picked = [t["video_id"] for t in tracks]
            order = [t["video_id"] for t in self.rows]
            moved = new_order(order, set(picked), target[1])
            if moved != order:
                playlists_store.reorder(self.playlist_id, moved)
                self.refresh_view()
                self._reselect(picked)
        return "break"

    # ---------- dónde caería ----------
    def _drop_target(self, x_root: int, y_root: int) -> tuple | None:
        over = self.winfo_containing(x_root, y_root)
        if over is self.genres:
            i = self.genres.index_at(y_root - self.genres.winfo_rooty())
            item = self.side_items[i] if i is not None and i < len(self.side_items) else None
            self.genres._set_hover(i if item and item[0] == "pl" else None)
            self._show_drop_line(None)
            if item and item[0] == "pl":
                self._ghost_hint(f"Añadir a «{item[2]}»")
                return ("pl", item[1])
            self._ghost_hint(None)
            return None
        self.genres._set_hover(None)
        if over is self.tree and self._can_reorder():
            slot = self._slot_at(y_root - self.tree.winfo_rooty())
            self._show_drop_line(slot)
            self._ghost_hint("Mover aquí")
            return ("slot", slot)
        self._show_drop_line(None)
        self._ghost_hint(None)
        return None

    def _slot_at(self, y: int) -> int:
        """Antes de qué fila caería: la mitad de arriba de una fila, antes; la de abajo, después."""
        iid = self.tree.identify_row(y)
        if not iid.isdigit():
            return len(self.rows) if y > 0 else 0
        box = self.tree.bbox(iid)
        row = int(iid)
        return row + 1 if box and y > box[1] + box[3] / 2 else row

    # ---------- lo que se ve al arrastrar ----------
    def _show_ghost(self) -> None:
        tracks = self._selected()
        p = self.pal
        text = (f"♪  {tracks[0]['title']}" if len(tracks) == 1
                else f"♪  {len(tracks)} canciones")
        self._drag_text = text
        self._drag_ghost = tk.Label(self, text=text, font=self.fonts["small"], padx=10, pady=5,
                                    bg=blend(p["panel"], p["fg"], 0.12), fg=p["fg"])

    def _ghost_hint(self, hint: str | None) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.configure(
                text=self._drag_text + (f"   →   {hint}" if hint else ""))

    def _move_ghost(self, x_root: int, y_root: int) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.place(x=x_root - self.winfo_rootx() + int(14 * self.k),
                                   y=y_root - self.winfo_rooty() + int(10 * self.k))
            self._drag_ghost.lift()

    def _show_drop_line(self, slot: int | None) -> None:
        if slot is None:
            if self._drag_line is not None:
                self._drag_line.place_forget()
            return
        if self._drag_line is None:
            self._drag_line = tk.Frame(self.tree, height=max(2, int(2 * self.k)))
        self._drag_line.configure(bg=self.scheme.get("accent", self.pal["accent"]))
        if slot < len(self.rows) and self.tree.bbox(str(slot)):
            y = self.tree.bbox(str(slot))[1]
        elif self.rows and self.tree.bbox(str(len(self.rows) - 1)):
            x, y0, w, h = self.tree.bbox(str(len(self.rows) - 1))
            y = y0 + h
        else:
            y = 0
        self._drag_line.place(x=0, y=max(0, y - 1), relwidth=1)
        self._drag_line.lift()

    def _end_drag_feedback(self) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None
        self._show_drop_line(None)
        self.genres._set_hover(None)
