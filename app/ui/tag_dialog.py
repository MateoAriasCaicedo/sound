"""Diálogo para editar las etiquetas de una canción descargada.

Escribe en el archivo y en la biblioteca con `metadata.edit` (no renombra ni mueve el archivo).
"""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import metadata
from .theme import style_titlebar

FIELDS = (("title", "Título"), ("artist", "Artista"), ("album", "Álbum"),
          ("genre", "Género"), ("year", "Año"))


class TagDialog(tk.Toplevel):
    def __init__(self, app, track: dict) -> None:
        super().__init__(app)
        self.app = app
        self.track = track
        self.k = app.k
        p = app.pal
        self.title("Editar etiquetas")
        self.configure(bg=p["bg"])
        self.resizable(False, False)
        self.transient(app)

        self.vars = {field: tk.StringVar(value="" if track.get(field) is None else str(track[field]))
                     for field, _label in FIELDS}
        body = tk.Frame(self, bg=p["bg"])
        body.pack(fill="both", expand=True, padx=int(18 * self.k), pady=int(16 * self.k))
        body.columnconfigure(1, weight=1)

        heading = tk.Label(body, text=f"{track.get('artist')} — {track.get('title')}",
                           font=app.fonts["strong"], bg=p["bg"], fg=p["fg"], anchor="w")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(10 * self.k)))

        for row, (field, label) in enumerate(FIELDS, start=1):
            text = tk.Label(body, text=label, font=app.fonts["body"], bg=p["bg"], fg=p["dim"],
                            anchor="w")
            text.grid(row=row, column=0, sticky="w", padx=(0, int(12 * self.k)),
                      pady=int(4 * self.k))
            entry = ttk.Entry(body, textvariable=self.vars[field], font=app.fonts["body"],
                              width=42)
            entry.grid(row=row, column=1, sticky="ew", pady=int(4 * self.k))
            if field == "title":
                self.first = entry

        note = tk.Label(body, text="Se actualizan el archivo y la biblioteca. "
                                   "El nombre del archivo no cambia.",
                        font=app.fonts["small"], bg=p["bg"], fg=p["faint"], anchor="w")
        note.grid(row=len(FIELDS) + 1, column=0, columnspan=2, sticky="ew",
                  pady=(int(10 * self.k), int(12 * self.k)))

        buttons = tk.Frame(body, bg=p["bg"])
        buttons.grid(row=len(FIELDS) + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="left")
        ttk.Button(buttons, text="Guardar", style="Accent.TButton",
                   command=self.save).pack(side="left", padx=(int(8 * self.k), 0))

        self.bind("<Return>", lambda _e: self.save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        style_titlebar(self, app.dark, p["bg"])
        self.grab_set()
        self.first.focus_set()
        self.first.selection_range(0, "end")

    def save(self) -> None:
        changes = {}
        for field, _label in FIELDS:
            before = self.track.get(field)
            before_text = "" if before is None else str(before).strip()
            after = self.vars[field].get().strip()
            if after != before_text:
                changes[field] = after
        if not changes:
            self.destroy()
            return
        try:
            updated = metadata.edit(self.track["video_id"], changes)
        except metadata.MetadataError as exc:
            messagebox.showerror("Editar etiquetas", str(exc), parent=self)
            return
        self.app.after_tag_edit(updated)
        self.destroy()
