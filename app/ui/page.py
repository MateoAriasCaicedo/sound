"""Base de las páginas que viven dentro de la ventana, no en ventanas emergentes.

Cada página ocupa el área de contenido (debajo de la cabecera y encima del reproductor) y
comparte la misma barra de navegación, tipografías y paleta que el resto de la aplicación.
"""
import tkinter as tk

from .widgets import TextButton


class Page(tk.Frame):
    def __init__(self, app, page_title: str, hint: str = "", status: str = "") -> None:
        p = app.pal
        super().__init__(app.content, bg=p["bg"])
        self.app = app
        self.page_title = page_title
        self.initial_status = status
        self.k = app.k
        self.fonts = app.fonts
        pad = int(22 * self.k)

        self.toolbar = tk.Frame(self, bg=p["bg"])
        self.toolbar.pack(fill="x", padx=pad, pady=(int(10 * self.k), int(2 * self.k)))
        self.back = TextButton(self.toolbar, app.anim, "←  Biblioteca", self.fonts["body"],
                               app.close_page)
        self.back.pack(side="left")
        self.hint = tk.Label(self.toolbar, text=hint, font=self.fonts["small"], anchor="e")
        self.hint.pack(side="right")

        self.body = tk.Frame(self, bg=p["bg"])
        self.body.pack(fill="both", expand=True, padx=pad, pady=(0, int(14 * self.k)))

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        self.app.status.configure(text=self.initial_status)

    def deactivate(self) -> None:
        """Se llama al salir de la página: cada una cancela sus temporizadores."""

    def restyle(self) -> None:
        p = self.app.pal
        self.configure(bg=p["bg"])
        self.toolbar.configure(bg=p["bg"])
        self.back.restyle(p["bg"], p["fg"], p["dim"])
        self.hint.configure(bg=p["bg"], fg=p["dim"])
        self.body.configure(bg=p["bg"])
        self._restyle_content()

    def _restyle_content(self) -> None:
        """Colores propios de cada página; la base ya pintó la barra y el contenedor."""
