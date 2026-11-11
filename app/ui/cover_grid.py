"""Vista de cuadrícula de la biblioteca: tarjetas con portada, título y artista.

Es un Canvas dibujado a mano, como `RowList`, y no una rejilla de widgets: con cientos de
canciones, crear un Frame y varios Canvas por tarjeta haría lenta cualquier ventana. Además,
solo se pintan las filas visibles (más una de margen) y las portadas se piden cuando hacen
falta, de modo que el coste no depende del tamaño de la biblioteca.

La selección se comporta como un `Listbox` extendido (clic, Ctrl+clic y Mayús+clic) y la
ventana principal sigue usando el `Treeview` oculto como fuente de verdad: este widget solo
refleja y modifica esa selección.

Una tarjeta puede representar varias filas de la tabla: las canciones de un mismo álbum se
juntan en una sola tarjeta (ver `group_albums`). Por eso la cuadrícula traduce siempre entre
sus tarjetas y las filas del `Treeview` (`members`).
"""
import io
import math
import tkinter as tk
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageOps, ImageTk

from ..core.filters import fold
from .theme import blend, readable_on, seed_color
from .widgets import HEART, SS, _rounded_mask, bind_touchpad, elide, touchpad_deltas

THUMB_CACHE = 120  # miniaturas en memoria
SOURCE_CACHE = 48  # imágenes originales (recortadas a 512 px) para rehacer miniaturas
CHIP = "#101014"    # fondo de los indicadores sobre la portada: opaco y legible en ambos temas
HEART_OFF = "#d9d9df"  # corazón vacío al pasar el ratón


def album_key(t: dict) -> tuple[str, str] | None:
    """Identidad del álbum de una canción descargada: (álbum, artista), sin tildes ni mayúsculas.

    Las canciones que no están en disco (resultados de YouTube) no se agrupan nunca.
    """
    if "path" not in t:
        return None
    album = fold(t.get("album") or "")
    if not album:
        return None
    return album, fold(t.get("artist") or "")


