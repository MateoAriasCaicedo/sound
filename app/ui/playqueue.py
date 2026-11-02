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
        if self.repeat == 2 and current:
            return current
        if self.up_next:
            return self.up_next[0]
        pos = self.skip_remote(self.opos + 1, 1)
        if not 0 <= pos < len(self.order):
            if not (self.order and self.repeat >= 1):
                return None
            pos = 0
        return self.ctx[self.order[pos]]

    def playable_ahead(self) -> int:
        """Cuántas canciones reproducibles quedan por delante (sin internet, solo las locales)."""
        return sum(1 for j in range(self.opos + 1, len(self.order))
                   if self._is_online() or not self._is_remote(self.ctx[self.order[j]]))
