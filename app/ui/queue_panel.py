"""Página de la cola de descargas: qué se está bajando, qué espera y qué falló.

Permite pausar y reanudar las descargas y reintentar las fallidas. Se refresca solo mientras
la página está abierta (lee la cola de la base de datos, así que también refleja lo que haga la
consola).
"""
import tkinter as tk
from tkinter import ttk

from ..core import library
from .page import Page
from .widgets import PillButton

REFRESH_MS = 1000
ROW_LIMIT = 300
STATE_TEXT = {"running": "descargando", "pending": "en cola", "failed": "falló"}
EDITABLE = ("pending", "failed")  # una canción que ya se está bajando no se puede quitar ni mover
HINT = ("Selecciona canciones (Ctrl+clic, Mayús+clic o Ctrl+A)  ·  Supr las quita  ·  "
        "Al frente reintenta las fallidas")
COLS = (("state", "Estado", 90), ("artist", "Artista", 170), ("title", "Título", 230),
        ("tries", "Intentos", 62), ("detail", "Detalle", 250))


class DownloadsPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Descargas", hint="Ctrl+A selecciona · Supr quita",
                         status="Cola de descargas")
        self._job = None
        self._pal = None

        b = self.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(0, weight=1)

        self.table = tk.Frame(b)
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.rowconfigure(0, weight=1)
        self.table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(self.table, columns=[c[0] for c in COLS], show="headings",
                                 selectmode="extended")
        for key, text, width in COLS:
            self.tree.heading(key, text=text, anchor="e" if key == "tries" else "w")
            self.tree.column(key, width=int(width * self.k), anchor="e" if key == "tries" else "w",
                             stretch=key in ("title", "artist", "detail"))
        self.scroll = ttk.Scrollbar(self.table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_selection_buttons())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Control-a>", self._select_all)

        self.hint = tk.Label(b, anchor="w", text=HINT, font=self.fonts["small"], justify="left")
        self.hint.grid(row=1, column=0, sticky="ew", pady=(int(8 * self.k), 0))

        self.buttons = tk.Frame(b)
        self.buttons.grid(row=2, column=0, sticky="ew", pady=(int(14 * self.k), 0))
        self.buttons.columnconfigure(4, weight=1)
        self.btn_pause = PillButton(self.buttons, self.app.anim, self.k, "Pausar",
                                    self.toggle_pause, self.fonts["body"],
                                    variant="primary", min_width=112)
        self.btn_pause.grid(row=0, column=0, sticky="w")
        self.btn_retry = PillButton(self.buttons, self.app.anim, self.k, "Reintentar fallidas",
                                    self.retry_failed, self.fonts["body"],
                                    variant="secondary", min_width=170)
        self.btn_retry.grid(row=0, column=1, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_front = PillButton(self.buttons, self.app.anim, self.k, "Al frente",
                                    self.prioritize_selected, self.fonts["body"],
                                    variant="secondary", min_width=104)
        self.btn_front.grid(row=0, column=2, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_remove = PillButton(self.buttons, self.app.anim, self.k, "Quitar",
                                     self.remove_selected, self.fonts["body"],
                                     variant="danger", min_width=94)
        self.btn_remove.grid(row=0, column=3, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_update = PillButton(self.buttons, self.app.anim, self.k, "Actualizar yt-dlp",
                                     self.app.update_components, self.fonts["body"],
                                     variant="ghost", min_width=158)
        self.btn_update.grid(row=0, column=5, padx=(int(18 * self.k), 0), sticky="e")

        self.restyle()
        self.refresh()

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        self.refresh()
        self._schedule()
        self.tree.focus_set()

    def deactivate(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(REFRESH_MS, self._tick)

    def _tick(self) -> None:
        self._job = None
        if self.app.active_page is not self:
            return
        self.refresh()
        self._schedule()

    # ---------- acciones ----------
    def toggle_pause(self) -> None:
        service = self.app.service
        if service is None:
            return
        service.resume() if service.paused else service.pause()
        self.refresh()

    def retry_failed(self) -> None:
        self.app.retry_failed_downloads()
        self.refresh()

    def selected_ids(self, only_editable: bool = True) -> list[str]:
        """Canciones seleccionadas, en el orden en que aparecen en la lista."""
        ids = sorted(self.tree.selection(), key=self.tree.index)
        if only_editable:
            ids = [i for i in ids if self.tree.item(i, "tags")[0] in EDITABLE]
        return ids

    def prioritize_selected(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.app.prioritize_downloads(ids)
            self.refresh()

    def remove_selected(self) -> str:
        ids = self.selected_ids(only_editable=False)  # las que ya se bajan se informan, no se ocultan
        if ids:
            self.app.remove_downloads(ids)
            self.refresh()
        return "break"

    def _select_all(self, _e=None) -> str:
        self.tree.selection_set(self.tree.get_children())
        return "break"

    # ---------- vista ----------
    def _restyle_content(self) -> None:
        pal = self.app.pal
        self._pal = pal
        for frame in (self.table, self.buttons):
            frame.configure(bg=pal["bg"])
        self.hint.configure(bg=pal["bg"], fg=pal["dim"])
        self.tree.tag_configure("running", foreground=pal["accent"])
        self.tree.tag_configure("pending", foreground=pal["fg"])
        self.tree.tag_configure("failed", foreground=pal["danger"])
        for button in (self.btn_pause, self.btn_retry, self.btn_front, self.btn_remove,
                       self.btn_update):
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], danger=pal["danger"])

    def _update_selection_buttons(self) -> None:
        """Al frente y Quitar solo valen para lo que no se está bajando ya."""
        usable = bool(self.app.service and self.selected_ids())
        for button in (self.btn_front, self.btn_remove):
            button.state(["!disabled"] if usable else ["disabled"])

    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        if self._pal is not self.app.pal:  # cambió el tema claro/oscuro
            self.restyle()
        counts = library.queue_counts()
        rows = library.queue_rows(ROW_LIMIT)
        service = self.app.service
        paused = bool(service and service.paused)

        selected, top = self.tree.selection(), self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            detail = row["error"] or ("al frente de la cola" if row["status"] == "pending"
                                      and row["priority"] > 0 else "")
            self.tree.insert("", "end", iid=row["video_id"], tags=(row["status"],), values=(
                STATE_TEXT.get(row["status"], row["status"]), row["artist"], row["title"],
                row["attempts"] or "", detail[:120]))
        keep = [i for i in selected if self.tree.exists(i)]  # la selección sobrevive al refresco
        if keep:
            self.tree.selection_set(keep)
        self.tree.yview_moveto(top)

        running, pending, failed = (counts.get(k, 0) for k in ("running", "pending", "failed"))
        parts = [f"{running} descargando", f"{pending} en cola", f"{failed} fallidas"]
        text = "  ·  ".join(parts) if running + pending + failed else "No hay descargas pendientes."
        if running + pending + failed > len(rows):
            text += f"   (se muestran las primeras {len(rows)})"
        if paused:
            text += "   —   EN PAUSA: lo que ya empezó termina, no se inicia nada nuevo"
        if self.app.active_page is self:
            self.app.status.configure(text=text)

        self.btn_pause.configure(text="Reanudar" if paused else "Pausar")
        self.btn_pause.state(["!disabled"] if service else ["disabled"])
        self.btn_retry.configure(text=f"Reintentar fallidas ({failed})" if failed else "Reintentar fallidas")
        self.btn_retry.state(["!disabled"] if failed and service else ["disabled"])
        self._update_selection_buttons()
