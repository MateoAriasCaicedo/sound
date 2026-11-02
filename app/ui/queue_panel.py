"""Página de la cola de descargas: qué se está bajando, qué espera y qué falló.

Permite pausar y reanudar las descargas y reintentar las fallidas. Se refresca solo mientras
la página está abierta (lee la cola de la base de datos, así que también refleja lo que haga la
consola).
"""
import tkinter as tk
from tkinter import ttk

from ..core import errors, library
from .page import Page
from .widgets import PillButton

REFRESH_MS = 1000
ROW_LIMIT = 300
STATE_TEXT = {"running": "descargando", "pending": "en cola", "failed": "falló"}
COLS = (("state", "Estado", 90), ("artist", "Artista", 170), ("title", "Título", 230),
        ("tries", "Intentos", 62), ("detail", "Detalle", 250))


class DownloadsPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Descargas", status="Cola de descargas")
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

        from ..core import downloader

        self.settings = tk.Frame(b)
        self.settings.grid(row=1, column=0, sticky="ew", pady=(int(12 * self.k), 0))
        self.cookie_label = tk.Label(self.settings, text="Cookies de YouTube:",
                                     font=self.fonts["body"])
        self.cookie_label.pack(side="left")
        self.cookie_var = tk.StringVar(value=self.app.cookies)
        self.cookie_box = ttk.Combobox(
            self.settings, textvariable=self.cookie_var, state="readonly",
            width=12, values=("", *downloader.COOKIE_BROWSERS), font=self.fonts["body"])
        self.cookie_box.pack(side="left", padx=(int(8 * self.k), 0))
        self.cookie_box.bind("<<ComboboxSelected>>",
                             lambda _e: self.app.set_cookies(self.cookie_var.get()))
        self.cookie_hint = tk.Label(
            self.settings,
            text="  Elige el navegador donde tengas la sesión de YouTube iniciada si aparece "
                 "«Sign in to confirm you're not a bot».",
            font=self.fonts["small"], anchor="w")
        self.cookie_hint.pack(side="left", padx=(int(8 * self.k), 0))

        self.buttons = tk.Frame(b)
        self.buttons.grid(row=2, column=0, sticky="ew", pady=(int(14 * self.k), 0))
        self.buttons.columnconfigure(3, weight=1)
        self.btn_pause = PillButton(self.buttons, self.app.anim, self.k, "Pausar",
                                    self.toggle_pause, self.fonts["body"],
                                    variant="primary", min_width=112)
        self.btn_pause.grid(row=0, column=0, sticky="w")
        self.btn_retry = PillButton(self.buttons, self.app.anim, self.k, "Reintentar",
                                    self.retry_failed, self.fonts["body"],
                                    variant="secondary", min_width=170)
        self.btn_retry.grid(row=0, column=1, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_health = PillButton(self.buttons, self.app.anim, self.k, "Diagnóstico",
                                     self.app.open_health, self.fonts["body"],
                                     variant="ghost", min_width=118)
        self.btn_health.grid(row=0, column=2, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_update = PillButton(self.buttons, self.app.anim, self.k, "Actualizar yt-dlp",
                                     self.app.update_components, self.fonts["body"],
                                     variant="ghost", min_width=158)
        self.btn_update.grid(row=0, column=4, padx=(int(18 * self.k), 0), sticky="e")

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

    # ---------- vista ----------
    def _restyle_content(self) -> None:
        pal = self.app.pal
        self._pal = pal
        self.table.configure(bg=pal["bg"])
        self.buttons.configure(bg=pal["bg"])
        self.settings.configure(bg=pal["bg"])
        self.cookie_label.configure(bg=pal["bg"], fg=pal["fg"])
        self.cookie_hint.configure(bg=pal["bg"], fg=pal["dim"])
        self.tree.tag_configure("running", foreground=pal["accent"])
        self.tree.tag_configure("pending", foreground=pal["fg"])
        self.tree.tag_configure("failed", foreground=pal["danger"])
        style = ttk.Style(self)
        style.configure("Sound.TCombobox", fieldbackground=pal["field"], background=pal["field"],
                        foreground=pal["fg"], arrowcolor=pal["fg"], bordercolor=pal["line"])
        self.cookie_box.configure(style="Sound.TCombobox")
        for button in (self.btn_pause, self.btn_retry, self.btn_health, self.btn_update):
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], on_accent=pal["accent_fg"],
                           danger=pal["danger"])

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
            detail = (errors.friendly(row["error"]) if row["error"] else
                      ("al frente de la cola" if row["status"] == "pending"
                       and row["priority"] > 0 else ""))
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

        if self.cookie_var.get() != self.app.cookies:
            self.cookie_var.set(self.app.cookies)
        self.btn_pause.configure(text="Reanudar" if paused else "Pausar")
        self.btn_pause.state(["!disabled"] if service else ["disabled"])
        self.btn_retry.configure(text=f"Reintentar ({failed})" if failed else "Reintentar")
        self.btn_retry.state(["!disabled"] if failed and service else ["disabled"])