def group_albums(rows: list[dict], min_size: int = 2) -> tuple[list[dict], list[tuple[int, ...]]]:
    """Junta en una tarjeta las canciones de un mismo álbum.

    Devuelve (tarjetas, filas): cada tarjeta es una canción (el mismo dict de `rows`) o un
    álbum (dict con la clave "_album"), y `filas[i]` son los índices de `rows` que representa
    la tarjeta i. El álbum ocupa el sitio de su primera canción; un álbum del que solo hay una
    canción sigue mostrándose como canción. Es una función pura para poder probarla sin Tk.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    keys = [album_key(t) for t in rows]
    for i, key in enumerate(keys):
        if key is not None:
            groups.setdefault(key, []).append(i)
    cards: list[dict] = []
    members: list[tuple[int, ...]] = []
    done: set[tuple[str, str]] = set()
    for i, (t, key) in enumerate(zip(rows, keys)):
        group = groups.get(key) if key is not None else None
        if group is None or len(group) < min_size:
            cards.append(t)
            members.append((i,))
            continue
        if key in done:
            continue
        done.add(key)
        tracks = [rows[j] for j in group]
        first = tracks[0]
        cards.append({"_album": key, "_tracks": tracks, "video_id": first["video_id"],
                      "path": first.get("path"), "title": first.get("album") or "",
                      "artist": first.get("artist") or ""})
        members.append(tuple(group))
    return cards, members


def grid_metrics(width: int, k: float) -> tuple[int, int, int, int, int, int, int, int]:
    """Columnas y medidas de una cuadrícula para un ancho dado.

    Devuelve (columnas, ancho de celda, lado de la portada, alto de tarjeta, alto de fila,
    margen, hueco, x del primer elemento). Es una función pura para poder probarla sin Tk.
    """
    width = max(1, int(width))
    pad, gap = int(16 * k), int(16 * k)
    inner = int(5 * k)
    target = int(150 * k) + 2 * inner
    cols = max(1, int((width - 2 * pad + gap) // (target + gap)))
    cell = max(int(92 * k), (width - 2 * pad - (cols - 1) * gap) // cols)
    cover = max(1, cell - 2 * inner)
    card_h = cover + int(40 * k) + 2 * inner
    row_h = card_h + gap
    used = cols * cell + (cols - 1) * gap
    x0 = pad + max(0, (width - 2 * pad - used) // 2)
    return cols, cell, cover, card_h, row_h, pad, gap, x0


class CoverGrid(tk.Canvas):
    def __init__(self, master, anim, k: float, fonts: dict, on_select=None,
                 on_activate=None, on_context=None, on_need_cover=None, on_like=None) -> None:
        super().__init__(master, highlightthickness=0, bd=0, takefocus=1)
        self.anim, self.k, self.fonts = anim, k, fonts
        self.on_select, self.on_activate = on_select, on_activate
        self.on_context, self.on_need_cover = on_context, on_need_cover
        self.on_like = on_like
        self.items: list[dict] = []
        self.members: list[tuple[int, ...]] = []  # filas del Treeview de cada tarjeta
        self._row_item: dict[int, int] = {}       # fila del Treeview → tarjeta
        self._card_vids: list[frozenset[str]] = []
        self._hearts: dict[int, tuple[int, int, int, int]] = {}  # zona clicable del corazón
        self.sel: set[int] = set()
        self.anchor: int | None = None
        self.hovered: int | None = None
        self.off = 0.0
        self.total = 0
        self._goal = 0.0
        self._width = 0
        self._range: tuple[int, int] | None = None
        self._cards: dict[int, list[int]] = {}
        self._eq: list[tuple[int, int, int, int, int, float]] = []
        self._vid_index: dict[str, int] = {}
        self._pill_cache: dict[tuple, ImageTk.PhotoImage] = {}
        self._photos: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()
        self._sources: OrderedDict[str, Image.Image] = OrderedDict()
        self._rings: dict[tuple, ImageTk.PhotoImage] = {}
        self._chips: dict[tuple, ImageTk.PhotoImage] = {}
        self._failed: set[str] = set()
        self._asked: set[str] = set()
        # referencias vivas (no copias): descargas, dañados y favoritos cambian por su cuenta
        self._liked: set[str] = set()
        self._damaged: set[str] = set()
        self._states: dict[str, str] = {}
        self._view_kind = "all"
        self._playing: str | None = None
        self._geo = (1, 1, 1, 1, 1, 1, 0, 0)  # cols, cell, cover, card_h, row_h, pad, gap, x0
        self.c = dict(bg="#000000", fg="#ffffff", dim="#888888", faint="#555555",
                      sel="#333333", hover="#222222", accent="#ffffff")
        self.bind("<Configure>", self._resized)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Button-3>", self._context)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<MouseWheel>", self._wheel)
        bind_touchpad(self, self._touchpad)
        self.bind("<Left>", lambda _e: self._move_by(-1))
        self.bind("<Right>", lambda _e: self._move_by(1))
        self.bind("<Up>", lambda _e: self._move_by(-self._geo[0]))
        self.bind("<Down>", lambda _e: self._move_by(self._geo[0]))
        self.bind("<Home>", lambda _e: self._move_to(0))
        self.bind("<End>", lambda _e: self._move_to(max(0, len(self.items) - 1)))
        self.bind("<Return>", lambda _e: self._activate())
        anim.add_loop(self._eq_frame)

    # ---------- datos ----------
    def set_context(self, *, liked=None, damaged=None, states=None, view_kind=None) -> None:
        """Guarda las referencias que deciden corazones, estados y el tipo de vista."""
        if liked is not None:
            self._liked = liked
        if damaged is not None:
            self._damaged = damaged
        if states is not None:
            self._states = states
        if view_kind is not None:
            self._view_kind = view_kind

    def set_items(self, items: list[dict], members: list[tuple[int, ...]] | None = None) -> None:
        """`members[i]`: filas del Treeview que representa la tarjeta i (por defecto, la i)."""
        self.items = list(items)
        self.members = list(members) if members is not None else [(i,) for i in range(len(items))]
        self._row_item = {row: i for i, rows in enumerate(self.members) for row in rows}
        self._card_vids = [frozenset(x.get("video_id") for x in self.tracks_of(i)
                                     if x.get("video_id")) for i in range(len(self.items))]
        self._vid_index = {vid: i for i, vids in enumerate(self._card_vids) for vid in vids}
        self.sel.clear()
        self.anchor = self.hovered = None
        self._layout()
        self._rehover()

    def _rehover(self) -> None:
        """Tras repintar todo, la tarjeta bajo el puntero sigue resaltada (y con su corazón)."""
        try:
            if not self.winfo_ismapped():
                return
            px = self.winfo_pointerx() - self.winfo_rootx()
            py = self.winfo_pointery() - self.winfo_rooty()
        except tk.TclError:
            return
        if 0 <= px < self.winfo_width() and 0 <= py < self.winfo_height():
            self._set_hover(self._hit(self.canvasx(px), self.canvasy(py)))

    def tracks_of(self, i: int) -> list[dict]:
        """Las canciones de la tarjeta i: una, o todas las del álbum."""
        card = self.items[i]
        return list(card["_tracks"]) if "_album" in card else [card]

    @staticmethod
    def is_album(card: dict) -> bool:
        return "_album" in card

    def set_playing(self, video_id: str | None) -> None:
        if video_id == self._playing:
            return
        self._playing = video_id
        if self._cards:
            self._render_visible(force=True)
        self.anim.wake()

    def set_state(self, video_id: str, _text: str) -> None:
        """Un estado de descarga cambió: basta con repintar su tarjeta, si está a la vista."""
        i = self._vid_index.get(video_id)
        if i is not None and self._in_range(i):
            self._paint_one(i)

    def forget_cover(self, video_id: str) -> None:
        """La canción acaba de descargarse: su portada puede haber pasado a estar incrustada."""
        self._failed.discard(video_id)
        self._asked.discard(video_id)
        self._sources.pop(video_id, None)
        for key in [key for key in self._photos if key[0] == video_id]:
            self._photos.pop(key, None)
        i = self._vid_index.get(video_id)
        if i is not None and self._in_range(i):
            self._paint_one(i)

    def set_cover(self, video_id: str, data) -> None:
        """Llegó (o falló) la portada pedida a un hilo de la ventana.

        `data` es una imagen de Pillow ya recortada (o None); también se aceptan bytes por si
        otra vía la entrega sin decodificar.
        """
        self._asked.discard(video_id)
        image = None
        if isinstance(data, Image.Image):
            image = data.convert("RGB")
        elif data:
            try:
                image = Image.open(io.BytesIO(data)).convert("RGB")
                image.thumbnail((512, 512), Image.LANCZOS)
            except Exception:  # noqa: BLE001 - una imagen corrupta usa el degradado
                image = None
        if image is None:
            self._failed.add(video_id)
        else:
            self._sources[video_id] = image
            self._trim(self._sources, SOURCE_CACHE)
            for key in [key for key in self._photos if key[0] == video_id]:
                self._photos.pop(key, None)
            self._failed.discard(video_id)
        i = self._vid_index.get(video_id)
        if i is not None and self._in_range(i):
            self._paint_one(i)
        if self._range is not None:  # da otra oportunidad a las tarjetas que esperaban turno
            for j in range(*self._range):
                self._want_cover(self._cover_track(j))

    # ---------- apariencia ----------
    def restyle(self, **colors) -> None:
        self.c.update(colors)
        self.configure(bg=self.c["bg"])
        self._pill_cache.clear()
        self._rings.clear()
        self._chips.clear()
        if self.items:
            self._render_visible(force=True)

    def set_accent(self, accent: str) -> None:
        if accent == self.c["accent"]:
            return
        self.c["accent"] = accent
        self._rings.clear()
        self._chips.clear()
        if self.items:
            self._render_visible(force=True)

    # ---------- geometría ----------
    def _metrics(self) -> tuple[int, int, int, int, int, int, int, int]:
        return grid_metrics(self._width or self.winfo_width(), self.k)

    def _layout(self) -> None:
        self.delete("all")
        self._cards.clear()
        self._eq.clear()
        self._range = None
        self._geo = self._metrics()
        cols, cell, _cover, card_h, row_h, pad, _gap, _x0 = self._geo
        if not self.items:
            self.total = 1
            self.configure(scrollregion=(0, 0, max(1, self._width), 1))
            self.off = self._goal = 0.0
            return
        rows = math.ceil(len(self.items) / cols)
        self.total = pad * 2 + rows * card_h + (rows - 1) * (row_h - card_h)
        self.configure(scrollregion=(0, 0, max(1, self._width), self.total))
        limit = max(0, self.total - self.winfo_height())
        self.off = min(max(0.0, self.off), float(limit))
        self._goal = self.off
        self.yview_moveto(self.off / max(1, self.total))
        self._render_visible(force=True)

    def _resized(self, e) -> None:
        if e.width != self._width:
            self._width = e.width
            self._layout()
        elif self.items:
            self._render_visible(force=True)

    def _cell(self, i: int) -> tuple[int, int]:
        cols, cell, _cover, card_h, row_h, pad, gap, x0 = self._geo
        return x0 + (i % cols) * (cell + gap), pad + (i // cols) * row_h

    def _in_range(self, i: int) -> bool:
        return self._range is not None and self._range[0] <= i < self._range[1]

    # ---------- dibujo ----------
    def _render_visible(self, force: bool = False) -> None:
        if not self.items:
            self.delete("all")
            self._cards.clear()
            self._eq.clear()
            self._range = None
            return
        cols, _cell, _cover, _card_h, row_h, pad, _gap, _x0 = self._geo
        top, height = self.off, max(1, self.winfo_height())
        rows = math.ceil(len(self.items) / cols)
        first_row = max(0, int((top - pad) // row_h))
        last_row = min(rows - 1, int((top + height - pad) // row_h) + 1)
        first = first_row * cols
        last = min(len(self.items), (last_row + 1) * cols)
        if (first, last) == self._range and not force:
            return
        self._range = (first, last)
        for i in list(self._cards):
            if not first <= i < last:
                self._forget(i)
        for i in range(first, last):
            if force or i not in self._cards:
                self._paint_one(i)

    def _forget(self, i: int) -> None:
        self._hearts.pop(i, None)
        removed = set(self._cards.pop(i, ()))
        for item in removed:
            self.delete(item)
        if removed:
            self._eq = [x for x in self._eq if x[0] not in removed]

    def _paint_one(self, i: int) -> None:
        self._forget(i)
        if not self._in_range(i):
            return
        t = self.items[i]
        c, k = self.c, self.k
        cols, cell, cover, card_h, _row_h, _pad, _gap, _x0 = self._geo
        x, y = self._cell(i)
        inner = (cell - cover) // 2
        cx, cy = x + inner, y + inner
        items: list[int] = []

        selected = i in self.sel
        hovered = i == self.hovered
        album = self.is_album(t)
        if selected or hovered:
            color = c["sel"] if selected else c["hover"]
            items.append(self.create_image(x, y, anchor="nw",
                                           image=self._pill(cell, card_h, color)))
            self.tag_lower(items[-1])

        if album:  # dos portadas asoman por detrás: es un álbum, no una canción
            for step, mix in ((2, 0.20), (1, 0.36)):
                inset, rise = int(9 * k) * step, int(4 * k) * step
                items.append(self.create_image(
                    cx + inset, cy - rise, anchor="nw",
                    image=self._pill(cover - 2 * inset, int(24 * k), blend(c["bg"], c["fg"], mix))))

        items.append(self.create_image(cx, cy, anchor="nw", image=self._photo(t, cover)))
        self._want_cover(self._cover_track(i))

        vids = self._card_vids[i] if i < len(self._card_vids) else frozenset()
        playing = bool(self._playing and self._playing in vids)
        if playing:
            ring = self._ring(cover, max(2, int(9 * k)), max(2, int(2 * k)))
            items.append(self.create_image(cx, cy, anchor="nw", image=ring))
            items.extend(self._eq_bars(cx, cy, cover))
            self.anim.wake()

        # corazón: siempre si está en «Me gusta»; vacío al pasar el ratón, para marcarla
        liked = bool(vids) and vids <= self._liked
        if (liked or hovered) and not self._is_remote(t) and self.on_like is not None:
            badge, rect = self._badge("♥" if liked else "♡", cx + cover - int(5 * k),
                                      cy + int(5 * k), anchor="ne",
                                      color=HEART if liked else HEART_OFF, wide=True)
            items.extend(badge)
            self._hearts[i] = rect
        elif liked:
            items.extend(self._badge("♥", cx + cover - int(5 * k), cy + int(5 * k),
                                     anchor="ne", color=HEART)[0])
        state = self._state_of(t) or (f"{len(vids)} canciones" if album else "")
        if state:
            items.extend(self._badge(state, cx + cover - int(5 * k),
                                     cy + cover - int(5 * k), anchor="se")[0])

        title_y = cy + cover + int(5 * k)
        tw = cover
        title_color = c["accent"] if playing else (
            c["fg"] if not self._is_remote(t) or selected or hovered else c["dim"])
        items.append(self.create_text(cx, title_y, anchor="nw", text=elide(
            self.fonts["body"], t.get("title") or "", tw), font=self.fonts["body"],
            fill=title_color))
        items.append(self.create_text(cx, title_y + int(19 * k), anchor="nw", text=elide(
            self.fonts["small"], t.get("artist") or "", tw), font=self.fonts["small"],
            fill=c["faint"] if self._is_remote(t) and not selected and not hovered else c["dim"]))
        self._cards[i] = items

    def _cover_track(self, i: int) -> dict:
        """La canción cuya portada representa la tarjeta (la primera del álbum)."""
        card = self.items[i]
        return card["_tracks"][0] if self.is_album(card) else card

    def _pill(self, w: int, h: int, color: str) -> ImageTk.PhotoImage:
        key = (w, h, color)
        if key not in self._pill_cache:
            mask = _rounded_mask(w, h, max(2, int(9 * self.k)))
            img = Image.new("RGBA", (w, h), color)
            img.putalpha(mask)
            self._pill_cache[key] = ImageTk.PhotoImage(img, master=self)
        return self._pill_cache[key]

    def _ring(self, size: int, radius: int, width: int) -> ImageTk.PhotoImage:
        key = (size, radius, width, self.c["accent"])
        if key not in self._rings:
            mask = Image.new("L", (size * SS, size * SS), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, size * SS - 1, size * SS - 1), radius=radius * SS,
                outline=255, width=width * SS)
            mask = mask.resize((size, size), Image.LANCZOS)
            img = Image.new("RGBA", (size, size), self.c["accent"])
            img.putalpha(mask)
            self._rings[key] = ImageTk.PhotoImage(img, master=self)
        return self._rings[key]

    def _chip(self, w: int, h: int) -> ImageTk.PhotoImage:
        key = (w, h)
        if key not in self._chips:
            mask = _rounded_mask(w, h, max(2, int(6 * self.k)))
            img = Image.new("RGBA", (w, h), CHIP)
            img.putalpha(mask)
            self._chips[key] = ImageTk.PhotoImage(img, master=self)
        return self._chips[key]

    def _badge(self, text: str, x: int, y: int, anchor: str, color: str | None = None,
               wide: bool = False):
        """Texto pequeño sobre una pastilla oscura, legible encima de cualquier portada.

        Devuelve (elementos del Canvas, rectángulo que ocupa). `wide` agranda la pastilla
        para que sea fácil de pulsar.
        """
        font = self.fonts["body" if wide else "small"]
        pad = int(6 * self.k)
        side = int((26 if wide else 18) * self.k)
        w = max(side, font.measure(text) + pad * 2)
        h = side
        left = x - w if anchor in ("ne", "se") else x
        top = y - h if anchor in ("se", "sw") else y
        items = [self.create_image(left, top, anchor="nw", image=self._chip(w, h)),
                 self.create_text(left + w // 2, top + h // 2, text=text, font=font,
                                  fill=readable_on(color or self.c["fg"], CHIP))]
        return items, (left, top, left + w, top + h)

    def _eq_bars(self, cx: int, cy: int, cover: int) -> list[int]:
        k = self.k
        chip_w, chip_h = int(28 * k), int(20 * k)
        x = cx + int(6 * k)
        y = cy + cover - chip_h - int(6 * k)
        out = [self.create_image(x, y, anchor="nw", image=self._chip(chip_w, chip_h))]
        bw = max(2, int(2.4 * k))
        gap = max(1, int(1.8 * k))
        total = bw * 3 + gap * 2
        base = y + chip_h - int(4 * k)
        hmax = chip_h - int(8 * k)
        for j in range(3):
            bx = x + (chip_w - total) // 2 + j * (bw + gap)
            h = max(2, int(hmax * 0.5))
            item = self.create_rectangle(bx, base - h, bx + bw, base,
                                         fill=readable_on(self.c["accent"], CHIP), outline="")
            out.append(item)
            self._eq.append((item, bx, base, bw, hmax, j * 2.1))
        return out

    def _photo(self, track: dict, size: int) -> ImageTk.PhotoImage:
        vid = track.get("video_id") or ""
        key = (vid, size)
        got = self._photos.get(key)
        if got is not None:
            self._photos.move_to_end(key)
            return got
        source = self._sources.get(vid)
        if source is not None:
            self._sources.move_to_end(vid)
            img = ImageOps.fit(source, (size, size), Image.LANCZOS).convert("RGBA")
        else:
            img = self._placeholder(vid, size)
        img.putalpha(_rounded_mask(size, size, max(2, int(9 * self.k))))
        photo = ImageTk.PhotoImage(img, master=self)
        self._photos[key] = photo
        self._trim(self._photos, THUMB_CACHE)
        return photo

    def _placeholder(self, seed: str, size: int) -> Image.Image:
        """Sin portada: el mismo degradado propio de cada canción que usa el reproductor."""
        base = seed_color(seed)
        ramp = Image.linear_gradient("L").rotate(35).crop((48, 48, 208, 208)).resize((size, size))
        return ImageOps.colorize(ramp, blend(base, "#000000", 0.35),
                                 blend(base, "#ffffff", 0.18)).convert("RGBA")

    def _want_cover(self, track: dict) -> None:
        vid = track.get("video_id")
        if (not vid or vid in self._sources or vid in self._failed or vid in self._asked
                or self.on_need_cover is None):
            return
        self._asked.add(vid)
        try:
            accepted = self.on_need_cover(dict(track))
        except Exception:  # noqa: BLE001 - pedir una portada nunca debe romper el dibujo
            accepted = False
        if accepted is False:  # demasiadas en curso: se reintenta al repintar
            self._asked.discard(vid)

    @staticmethod
    def _trim(cache: OrderedDict, limit: int) -> None:
        while len(cache) > limit:
            cache.popitem(last=False)

    # ---------- estado de cada fila ----------
    def _is_remote(self, t: dict) -> bool:
        return "path" not in t

    def _state_of(self, t: dict) -> str:
        if self.is_album(t):
            return "✗" if any(x.get("video_id") in self._damaged for x in t["_tracks"]) else ""
        vid = t.get("video_id") or ""
        if vid in self._damaged:
            return "✗"
        if self._view_kind in ("recent", "top") and t.get("_plays"):
            return f"×{t['_plays']}"
        return self._states.get(vid) or ("" if not self._is_remote(t) else "☁")

    # ---------- interacción ----------
    def selection(self) -> tuple[str, ...]:
        """Filas del Treeview seleccionadas: un álbum aporta todas sus canciones."""
        rows = sorted({row for i in self.sel if i < len(self.members) for row in self.members[i]})
        return tuple(str(row) for row in rows)

    def select(self, iids) -> None:
        """Selección programática (filas del Treeview, que manda); no dispara `on_select`."""
        new = set()
        for value in iids:
            try:
                i = self._row_item.get(int(value))
            except (TypeError, ValueError):
                continue
            if i is not None:
                new.add(i)
        if new == self.sel:
            return
        old, self.sel = self.sel, new
        for i in old | new:
            if self._in_range(i):
                self._paint_one(i)
        if self.hovered in new and self.hovered is not None:
            self._paint_one(self.hovered)

    def see_row(self, row: int) -> None:
        """Desplaza hasta la tarjeta que contiene la fila `row` del Treeview."""
        i = self._row_item.get(row)
        if i is not None:
            self.see(i)

    def see(self, i: int) -> None:
        if not 0 <= i < len(self.items):
            return
        _x, y = self._cell(i)
        card_h = self._geo[3]
        view = self.winfo_height()
        if y < self.off:
            self._scroll_to(y - self._geo[5])
        elif y + card_h > self.off + view:
            self._scroll_to(y + card_h - view + self._geo[5])

    def _hit(self, x: float, y: float) -> int | None:
        if not self.items or self._geo[0] <= 0:
            return None
        cols, cell, _cover, card_h, row_h, pad, gap, x0 = self._geo
        if x < x0 or y < pad:
            return None
        col = int((x - x0) // (cell + gap))
        row = int((y - pad) // row_h)
        if not 0 <= col < cols or row < 0:
            return None
        if x - x0 - col * (cell + gap) > cell or y - pad - row * row_h > card_h:
            return None
        i = row * cols + col
        return i if 0 <= i < len(self.items) else None

    def _motion(self, e) -> None:
        i = self._hit(self.canvasx(e.x), self.canvasy(e.y))
        self.configure(cursor="hand2" if i is not None else "arrow")
        self._set_hover(i)

    def _set_hover(self, i: int | None) -> None:
        if i == self.hovered:
            return
        old, self.hovered = self.hovered, i
        for j in (old, i):
            if j is not None and self._in_range(j):
                self._paint_one(j)

    def _on_heart(self, i: int, x: float, y: float) -> bool:
        rect = self._hearts.get(i)
        return rect is not None and rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]

    def _press(self, e) -> None:
        self.focus_set()
        x, y = self.canvasx(e.x), self.canvasy(e.y)
        i = self._hit(x, y)
        if i is None:
            return
        if self.on_like is not None and self._on_heart(i, x, y):
            self.on_like(i)  # marcar no cambia la selección
            return
        ctrl = bool(e.state & 0x4)
        shift = bool(e.state & 0x1)
        prev = set(self.sel)
        if ctrl:
            self.sel.symmetric_difference_update({i})
            self.anchor = i
        elif shift and self.anchor is not None:
            lo, hi = sorted((self.anchor, i))
            self.sel = set(range(lo, hi + 1))
        else:
            self.sel, self.anchor = {i}, i
        self._selection_changed(prev)

    def _selection_changed(self, prev: set[int]) -> None:
        for i in prev | self.sel:
            if self._in_range(i):
                self._paint_one(i)
        if self.on_select is not None and prev != self.sel:
            self.on_select(self.selection())

    def _double(self, e) -> None:
        x, y = self.canvasx(e.x), self.canvasy(e.y)
        i = self._hit(x, y)
        if i is not None and self._on_heart(i, x, y):
            return  # doble clic en el corazón: marcar y desmarcar, no reproducir
        if i is not None and self.on_activate is not None:
            self.on_activate(i)

    def _activate(self) -> None:
        if self.sel and self.on_activate is not None:
            self.on_activate(next(iter(sorted(self.sel))))

    def _context(self, e) -> str:
        i = self._hit(self.canvasx(e.x), self.canvasy(e.y))
        if i is None:
            return "break"
        if i not in self.sel:
            prev = set(self.sel)
            self.sel, self.anchor = {i}, i
            self._selection_changed(prev)
        if self.on_context is not None:
            self.on_context(i, e.x_root, e.y_root)
        return "break"

    # ---------- teclado y desplazamiento ----------
    def _move_by(self, delta: int) -> str:
        if not self.items:
            return "break"
        if not self.sel:
            j = 0 if delta > 0 else len(self.items) - 1
        else:
            j = (max(self.sel) if delta > 0 else min(self.sel)) + delta
        self._move_to(max(0, min(len(self.items) - 1, j)))
        return "break"

    def _move_to(self, i: int) -> None:
        if not 0 <= i < len(self.items):
            return
        prev = set(self.sel)
        self.sel, self.anchor = {i}, i
        self._selection_changed(prev)
        self.see(i)

    def _wheel(self, e) -> str:
        self._scroll_to(self._goal - e.delta / 120 * 3 * self._geo[4])
        return "break"

    def _touchpad(self, e) -> str:
        """Gesto de dos dedos: en Tk 9 llega aparte y en píxeles (no en «clics» de rueda)."""
        _dx, dy = touchpad_deltas(e)
        if dy:
            self._apply_scroll(self.off - dy)
            self._goal = self.off
        return "break"

    def _scroll_to(self, y: float) -> None:
        limit = max(0, self.total - self.winfo_height())
        goal = self._goal = max(0.0, min(float(limit), y))
        start = self.off
        if abs(goal - start) < 0.5:
            self._apply_scroll(goal)
            return
        self.anim.tween(("gridscroll", id(self)), 160,
                        lambda p: self._apply_scroll(start + (goal - start) * p))

    def _apply_scroll(self, y: float) -> None:
        limit = max(0, self.total - self.winfo_height())
        self.off = max(0.0, min(float(limit), y))
        self.yview_moveto(self.off / max(1, self.total))
        self._render_visible()

    def scrollbar(self, action: str, *args) -> None:
        """Interfaz de la barra de desplazamiento, como la de un Canvas."""
        try:
            if action == "moveto":
                goal = float(args[0]) * self.total
            elif action == "scroll":
                unit = self.winfo_height() if len(args) > 1 and args[1] == "pages" else self._geo[4]
                goal = self._goal + float(args[0]) * unit
            else:
                return
        except (IndexError, ValueError):
            return
        limit = max(0, self.total - self.winfo_height())
        self._goal = max(0.0, min(float(limit), goal))
        self._apply_scroll(self._goal)

    # ---------- ecualizador de la tarjeta que suena ----------
    def _eq_frame(self, now: float) -> bool:
        if not self._eq or not self.winfo_ismapped() or self.anim.reduced:
            return False
        for item, x, base, bw, hmax, phase in self._eq:
            level = 0.35 + 0.55 * (0.5 + 0.5 * math.sin(now * 6.3 + phase))
            h = max(2.0, level * hmax)
            self.coords(item, x, base - h, x + bw, base)
        return True
