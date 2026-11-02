"""Página «Música por frase»: escribes qué quieres oír y cuántas canciones, y las busca (sin IA).

Previsualizar muestra las canciones encontradas con su año verificado; Descargar las manda a la
cola de descargas (si aún no se había previsualizado, busca y descarga de una vez).
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import requests

from ..core import genres, prompt
from .page import Page
from .theme import FONT_FAMILY
from .widgets import PillButton

EXAMPLES = ("Ejemplos:  música de los ochenta  ·  rock de los 90  ·  "
            "baladas románticas de los 70  ·  cumbia de los 2000")
POLL_MS = 100
MAX_N = 500
COLS = (("year", "Año", 60), ("artist", "Artista", 230), ("title", "Título", 380))


class PromptPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "Música por frase", hint="Sin IA: entiende décadas, idiomas y géneros",
                         status="Escribe una frase y previsualiza o descarga")
        self.events: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None
        self.result = None  # última búsqueda lista para descargar
        self.result_key: tuple[str, int] | None = None
        self._poll_job: str | None = None

        b = self.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(4, weight=1)
        self.bind("<Configure>", self._resized)

        self.heading = tk.Label(b, anchor="w", text="¿Qué quieres escuchar?",
                                font=self.fonts["strong"])
        self.heading.grid(row=0, column=0, sticky="ew", pady=(0, int(6 * self.k)))

        self.top = tk.Frame(b)
        self.top.grid(row=1, column=0, sticky="ew")
        self.top.columnconfigure(0, weight=1)
        self.var_text = tk.StringVar()
        # tk.Entry y no ttk.Entry: los colores salen de la paleta y se leen en ambos temas
        self.entry = tk.Entry(self.top, textvariable=self.var_text,
                              font=(FONT_FAMILY, 11), relief="flat", bd=0, highlightthickness=1)
        self.entry.grid(row=0, column=0, sticky="ew", ipady=6)
        self.entry.bind("<Return>", lambda e: self.start(auto_download=False))
        self.var_text.trace_add("write", lambda *a: self._update_understood())
        self.count_label = tk.Label(self.top, text="Cantidad", font=self.fonts["body"])
        self.count_label.grid(row=0, column=1, padx=(int(14 * self.k), int(6 * self.k)))
        self.var_n = tk.StringVar(value="50")
        self.spin = tk.Spinbox(self.top, from_=1, to=MAX_N, width=6, textvariable=self.var_n,
                               font=(FONT_FAMILY, 11), relief="flat", bd=0, highlightthickness=1)
        self.spin.grid(row=0, column=2, ipady=5)

        self.examples = tk.Label(b, anchor="w", text=EXAMPLES, font=self.fonts["small"])
        self.examples.grid(row=2, column=0, sticky="ew", pady=(int(6 * self.k), 0))
        self.understood = tk.Label(b, anchor="w", font=self.fonts["small"], justify="left")
        self.understood.grid(row=3, column=0, sticky="ew", pady=(int(4 * self.k), int(8 * self.k)))

        self.table = tk.Frame(b)
        self.table.grid(row=4, column=0, sticky="nsew")
        self.table.rowconfigure(0, weight=1)
        self.table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(self.table, columns=[c[0] for c in COLS], show="headings",
                                 selectmode="browse")
        for key, text, width in COLS:
            self.tree.heading(key, text=text, anchor="e" if key == "year" else "w")
            self.tree.column(key, width=int(width * self.k), anchor="e" if key == "year" else "w",
                             stretch=key != "year")
        self.scroll = ttk.Scrollbar(self.table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")

        self.status = tk.Label(b, anchor="w", justify="left", font=self.fonts["small"])
        self.status.grid(row=5, column=0, sticky="ew", pady=(int(8 * self.k), 0))

        self.buttons = tk.Frame(b)
        self.buttons.grid(row=6, column=0, sticky="ew", pady=(int(12 * self.k), 0))
        self.buttons.columnconfigure(3, weight=1)
        self.btn_search = PillButton(self.buttons, self.app.anim, self.k, "Previsualizar",
                                     lambda: self.start(False), self.fonts["body"],
                                     variant="secondary", min_width=124)
        self.btn_search.grid(row=0, column=0, sticky="w")
        self.btn_download = PillButton(self.buttons, self.app.anim, self.k, "Descargar",
                                       self.download, self.fonts["body"],
                                       variant="primary", min_width=124)
        self.btn_download.grid(row=0, column=1, padx=(int(8 * self.k), 0), sticky="w")
        self.btn_cancel = PillButton(self.buttons, self.app.anim, self.k, "Cancelar búsqueda",
                                     self.cancel, self.fonts["body"],
                                     variant="ghost", min_width=150)
        self.btn_cancel.grid(row=0, column=2, padx=(int(8 * self.k), 0), sticky="w")

        self.restyle()
        self._update_understood()
        self._set_busy(False)

    # ---------- ciclo de vida ----------
    def activate(self) -> None:
        super().activate()
        self.entry.focus_set()
        if self._poll_job is None:
            self._poll_job = self.after(POLL_MS, self._poll)

    def deactivate(self) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if (self.worker is not None and self.worker.is_alive()) or not self.events.empty():
            # sigue escuchando al hilo: una descarga pedida aquí debe completarse aunque cambies de página
            self._poll_job = self.after(POLL_MS, self._poll)

    def _resized(self, e) -> None:
        self.status.configure(wraplength=max(int(280 * self.k), e.width - int(48 * self.k)))

    # ---------- entrada ----------
    def _count(self) -> int:
        try:
            return max(1, min(MAX_N, int(self.var_n.get())))
        except ValueError:
            return 50

    def _update_understood(self) -> None:
        text = self.var_text.get().strip()
        if not text:
            self.understood.configure(text="")
            return
        parsed = prompt.parse(text)
        if parsed.constrained:
            self.understood.configure(text=f"Entendí  →  {parsed.describe()}")
        else:
            self.understood.configure(
                text="Sin década ni idioma en la frase: busco por género (prueba «de los 80» o «en español»).")

    # ---------- búsqueda ----------
    def _set_busy(self, busy: bool) -> None:
        for widget in (self.btn_search, self.btn_download):
            widget.state(["disabled"] if busy else ["!disabled"])
        for widget in (self.entry, self.spin):  # son tk (no ttk): se desactivan con state=
            widget.configure(state="disabled" if busy else "normal")
        self.btn_cancel.state(["!disabled"] if busy else ["disabled"])

    def start(self, auto_download: bool) -> None:
        text, n = self.var_text.get().strip(), self._count()
        if not text:
            self.status.configure(text="Escribe qué quieres escuchar.")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        self.var_n.set(str(n))
        self.tree.delete(*self.tree.get_children())
        self.result = None
        self.stop_flag.clear()
        self._set_busy(True)
        self.status.configure(text="Buscando…")

        def work() -> None:
            try:
                known_ids, known_songs = genres.known_from_library()  # solo novedades
                result = prompt.select_by_prompt(
                    text, n, skip_ids=known_ids, skip_songs=known_songs,
                    on_progress=lambda *a: self.events.put(("progress", *a)),
                    should_stop=self.stop_flag.is_set,
                )
                self.events.put(("done", result, auto_download, (text, n)))
            except Exception as exc:  # noqa: BLE001 - se muestra en la página
                self.events.put(("error", exc))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.stop_flag.set()
        self.status.configure(text="Cancelando: termino la búsqueda en curso y conservo lo encontrado…")

    def download(self) -> None:
        text, n = self.var_text.get().strip(), self._count()
        if self.result and self.result.tracks and self.result_key == (text, n):
            self._submit(self.result)
        else:
            self.start(auto_download=True)  # sin previsualizar (o la frase cambió): busca y descarga

    def _submit(self, result) -> None:
        self.app.dl.submit(result.tracks, result.label)
        self.status.configure(
            text=f"{len(result.tracks)} canciones de «{result.label}» añadidas a la cola. "
                 "Sigue el avance en «Descargas».")
        if self.app.active_page is self:
            self.app.status.configure(text=f"{len(result.tracks)} canciones de «{result.label}» en la cola")
        self.result = None  # no se manda dos veces la misma lista

    def _poll(self) -> None:
        self._poll_job = None
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    _kind, found, wanted, done, total = event
                    self.status.configure(
                        text=f"Buscando…  {found} de {wanted} canciones  (búsqueda {done} de {total})")
                elif event[0] == "done":
                    self._finished(*event[1:])
                elif event[0] == "error":
                    self._failed(event[1])
        except queue.Empty:
            pass
        alive = self.worker is not None and self.worker.is_alive()
        if self.app.active_page is self or alive or not self.events.empty():
            self._poll_job = self.after(POLL_MS, self._poll)

    def _finished(self, result, auto_download: bool, key: tuple[str, int]) -> None:
        self._set_busy(False)
        for track in result.tracks:
            self.tree.insert("", "end", values=(track.get("year") or "", track["artist"], track["title"]))
        if not result.tracks:
            self.status.configure(
                text=result.summary(key[1]) + ". Prueba con otra frase o con menos condiciones.")
            return
        self.status.configure(text=result.summary(key[1]))
        self.result, self.result_key = result, key
        if auto_download:
            self._submit(result)

    def _failed(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, (requests.RequestException, ConnectionError)):
            self.status.configure(text=f"No pude conectar con YouTube (¿sin internet?). {exc}")
        else:
            self.status.configure(text=f"No se pudo completar la búsqueda: {exc}")

    # ---------- vista ----------
    def _restyle_content(self) -> None:
        pal = self.app.pal
        for frame in (self.top, self.table, self.buttons):
            frame.configure(bg=pal["bg"])
        for label in (self.heading, self.count_label, self.examples, self.understood, self.status):
            label.configure(bg=pal["bg"])
        self.heading.configure(fg=pal["fg"])
        self.count_label.configure(fg=pal["dim"])
        self.examples.configure(fg=pal["faint"])
        self.understood.configure(fg=pal["accent"])
        self.status.configure(fg=pal["dim"])
        for field in (self.entry, self.spin):
            field.configure(bg=pal["field"], fg=pal["fg"], insertbackground=pal["fg"],
                            highlightbackground=pal["line"], highlightcolor=pal["accent"],
                            disabledbackground=pal["panel"], disabledforeground=pal["dim"])
        self.spin.configure(buttonbackground=pal["panel"])
        for button in (self.btn_search, self.btn_download, self.btn_cancel):
            button.restyle(bg=pal["bg"], fg=pal["fg"], dim=pal["dim"], faint=pal["faint"],
                           accent=pal["accent"], danger=pal["danger"])
