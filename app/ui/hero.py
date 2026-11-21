"""Cabecera de portada de álbumes, artistas y listas: qué lleva cada página y sus botones.

La cabecera (`widgets.Header`) dibuja; aquí se decide qué enseña cada página (el tipo, de quién
es, la portada o un mosaico de cuatro), se cargan las portadas en segundo plano y se responde a
los botones: ▶ Reproducir, Aleatorio, Me gusta y «⋯» (cola, listas, descargar lo que falta…).
"""
from __future__ import annotations

import io
import random
import tkinter as tk

from PIL import Image, ImageDraw, ImageOps

from ..core import covers
from ..core import log as _log
from ..core.filters import fold
from .theme import blend, seed_color
from .widgets import IconButton, PillButton

log = _log.get("ui")
SS = 4  # suavizado de los bordes de la máscara


def _mask(size: int, circle: bool, radius: int) -> Image.Image:
    big = Image.new("L", (size * SS, size * SS), 0)
    draw = ImageDraw.Draw(big)
    box = (0, 0, size * SS - 1, size * SS - 1)
    if circle:
        draw.ellipse(box, fill=255)
    else:
        draw.rounded_rectangle(box, radius * SS, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def compose_art(images: list, size: int, seed: str, circle: bool = False) -> Image.Image:
    """La portada de la cabecera: una sola, un mosaico de cuatro (listas con cuatro o más
    portadas distintas) o, sin ninguna, un degradado propio. RGBA con bordes redondeados (o en
    círculo, para un artista)."""
    found = [img for img in images if img is not None]
    if len(found) >= 4 and not circle:
        half = (size + 1) // 2
        out = Image.new("RGB", (half * 2, half * 2))
        for n, img in enumerate(found[:4]):
            tile = ImageOps.fit(img.convert("RGB"), (half, half), Image.LANCZOS)
            out.paste(tile, ((n % 2) * half, (n // 2) * half))
        out = out.resize((size, size), Image.LANCZOS) if half * 2 != size else out
    elif found:
        out = ImageOps.fit(found[0].convert("RGB"), (size, size), Image.LANCZOS)
    else:
        base = seed_color(seed or "sound")
        ramp = Image.linear_gradient("L").rotate(35).crop((48, 48, 208, 208)).resize((size, size))
        out = ImageOps.colorize(ramp, blend(base, "#000000", 0.35), blend(base, "#ffffff", 0.18))
    out = out.convert("RGBA")
    out.putalpha(_mask(size, circle, max(4, size // 18)))
    return out


def art_rows(rows: list[dict], limit: int = 4) -> list[dict]:
    """Canciones con portadas distintas (una por álbum), para el mosaico."""
    seen, out = set(), []
    for t in rows:
        # también las que aún no están en disco: un álbum, una portada
        key = ((fold(t["album"]), fold(t.get("artist") or "")) if t.get("album")
               else ("vid", t.get("video_id")))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= limit:
            break
    return out


class HeroMixin:
    def _init_hero(self) -> None:
        self._hero_art: tuple | None = None  # (clave de las portadas, imagen ya compuesta)
        self._hero_token = 0

    def _build_hero_bar(self) -> None:
        k, f = self.k, self.fonts
        bar = self.hero_bar = tk.Frame(self.header)
        self.btn_hero_play = PillButton(bar, self.anim, k, "▶   Reproducir", self.hero_play,
                                        f["body"], variant="primary", min_width=150)
        self.btn_hero_shuffle = IconButton(bar, self.anim, k, "shuffle", self.hero_shuffle,
                                           tip="Reproducir en orden aleatorio")
        self.btn_hero_like = IconButton(bar, self.anim, k, "heart", self.hero_like,
                                        tip=lambda: "Quitar de Me gusta" if self._hero_all_liked()
                                        else "Me gusta (todas)")
        self.btn_hero_more = IconButton(bar, self.anim, k, "more", self.hero_more,
                                        tip="Más acciones")
        self.btn_hero_play.pack(side="left", padx=(0, int(10 * k)))
        for button in (self.btn_hero_shuffle, self.btn_hero_like, self.btn_hero_more):
            button.pack(side="left", padx=(0, int(2 * k)))
        self.header.set_bar(bar)
        self.header.on_bar_color = self._hero_bar_color

    def _hero_bar_color(self, color: str) -> None:
        p = self.pal
        accent = self.scheme.get("accent", p["accent"])
        self.hero_bar.configure(bg=color)
        self.btn_hero_play.restyle(bg=color, fg=p["fg"], dim=p["dim"], faint=p["faint"],
                                   accent=accent, on_accent=p["accent_fg"], danger=p["danger"])
        for button in (self.btn_hero_shuffle, self.btn_hero_like, self.btn_hero_more):
            button.restyle(color, p["fg"], p["dim"], accent)

    # ---------- qué lleva cada página ----------
    def _hero_info(self, rows: list[dict]) -> dict | None:
        if self.query.get().strip():
            return None
        top = self.sub_view
        if top is not None and top["kind"] == "album":
            year = next((t.get("year") for t in rows if t.get("year")), None)
            artist = (top.get("artist") or "").split(",")[0].strip()
            link = (lambda: self.open_artist(artist)) if self._has_artist(artist) else None
            return dict(key=("album", top["key"]), kind="ÁLBUM" + (f"  ·  {year}" if year else ""),
                        byline=(top.get("artist") or "", link) if artist else None,
                        rows=rows[:1], circle=False, label=f"Álbum «{top['title']}»")
        if top is not None:
            return dict(key=("artist", top["key"]), kind="ARTISTA", byline=None,
                        rows=rows[:1], circle=True, label=f"Artista «{top['title']}»")
        if self.view_kind == "playlist" and self.playlist_id is not None:
            name = self._playlist_name(self.playlist_id) or "Lista"
            return dict(key=("pl", self.playlist_id), kind="LISTA", byline=None,
                        rows=art_rows(rows), circle=False, label=f"Lista «{name}»")
        if self.view_kind == "liked":
            return dict(key=("liked",), kind="LISTA", byline=None, rows=art_rows(rows),
                        circle=False, label="Me gusta")
        return None

    def _update_hero(self, rows: list[dict]) -> None:
        """Pone la cabecera de portada de la página (o la quita) y pide sus portadas."""
        info = self._hero_info(rows)
        if info is None:
            self.header.set_hero(None)
            return
        art_key = (info["key"], info["circle"], tuple(t["video_id"] for t in info["rows"]))
        fresh = self.header.hero_key != info["key"]
        if self._hero_art is not None and self._hero_art[0] == art_key:
            info["art"] = self._hero_art[1] if fresh else None
        else:
            side = self.header.art_side
            # mientras llegan las portadas: el degradado propio de la página
            info["art"] = compose_art([], side, str(info["key"]), info["circle"])
            self._load_hero_art(art_key, info["rows"], info["circle"])
        self.header.set_hero(info)
        self._sync_hero_like()
        self.btn_hero_play.set_enabled(bool(rows))
        self.btn_hero_shuffle.set_enabled(len(rows) > 1)

    def _load_hero_art(self, art_key: tuple, rows: list[dict], circle: bool) -> None:
        self._hero_token += 1
        token, side = self._hero_token, self.header.art_side
        self._hero_art = (art_key, None)

        def work() -> None:
            images = []
            for t in rows:
                try:
                    data = covers.load(t)
                    images.append(Image.open(io.BytesIO(data)).convert("RGB") if data else None)
                except Exception as exc:  # noqa: BLE001 - sin esa portada, las demás
                    _log.failure(log, "no se pudo leer una portada para la cabecera", exc)
                    images.append(None)
            art = compose_art(images, side, str(art_key[0]), circle) if any(images) else None
            self.q.put(("hero_art", token, art_key, art))

        try:
            self.thumb_pool.submit(work)
        except RuntimeError:  # la ventana se está cerrando
            pass

    def _hero_art_loaded(self, token: int, art_key: tuple, art) -> None:
        if token != self._hero_token or art is None:
            return
        self._hero_art = (art_key, art)
        if self.header.hero_key == art_key[0]:
            self.header.set_art(art)

    # ---------- desplazar: la portada se recoge ----------
    def _hero_scrolled(self, first: float) -> None:
        if self.header.hero_key is None:
            return
        if first > 0.02:
            self.header.set_collapsed(True)
        elif first <= 0.0005:
            self.header.set_collapsed(False)

    # ---------- botones ----------
    def _hero_label(self) -> str | None:
        hero = self.header._hero
        return hero.get("label") if hero else None

    def _hero_all_liked(self) -> bool:
        return bool(self.rows) and all(t["video_id"] in self.liked for t in self.rows)

    def _sync_hero_like(self) -> None:
        liked = self._hero_all_liked()
        self.btn_hero_like.set_icon("heart_fill" if liked else "heart")
        self.btn_hero_like.set_on(liked)
        self.btn_hero_like.set_enabled(bool(self.rows))

    def hero_play(self) -> None:
        if self.rows:
            self.play_tracks(list(self.rows), 0, label=self._hero_label())

    def hero_shuffle(self) -> None:
        rows = list(self.rows)
        if not rows:
            return
        if not self.pq.shuffle:
            self.toggle_shuffle()
        self.play_tracks(rows, random.randrange(len(rows)), label=self._hero_label())

    def hero_like(self) -> None:
        if self.rows:
            self.toggle_like(list(self.rows))
            self._sync_hero_like()

    def hero_more(self) -> None:
        rows = list(self.rows)
        menu = self._menu()
        station = self._hero_station(rows)
        if station is not None:  # una radio sin fin de lo que se ve (estaciones de Sound)
            menu.add_command(label=f"Iniciar «{station['title']}»",
                             command=lambda: self.start_station(station))
            menu.add_separator()
        if rows:
            menu.add_command(label="Añadir a la cola", command=lambda: self.enqueue(rows))
            menu.add_cascade(label="Añadir a lista", menu=self._playlist_menu(menu, rows))
        missing = [t for t in rows if self._is_remote(t)]
        if missing:
            menu.add_command(label=f"Descargar lo que falta ({len(missing)})",
                             command=lambda: self.download_tracks(missing))
        top = self.sub_view
        if top is not None and top["kind"] == "album" and self._has_artist(top.get("artist")):
            menu.add_separator()
            menu.add_command(label=f"Ir a {top['artist']}",
                             command=lambda: self.open_artist(top["artist"]))
        if top is None and self.view_kind == "playlist" and self.playlist_id is not None:
            pid = self.playlist_id
            menu.add_separator()
            menu.add_command(label="Renombrar…", command=lambda: self.rename_playlist(pid))
            menu.add_command(label="Exportar a M3U…", command=lambda: self.export_playlist(pid))
            menu.add_command(label="Eliminar lista", command=lambda: self.delete_playlist(pid))
        if menu.index("end") is None:
            return
        button = self.btn_hero_more
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()
