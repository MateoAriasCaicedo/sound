"""Página «Importar de YouTube Music»: pega un enlace y encola lo que aún no tengas.

Sustituye al cuadro de diálogo nativo por una página dentro de la ventana: comparte la paleta,
la tipografía y los botones del resto de la aplicación, y se vuelve con «← …» (Atrás) o Esc.
"""
import queue
import threading
import tkinter as tk

from ..core import importer
from ..core import log as _log
from .page import Page
from .theme import FONT_FAMILY
from .widgets import Field, PillButton, Switch

POLL_MS = 100
LIMIT = 200
INFO = ("Se encolan hasta 200 canciones; las que ya tienes en la biblioteca no se vuelven "
        "a descargar.")
EXAMPLES = ("Ejemplos:  music.youtube.com/playlist?list=…  ·  "
            "music.youtube.com/watch?v=…  ·  music.youtube.com/channel/…")

log = _log.get("ui")


class ImportPage(Page):
    def __init__(self, app) -> None:
        # el título dice qué es; el estado, qué se puede traer; el campo, dónde va el enlace
        super().__init__(app, "Importar de YouTube Music",
                         status="Un álbum, una playlist, un artista o una canción")
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self._poll_job: str | None = None
        self._status_error = False

        b = self.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(6, weight=1)
        self._source: str | None = None  # la playlist de YouTube del enlace que se importa
        self.bind("<Configure>", self._resized)


        self.top = tk.Frame(b)
        self.top.grid(row=1, column=0, sticky="ew")
        self.top.columnconfigure(0, weight=1)
        self.var_url = tk.StringVar()
        self.url_field = Field(self.top, self.app.anim, self.k, (FONT_FAMILY, 11),
                               self.var_url, hint="Pega aquí el enlace…", height=40)
        self.url_field.grid(row=0, column=0, sticky="ew")
        self.url_field.entry.bind("<Return>", lambda e: self.start())

        self.info = tk.Label(b, anchor="w", text=INFO, font=self.fonts["small"], justify="left")
        self.info.grid(row=2, column=0, sticky="ew", pady=(int(6 * self.k), 0))
        self.examples = tk.Label(b, anchor="w", text=EXAMPLES, font=self.fonts["small"],
                                 justify="left")
        self.examples.grid(row=3, column=0, sticky="ew", pady=(int(3 * self.k), 0))

        # una playlist de YouTube puede quedarse también como lista propia, en su orden
        self.save_row = tk.Frame(b)
        self.save_row.grid(row=4, column=0, sticky="w", pady=(int(14 * self.k), 0))
        self.save_switch = Switch(self.save_row, self.app.anim, self.k, self._toggle_save)
        self.save_switch.grid(row=0, column=0, rowspan=2, sticky="n", pady=(int(1 * self.k), 0))
        self.save_label = tk.Label(self.save_row, anchor="w", font=self.fonts["body"],
                                   text="Guardar las playlists también en «Tus listas»",
                                   cursor="hand2")
        self.save_label.grid(row=0, column=1, sticky="w", padx=(int(10 * self.k), 0))
        self.save_label.bind("<ButtonRelease-1>", lambda e: self._toggle_save())
        self.save_hint = tk.Label(self.save_row, anchor="w", font=self.fonts["small"],
                                  text="Con su nombre y en su orden. Si ya la habías importado, "
                                       "se le añaden las canciones nuevas.")
        self.save_hint.grid(row=1, column=1, sticky="w", padx=(int(10 * self.k), 0))

        self.status = tk.Label(b, anchor="w", justify="left", font=self.fonts["small"])
        self.status.grid(row=5, column=0, sticky="ew", pady=(int(10 * self.k), int(8 * self.k)))

        self.buttons = tk.Frame(b)
        self.buttons.grid(row=6, column=0, sticky="new")
        self.btn_import = PillButton(self.buttons, self.app.anim, self.k, "Importar",
                                     self.start, self.fonts["body"],
                                     variant="primary", min_width=124)
        self.btn_import.grid(row=0, column=0, sticky="w")
        self.btn_paste = PillButton(self.buttons, self.app.anim, self.k, "Pegar",
                                    self.paste, self.fonts["body"],
                                    variant="secondary", min_width=104)
        self.btn_paste.grid(row=0, column=1, padx=(int(8 * self.k), 0), sticky="w")

        self.restyle()
        self._set_busy(False)

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        self.url_field.focus_set()
        if self.worker is not None and self.worker.is_alive() and self._poll_job is None:
            self._poll_job = self.after(POLL_MS, self._poll)

    def deactivate(self) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        # una importación pedida aquí termina aunque cambies de página
        if self.worker is not None and self.worker.is_alive():
            self._poll_job = self.after(POLL_MS, self._poll)

    def _resized(self, e) -> None:
        wrap = max(int(280 * self.k), e.width - int(48 * self.k))
        self.info.configure(wraplength=wrap)
        self.examples.configure(wraplength=wrap)
        self.status.configure(wraplength=wrap)

    # ---------- acciones ----------
    def _toggle_save(self) -> None:
        self.app.import_as_playlist = not self.app.import_as_playlist
        self.app.save_prefs()
        self.save_switch.set(self.app.import_as_playlist)

    def paste(self) -> None:
        try:
            text = self.app.clipboard_get().strip()
        except tk.TclError:
            self._show_status("El portapapeles está vacío.", error=True)
            return
        if not text:
            self._show_status("El portapapeles está vacío.", error=True)
            return
        self.var_url.set(text)
        self.url_field.entry.icursor("end")
        self.url_field.focus_set()
        self._show_status("")

    def start(self) -> None:
        url = self.var_url.get().strip()
        if not url:
            self._show_status("Pega un enlace de YouTube Music.", error=True)
            self.url_field.focus_set()
            return
        if self.worker is not None and self.worker.is_alive():
            return
        self.var_url.set(url)
        self._source = importer.source_of(url)
        self._set_busy(True)
        self._show_status("Leyendo la lista de YouTube Music…")

        def work() -> None:
            try:
                label, tracks = importer.fetch(url, LIMIT)
            except importer.ImportFailure as exc:
                self.events.put(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001 - la ventana sigue funcionando
                _log.failure(log, "importación fallida", exc)
                self.events.put(("error", str(exc)))
            else:
                self.events.put(("done", label, tracks))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._poll_job is None:
            self._poll_job = self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "done":
                    self._finished(event[1], event[2])
                elif event[0] == "error":
                    self._failed(event[1])
        except queue.Empty:
            pass
        if (self._poll_job is None
                and ((self.worker is not None and self.worker.is_alive())
                     or not self.events.empty())):
            self._poll_job = self.after(POLL_MS, self._poll)

    # ---------- resultado ----------
    def _finished(self, label: str, tracks: list[dict]) -> None:
        self._set_busy(False)
        self.var_url.set("")
        self._show_status(f"«{label}»: {len(tracks)} canciones listas.")
        if self.app.active_page is self:
            self.app.close_page()
        source = self._source if self.app.import_as_playlist else None
        self.app.import_loaded(label, tracks, source=source)

    def _failed(self, message: str) -> None:
        self._set_busy(False)
        text = f"No se pudo importar: {message}"
        if self.app.active_page is self:
            self._show_status(text, error=True)
        else:
            self.app.status.configure(text=text)

    # ---------- vista ----------
    def _set_busy(self, busy: bool) -> None:
        self.btn_import.state(["disabled"] if busy else ["!disabled"])
        self.btn_paste.state(["disabled"] if busy else ["!disabled"])
        self.url_field.set_enabled(not busy)

    def _show_status(self, text: str, error: bool = False) -> None:
        self._status_error = error
        self.status.configure(text=text, fg=self.app.pal["danger" if error else "dim"])

    def _restyle_content(self) -> None:
        pal = self.app.pal
        for frame in (self.top, self.buttons, self.save_row):
            frame.configure(bg=pal["bg"])
        for label in (self.info, self.examples, self.status, self.save_label, self.save_hint):
            label.configure(bg=pal["bg"])
        self.save_label.configure(fg=pal["fg"])
        self.save_hint.configure(fg=pal["dim"])
        self.save_switch.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], accent=pal["accent"],
                                 on_accent=pal["accent_fg"])
        self.save_switch.set(self.app.import_as_playlist, animate=False)
        self.info.configure(fg=pal["dim"])
        self.examples.configure(fg=pal["faint"])
        self.status.configure(fg=pal["danger" if self._status_error else "dim"])
        self.url_field.restyle(bg=pal["bg"], field=pal["field"], panel=pal["panel"], fg=pal["fg"],
                               dim=pal["dim"], line=pal["line"], accent=pal["accent"])
        for button in (self.btn_import, self.btn_paste):
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], on_accent=pal["accent_fg"],
                           danger=pal["danger"])
