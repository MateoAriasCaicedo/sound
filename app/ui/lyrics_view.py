"""Panel de letra: texto de solo lectura con la línea actual resaltada y desplazamiento suave.

No sabe nada de red ni de reproducción: recibe la letra y la posición, y solo repinta cuando
cambia la línea. Así el coste es cero en los fotogramas en los que no pasa nada.
"""
import tkinter as tk
from tkinter import ttk

from ..core.lyrics import line_at


class LyricsView(tk.Frame):
    def __init__(self, master, k: float, fonts: dict) -> None:
        super().__init__(master)
        self.k, self.fonts = k, fonts
        pad = int(14 * k)
        self.text = tk.Text(self, wrap="word", bd=0, highlightthickness=0, cursor="arrow",
                            padx=pad, pady=int(4 * k), spacing1=int(5 * k),
                            spacing3=int(5 * k), takefocus=1)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.text.configure(state="disabled")
        self._lines: list[tuple[int, str]] = []
        self._index = -1

    def restyle(self, bg: str, fg: str, dim: str, faint: str, accent: str) -> None:
        self.configure(bg=bg)
        self.text.configure(bg=bg, fg=fg, insertbackground=fg, selectbackground=dim,
                            selectforeground=bg)
        self.text.tag_configure("cur", foreground=accent, font=self.fonts["strong"])
        self.text.tag_configure("near", foreground=fg)
        self.text.tag_configure("far", foreground=dim)
        self.text.tag_configure("plain", foreground=fg)
        self.text.tag_configure("faint", foreground=faint)

    def _replace(self, chunks: list[tuple[str, str]]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for content, tag in chunks:
            self.text.insert("end", content, tag)
        self.text.configure(state="disabled")

    def set_message(self, message: str) -> None:
        self._lines, self._index = [], -1
        self._replace([(message, "faint")])

    def show(self, payload: dict | None) -> None:
        """Muestra una letra sincronizada, texto plano o el aviso de que no hay."""
        self._lines, self._index = [], -1
        if not payload or payload.get("negative"):
            self.set_message("Sin letra para esta canción.")
            return
        synced = payload.get("synced") or []
        plain = (payload.get("plain") or "").strip()
        if synced:
            try:
                self._lines = [(int(ms), str(text)) for ms, text in synced]
            except (TypeError, ValueError):
                self.set_message("La letra de esta canción no se pudo leer.")
                return
            self._replace([(line + "\n", "far") for _ms, line in self._lines])
        elif plain:
            self._replace([(plain, "plain")])
        else:
            self.set_message("Sin letra para esta canción.")
            return
        self.text.yview_moveto(0)

    def sync(self, position_ms: int, force: bool = False) -> None:
        """Resalta la línea que toca y la deja a la vista; barato si no cambió."""
        if not self._lines:
            return
        index = line_at(self._lines, int(position_ms))
        if index == self._index and not force:
            return
        self.text.configure(state="normal")
        if self._index >= 0:
            self.text.tag_remove("cur", f"{self._index + 1}.0", f"{self._index + 2}.0")
            self.text.tag_add("far", f"{self._index + 1}.0", f"{self._index + 2}.0")
        if self._index - 1 >= 0:
            self.text.tag_remove("near", f"{self._index}.0", f"{self._index + 1}.0")
            self.text.tag_add("far", f"{self._index}.0", f"{self._index + 1}.0")
        if index >= 0:
            self.text.tag_remove("far", f"{index + 1}.0", f"{index + 2}.0")
            self.text.tag_add("cur", f"{index + 1}.0", f"{index + 2}.0")
        if index - 1 >= 0:
            self.text.tag_remove("far", f"{index}.0", f"{index + 1}.0")
            self.text.tag_add("near", f"{index}.0", f"{index + 1}.0")
        self._index = index
        self.text.configure(state="disabled")
        if index >= 0:
            self.text.see(f"{index + 1}.0")
