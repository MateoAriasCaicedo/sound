"""Cola de reproducción: qué suena, qué viene después y en qué orden. Sin interfaz ni hilos.

Separada de la ventana para poder probarla: el orden, la repetición, el barajado y el salto de
canciones que solo existen en YT Music (sin internet) son lógica pura sobre listas.
"""
import random
from typing import Callable


class PlayQueue:
    def __init__(self, is_remote: Callable[[dict], bool], is_online: Callable[[], bool]) -> None:
        self._is_remote = is_remote
        self._is_online = is_online
        self.ctx: list[dict] = []     # la lista desde la que se reproduce
        self.order: list[int] = []    # índices de ctx en el orden en que sonarán
        self.opos = 0                 # posición actual dentro de `order`
        self.up_next: list[dict] = []  # cola manual: suena antes que el resto
        self.shuffle = False
        self.repeat = 0               # 0 = no, 1 = todo, 2 = una
        self.auto_ctx = False         # la lista es una cola automática (semilla + similares/respaldo)
        self.album = False            # la lista es un álbum: al acabar puede seguir la automática
        self.played: set[str] = set()

    def start(self, ctx: list[dict], first: int, auto: bool) -> None:
        """Empieza a reproducir `ctx` desde el índice `first`."""
        self.ctx, self.auto_ctx = ctx, auto
        self.make_order(first)

    def make_order(self, first: int) -> None:
        self.order = list(range(len(self.ctx)))
        if self.shuffle and self.order and not self.auto_ctx:  # la cola automática va en su orden
            random.shuffle(self.order)
            self.order.remove(first)
            self.order.insert(0, first)
        self.opos = self.order.index(first) if self.order else 0

    def append(self, tracks: list[dict]) -> None:
        start = len(self.ctx)
        self.ctx.extend(tracks)
        self.order.extend(range(start, len(self.ctx)))

    def current(self, video_id: str | None) -> dict | None:
        return next((t for t in self.ctx if t["video_id"] == video_id), None)

    def skip_remote(self, pos: int, step: int) -> int:
        """Sin internet se saltan las canciones que solo existen en YT Music."""
        if step == 0:
            return pos
        while 0 <= pos < len(self.order) and not self._is_online() \
                and self._is_remote(self.ctx[self.order[pos]]):
            pos += step
        return pos

    def next_tracks(self, n: int = 2) -> list[dict]:
        """Las próximas `n` canciones en el orden en que sonarán (cola manual primero)."""
        nxt = list(self.up_next[:n])
        if len(nxt) < n and self.order:
            for k in range(1, n + 1):
                j = self.opos + k
                if j >= len(self.order):
                    if self.repeat < 1:
                        break
                    j %= len(self.order)
                nxt.append(self.ctx[self.order[j]])
        return nxt[:n]

    def peek_next(self, current: dict | None) -> dict | None:
        """La canción que avanzar elegiría ahora, sin cambiar nada."""
        if self.repeat == 2 and current and (self._is_online() or not self._is_remote(current)):
            return current
        if self.up_next:
            return self.up_next[0]
        pos = self.skip_remote(self.opos + 1, 1)
        if not 0 <= pos < len(self.order):
            if not (self.order and self.repeat >= 1):
                return None
            pos = self.skip_remote(0, 1)
            if not 0 <= pos < len(self.order):
                return None
        return self.ctx[self.order[pos]]

    def playable_ahead(self) -> int:
        """Cuántas canciones reproducibles quedan por delante (sin internet, solo las locales)."""
        return sum(1 for j in range(self.opos + 1, len(self.order))
                   if self._is_online() or not self._is_remote(self.ctx[self.order[j]]))

    # ---------- edición de la cola ----------
    def take_up_next(self, index: int) -> dict | None:
        """Saca una canción de la cola manual (para quitarla o moverla)."""
        if 0 <= index < len(self.up_next):
            return self.up_next.pop(index)
        return None

    def take_upcoming(self, pos: int) -> dict | None:
        """Saca la canción que está en la posición `pos` de `order`, sin tocar la actual."""
        if not 0 <= pos < len(self.order) or pos == self.opos:
            return None
        ctx_idx = self.order[pos]
        track = self.ctx[ctx_idx]
        del self.order[pos]
        del self.ctx[ctx_idx]
        # los índices de ctx que quedaban por encima bajan uno
        self.order = [i - 1 if i > ctx_idx else i for i in self.order]
        if pos < self.opos:
            self.opos -= 1
        return track

    def insert_up_next(self, pos: int, track: dict) -> None:
        """Devuelve a la cola manual la canción en la posición `pos`."""
        self.up_next.insert(max(0, min(pos, len(self.up_next))), track)

    def insert_upcoming(self, pos: int, track: dict) -> None:
        """Devuelve la canción a la lista para que suene en la posición `pos` de `order`."""
        had_order = bool(self.order)
        pos = max(self.opos + 1 if had_order else 0, min(pos, len(self.order)))
        self.ctx.append(track)
        self.order.insert(pos, len(self.ctx) - 1)
        if had_order and pos <= self.opos:  # solo si se inserta antes de la actual
            self.opos += 1
