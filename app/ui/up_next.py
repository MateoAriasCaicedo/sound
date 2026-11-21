"""«A continuación»: qué suena después y en qué orden.

- Empezar a sonar una vista o unas canciones (su contexto pasa a ser la cola).
- El panel lateral «A continuación»: saltar, mover y quitar canciones; encolar y vaciar.
- La cola automática: con internet, canciones parecidas (la «radio» de YouTube Music); sin
  internet, una cola de respaldo con tu biblioteca.

El modelo de la cola es ``playqueue.PlayQueue``; esto es su parte de ventana. Mixin de
``main.App``.
"""
from __future__ import annotations

import threading

from ..core import log as _log
from . import home
from .playback import fmt

log = _log.get("ui")
DRAWER_W = 340  # ancho del panel «A continuación»


class UpNextMixin:
    # ---------- encolar ----------
    def enqueue(self, tracks: list[dict], front: bool = False) -> None:
        if not tracks:
            return
        before = list(self.pq.up_next)
        if front:
            self.pq.up_next[0:0] = tracks
        else:
            self.pq.up_next.extend(tracks)
        self._refresh_queue()
        one = len(tracks) == 1
        what = f"«{tracks[0]['title']}»" if one else f"{len(tracks)} canciones"
        if front:
            where = "sonará a continuación" if one else "sonarán a continuación"
        else:
            where = "añadida a la cola" if one else "añadidas a la cola"
        self.toast.show(f"{what} {where}", "Deshacer",
                        on_action=lambda: self._restore_up_next(before))

    def clear_queue(self) -> None:
        before = list(self.pq.up_next)
        if not before:
            return
        self.pq.up_next.clear()
        self._refresh_queue()
        self.toast.show(f"Cola vaciada ({len(before)})", "Deshacer",
                        on_action=lambda: self._restore_up_next(before))

    def _restore_up_next(self, tracks: list[dict]) -> None:
        self.pq.up_next[:] = tracks
        self._refresh_queue()
        self._prefetch_next()


    # ---------- empezar a sonar una lista ----------
    def play_index(self, i: int) -> None:
        """Empieza a reproducir la lista visible desde la fila i."""
        if not 0 <= i < len(self.rows):
            return
        self._end_station()  # otra cosa para escuchar: la estación de Sound termina
        if self.sub_view is not None and self.sub_view["kind"] == "album":  # en su orden
            self.play_tracks(self.rows, i)
            return
        if self._in_playlist():  # una lista suena en su orden (y luego sigue la cola automática)
            self.play_tracks(self.rows, i, label=f"Lista «{self._view_title()}»")
            return
        t = self.rows[i]
        self.pq.album = False
        self._play_from = self._view_title()
        rows, first = self.rows, i
        if self.raw[2] is not None:  # en «Inicio», la lista es la estantería de esa canción
            start, end, title = home.section_of(self.sections, len(self.rows), i)
            rows, first = self.rows[start:end], i - start
            self._play_from = f"Inicio · {title}"
        if self.autoplay:  # cola automática inspirada en esta primera canción
            self.pq.ctx, self.pq.auto_ctx = [t], True
            self.pq.make_order(0)
            self._seed(t)
        else:
            self.pq.ctx, self.pq.auto_ctx = list(rows), False
            self.pq.make_order(first)
        self._play_track(t)

    def play_tracks(self, tracks: list[dict], first: int = 0, label: str | None = None) -> None:
        """Reproduce una lista concreta (un álbum) en su orden. Con la cola automática activa,
        al llegar a la última pista se piden canciones similares para seguir sin silencio."""
        if not 0 <= first < len(tracks):
            return
        self._end_station()
        self._play_from = label or f"Álbum «{tracks[first].get('album') or 'sin título'}»"
        self.pq.ctx, self.pq.auto_ctx = list(tracks), False
        self.pq.album = True
        self.pq.make_order(first)
        self._play_track(self.pq.ctx[first])

    def _continue_after_album(self, t: dict) -> None:
        """Suena la última pista del álbum: la cola automática toma el relevo desde ella."""
        pq = self.pq
        if not (pq.album and self.autoplay and pq.repeat == 0 and not pq.auto_ctx
                and pq.order and pq.opos >= len(pq.order) - 1):
            return
        pq.album = False
        pq.auto_ctx = True
        album = {x["video_id"] for x in pq.ctx}
        self._seed(t)
        pq.played |= album  # lo que ya sonó del álbum no vuelve en las similares


    # ---------- cola automática ----------
    def _genre_of(self, t: dict) -> str | None:
        return t.get("genre") or t.get("_genre") or (self.lib_by_id.get(t["video_id"]) or {}).get("genre")

    def _seed(self, t: dict) -> None:
        """Empieza una cola automática a partir de `t`."""
        self.pq.played = {t["video_id"]}
        self.seed_token += 1
        self.seed_genre = self._genre_of(t)
        self.queue_why = {}
        self.topping = False
        if self.online:
            self._fetch_radio(t)
        else:
            self._fill_backup()

    def _fetch_radio(self, t: dict) -> None:
        """Pide a YT Music canciones similares a `t` y las añade al final de la cola."""
        if self.topping:
            return
        self.topping, token = True, self.seed_token
        self.radio_seed = t

        def work() -> None:
            try:
                from ..core import ytsearch
                self.q.put(("radio", token, ytsearch.radio_tracks(t["video_id"], 25)))
            except Exception as exc:  # noqa: BLE001 - sin internet, corte de red, API cambiada
                _log.failure(log, f"no se pudieron pedir canciones similares a {t['video_id']}", exc)
                self.q.put(("radio_err", token, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _append_ctx(self, tracks: list[dict]) -> None:
        self.pq.append(tracks)
        self._refresh_queue()

    def _on_radio(self, token: int, tracks: list[dict]) -> None:
        self.topping = False
        if token != self.seed_token or not self.pq.auto_ctx:
            return
        have = {x["video_id"] for x in self.pq.ctx} | self.pq.played
        seed = self.radio_seed or {}
        reason = f"similar a {seed.get('artist') or seed.get('title') or 'la semilla'}"
        new = []
        for x in tracks:
            if x["video_id"] in have:
                continue
            x["_genre"] = self.seed_genre  # lo que se guarde por el respaldo cae en el mismo género
            new.append(self.lib_by_id.get(x["video_id"], x))  # si ya la tienes, suena desde el disco
            self.queue_why[x["video_id"]] = reason
        if new:
            self._append_ctx(new)
            source = (self.station_now or {}).get("title") or "Cola automática"
            self.status.configure(text=f"{source}: {len(new)} canciones parecidas más")
            self._prefetch_next()
            self.dl.download_ahead()
        else:
            self._fill_backup()

    def _on_radio_error(self, token: int, error: str) -> None:
        self.topping = False
        if token == self.seed_token and self.pq.auto_ctx:
            self._fill_backup()
            self.status.configure(text="No se pudo pedir canciones similares: cola de respaldo")

    def _fill_backup(self, n: int = 15) -> None:
        """Añade canciones de tu biblioteca: mismo género primero, otros géneros solo si no alcanzan."""
        if not self._station_keeps_going():
            return
        from ..core import autoqueue
        cur = self.pq.current(self.playing_vid)
        genre = (self._genre_of(cur) if cur else None) or self.seed_genre
        exclude = {x["video_id"] for x in self.pq.ctx} | self.pq.played
        tracks = autoqueue.backup_queue(
            self.library, genre, exclude, n,
            energy=self._track_energy, target_energy=self._track_energy(cur) if cur else None)
        if tracks:
            for track in tracks:
                self.queue_why[track["video_id"]] = autoqueue.reason_for(track, genre)
            self._append_ctx(tracks)
            same = sum(1 for x in tracks if (x.get("genre") or "").lower() == (genre or "").lower())
            self.status.configure(text=f"Cola de respaldo: {len(tracks)} canciones "
                                       f"({same} de {genre or 'sin género'})")

    def _top_up(self) -> None:
        """Mantiene unas cuantas canciones por delante: similares con internet, respaldo sin él.
        Con una estación de Sound sigue aunque la cola automática esté desactivada."""
        if not self._station_keeps_going() or self.pq.playable_ahead() >= 6:
            return
        cur = self.pq.current(self.playing_vid)
        if self.online and cur:
            self._fetch_radio(cur)
        elif not self.online:
            self._fill_backup()

    def toggle_auto(self) -> None:
        self.autoplay = not self.autoplay
        if self.autoplay and not self.pq.auto_ctx and self.playing_vid:
            cur = self.pq.current(self.playing_vid)
            if cur:  # convierte lo que suena en la semilla de una cola automática
                self.pq.ctx, self.pq.auto_ctx = [cur], True
                self.pq.album = False
                self.pq.make_order(0)
                self._seed(cur)
        elif not self.autoplay:
            self.pq.auto_ctx = False
            self._end_station()  # desactivarla también para una estación que sonara
        self._refresh_toggles()
        self.save_prefs()
        self.status.configure(text="Cola automática " + ("activada" if self.autoplay else "desactivada"))


    # ---------- panel «A continuación» ----------
    def _show_queue(self) -> None:
        """Abre o cierra el panel, que entra deslizándose por la derecha."""
        if self.now_open:  # con la vista en grande, la cola es una de sus pestañas
            view = self.now_view
            view.show_tab("album" if view.tab == "queue" else "queue")
            return
        opening = self.queue_win is None
        self.queue_win = self.drawer if opening else None
        self.queue_lb = (self.drawer_list if opening and self.drawer_mode == "queue"
                         else None)
        self.btn_queue.set_on(opening)
        start, to = self._drawer_x, 0 if opening else int(DRAWER_W * self.k)
        self.drawer.lift()
        if opening:
            self._set_drawer_mode(self.drawer_mode)
            if self.drawer_mode == "lyrics":
                self.lyrics_view.text.focus_set()
            else:
                self.drawer_list.focus_set()

        def step(p: float) -> None:
            self._drawer_x = int(start + (to - start) * p)
            self.drawer.place_configure(x=self._drawer_x)

        self.anim.tween("drawer", 260 if opening else 190, step)

    def _set_drawer_mode(self, mode: str) -> None:
        """Alterna el panel entre la cola y la letra, sin cerrarlo."""
        self.drawer_mode = "lyrics" if mode == "lyrics" else "queue"
        self.queue_lb = self.drawer_list if self.drawer_mode == "queue" else None
        if self.drawer_mode == "lyrics":
            self.drawer_list.pack_forget()
            self.drawer_hint.pack_forget()
            self.lyrics_view.pack(fill="both", expand=True, padx=int(4 * self.k),
                                  pady=(0, int(4 * self.k)))
            self.drawer_title.configure(text="Letra")
            self.drawer_sub.configure(
                text="Sincronizada con la canción" if self.lyrics_enabled
                else "Actívala en Ajustes › Reproducción")
            self._load_lyrics(self.cur_track)
            self._sync_lyrics(force=True)
        else:
            self.lyrics_view.pack_forget()
            self.drawer_hint.pack(fill="x", padx=int(16 * self.k), pady=(0, int(8 * self.k)))
            self.drawer_list.pack(fill="both", expand=True, padx=int(4 * self.k))
            self.drawer_title.configure(text="A continuación")
            self._refresh_queue()
        self._style_drawer_tabs(self.scheme.get("accent", self.pal["accent"]))

    def _refresh_queue(self) -> None:
        if self.now_open:
            self._now_queue()
        lb = self.queue_lb
        if self.queue_win is None or lb is None:
            return
        p = self.pal
        items: list[dict] = []
        self.queue_map = []

        def header(text: str) -> None:
            items.append(dict(text=text, hdr=True))
            self.queue_map.append(("hdr", 0))

        def add(t: dict, kind: str, idx: int, **extra) -> None:
            item = dict(text=f"{t['title']} — {t['artist']}", **extra)
            why = self.queue_why.get(t["video_id"]) if kind == "ctx" and self.pq.auto_ctx else None
            if why:
                item["sub"] = why
            if kind in ("up", "ctx"):  # solo lo que queda por delante se puede arrastrar
                item["move"] = kind
            items.append(item)
            self.queue_map.append((kind, idx))

        cur = self.pq.current(self.playing_vid)
        if cur:
            header("Sonando ahora")
            add(cur, "cur", 0, color=self.scheme["accent"])
        # lo que pusiste tú va aparte de lo que llega solo (similares o respaldo)
        if self.pq.up_next:
            header(f"Añadidas por ti · {len(self.pq.up_next)}")
            for i, t in enumerate(self.pq.up_next):
                add(t, "up", i, right="＋")
        upcoming = list(range(self.pq.opos + 1, len(self.pq.order)))
        if upcoming:
            header("Siguen automáticamente" if self.pq.auto_ctx else "Siguen en la lista")
            for j in upcoming:
                t = self.pq.ctx[self.pq.order[j]]
                remote = self._is_remote(t)
                add(t, "ctx", j, right="☁" if remote else "",
                    color=p["faint"] if remote and not self.online else None)
        lb.sel.clear()
        lb.set_items(items)
        kind = ((self.station_now or {}).get("title") or "Cola automática") if self.pq.auto_ctx             else "De la lista"  # con una estación de Sound, su nombre («Radio de Shakira»)
        queued = list(self.pq.up_next) + [self.pq.ctx[self.pq.order[j]] for j in upcoming]
        total = sum((t.get("duration") or 0) for t in queued)
        if queued:
            if total >= 3600:
                length = f"{int(total) // 3600} h {int(total) % 3600 // 60} min"
            else:
                length = fmt(total)
            summary = f"{len(queued)} canciones · {length}"
        else:
            summary = "nada en cola"
        self.drawer_sub.configure(
            text=f"{kind} · {'en línea' if self.online else 'sin conexión'} · {summary}")

    def _queue_jump(self, _e=None) -> None:
        sel = self.queue_lb.curselection() if self.queue_lb else ()
        if sel:
            self._queue_jump_at(sel[0])

    def _queue_jump_at(self, item: int) -> None:
        if 0 <= item < len(self.queue_map):
            self._play_queue_entry(*self.queue_map[item])

    def _play_queue_entry(self, kind: str, idx: int) -> None:
        """Salta a una canción de la cola: una añadida por ti o una de la lista que sigue."""
        if kind == "up":
            track = self.pq.take_up_next(idx)
            if track is not None:
                self._play_track(track)
        elif kind == "ctx":
            self.pq.opos = idx
            self._play_track(self.pq.ctx[self.pq.order[idx]])


    # ---------- edición de la cola ----------
    def _queue_move(self, src: int, dst: int) -> None:
        """Mueve una fila dentro de su sección; `dst` es la posición de inserción."""
        if not 0 <= src < len(self.queue_map):
            return
        kind, idx = self.queue_map[src]
        if kind == "up":
            if dst in (idx, idx + 1):
                return  # se suelta donde estaba
            track = self.pq.take_up_next(idx)
            if track is None:
                return
            self.pq.insert_up_next(dst - 1 if dst > idx else dst, track)
        elif kind == "ctx":
            positions = [v for k, v in self.queue_map if k == "ctx"]
            target = positions[dst] if dst < len(positions) else len(self.pq.order)
            if target in (idx, idx + 1):
                return
            track = self.pq.take_upcoming(idx)
            if track is None:
                return
            self.pq.insert_upcoming(target - 1 if target > idx else target, track)
        else:
            return
        self._refresh_queue()
        self._prefetch_next()
        self.dl.download_ahead()

    def _queue_remove(self, item: int | None = None) -> str | None:
        """Quita de la cola la fila elegida (o la seleccionada, con Supr)."""
        if item is None:
            sel = self.queue_lb.curselection() if self.queue_lb else ()
            if not sel:
                return None
            item = sel[0]
        if not 0 <= item < len(self.queue_map):
            return None
        kind, idx = self.queue_map[item]
        if kind == "up":
            track = self.pq.take_up_next(idx)
        elif kind == "ctx":
            track = self.pq.take_upcoming(idx)
        else:
            return None
        if track is None:
            return None

        def undo() -> None:
            if kind == "up":
                self.pq.insert_up_next(idx, track)
            else:
                self.pq.insert_upcoming(idx, track)
            self._refresh_queue()
            self._prefetch_next()

        self.toast.show(f"«{track['title']}» quitada de la cola", "Deshacer", on_action=undo)
        self._refresh_queue()
        self._prefetch_next()
        return "break"

    def _queue_context(self, item: int, x: int, y: int) -> None:
        if not 0 <= item < len(self.queue_map):
            return
        kind, _idx = self.queue_map[item]
        if kind not in ("up", "ctx"):
            return
        m = self._menu(self.drawer_list)
        m.add_command(label="Reproducir ahora",
                      command=lambda: self._queue_jump_at(item))
        m.add_command(label="Quitar de la cola",
                      command=lambda: self._queue_remove(item))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()
