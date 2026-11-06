"""Página de diagnóstico: qué falta para que las descargas funcionen y dónde están los datos."""
import threading
import tkinter as tk

from ..core import config, health
from .page import Page
from .widgets import PillButton


class HealthPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Diagnóstico", hint="Entorno, cookies y carpetas",
                         status="Comprobando el entorno…")
        self._checks: list[dict] = []
        self._running = False
        self._pal = None

        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self.rows = tk.Frame(self.body)
        self.rows.grid(row=0, column=0, sticky="nsew")
        self.rows.columnconfigure(1, weight=1)

        self.buttons = tk.Frame(self.body)
        self.buttons.grid(row=1, column=0, sticky="ew", pady=(int(14 * self.k), 0))
        self.btn_check = PillButton(self.buttons, self.app.anim, self.k, "Recomprobar",
                                    self.refresh, self.fonts["body"], variant="primary",
                                    min_width=120)
        self.btn_check.grid(row=0, column=0, sticky="w")
        self.btn_data = PillButton(self.buttons, self.app.anim, self.k, "Abrir carpeta de datos",
                                   self.open_data, self.fonts["body"], variant="secondary",
                                   min_width=190)
        self.btn_data.grid(row=0, column=1, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_music = PillButton(self.buttons, self.app.anim, self.k, "Abrir carpeta de música",
                                    self.open_music, self.fonts["body"], variant="secondary",
                                    min_width=190)
        self.btn_music.grid(row=0, column=2, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_log = PillButton(self.buttons, self.app.anim, self.k, "Abrir registro",
                                  self.open_log, self.fonts["body"], variant="ghost",
                                  min_width=125)
        self.btn_log.grid(row=0, column=4, padx=(int(18 * self.k), 0), sticky="e")
        self.buttons.columnconfigure(3, weight=1)

        self.restyle()

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        self.refresh()

    def _restyle_content(self) -> None:
        pal = self.app.pal
        self._pal = pal
        self.rows.configure(bg=pal["bg"])
        self.buttons.configure(bg=pal["bg"])
        for button in (self.btn_check, self.btn_data, self.btn_music, self.btn_log):
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], on_accent=pal["accent_fg"],
                           danger=pal["danger"])
        self._paint()

    # ---------- acciones ----------
    def refresh(self) -> None:
        if self._running:
            return
        self._running = True
        self._set_status("Comprobando el entorno…")
        cookies = self.app.cookies

        def work() -> None:
            try:
                checks = health.diagnostics(cookies=cookies)
            except Exception as exc:  # noqa: BLE001 - el diagnóstico nunca debe romper la página
                checks = [{"name": "Diagnóstico", "status": "fail", "detail": str(exc)}]
            self.app.q.put(("health", checks))

        threading.Thread(target=work, name="diagnostico", daemon=True).start()

    def set_checks(self, checks: list[dict]) -> None:
        self._running = False
        self._checks = checks
        self._build_rows()
        failures = sum(1 for c in checks if c["status"] == "fail")
        warnings = sum(1 for c in checks if c["status"] == "warn")
        if failures:
            self._set_status(f"{failures} problema(s) que impiden descargar · "
                             f"{warnings} aviso(s)")
        elif warnings:
            self._set_status(f"Todo listo · {warnings} aviso(s) opcional(es)")
        else:
            self._set_status("Todo listo")

    def open_data(self) -> None:
        health.open_path(config.DATA_DIR)

    def open_music(self) -> None:
        health.open_path(config.MUSIC_DIR)

    def open_log(self) -> None:
        health.open_path(config.LOG_PATH)

    # ---------- vista ----------
    def _set_status(self, text: str) -> None:
        if self.app.active_page is self:
            self.app.status.configure(text=text)

    def _build_rows(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        for index, check in enumerate(self._checks):
            name = tk.Label(self.rows, text=check["name"], font=self.fonts["body"], anchor="w")
            name.grid(row=index, column=0, sticky="w", pady=(0, int(7 * self.k)))
            detail = tk.Label(self.rows, text=check["detail"], font=self.fonts["small"],
                              anchor="w", justify="left")
            detail.grid(row=index, column=1, sticky="w", padx=(int(14 * self.k), 0),
                        pady=(0, int(7 * self.k)))
            symbol = {"ok": "✓", "warn": "!", "fail": "✗"}.get(check["status"], "●")
            dot = tk.Label(self.rows, text=symbol, font=self.fonts["strong"])
            dot.grid(row=index, column=2, sticky="e", padx=(int(8 * self.k), 0),
                     pady=(0, int(7 * self.k)))
            self._paint_row(name, detail, dot, check["status"])

    def _paint(self) -> None:
        if not self._checks:
            return
        # al cambiar de tema se reconstruyen las filas con los colores nuevos
        self._build_rows()

    def _paint_row(self, name, detail, dot, status: str) -> None:
        pal = self.app.pal
        for widget in (name, detail, dot):
            widget.configure(bg=pal["bg"])
        name.configure(fg=pal["fg"])
        detail.configure(fg=pal["dim"])
        dot.configure(fg={"ok": pal["accent"], "warn": pal["dim"],
                          "fail": pal["danger"]}.get(status, pal["dim"]))
