"""Mixin de gestión de biblioteca para la ventana principal."""
from pathlib import Path


class LibraryMixin:
    def _reload_library(self) -> None:
        from .main import load_library

        self.library = load_library()
        if not self._library_cleaned:
            self._library_cleaned = True
            from ..core import downloader, library
            bad = []
            for t in list(self.library):
                try:
                    if not downloader._audio_ok(Path(t["path"]), t):
                        bad.append(t)
                        Path(t["path"]).unlink(missing_ok=True)
                        library.remove_track(t["video_id"])
                except Exception:  # noqa: BLE001
                    pass
            if bad:
                self.library = [t for t in self.library if t not in bad]

                def _requeue() -> None:
                    if self.dl.service is not None:
                        self.dl.submit(bad, None, priority=5, quiet=True)
                    else:
                        self.after(1000, _requeue)

                _requeue()
                self.status.configure(
                    text=f"Se detectaron {len(bad)} archivo(s) dañado(s): se re-descargan")
        self.lib_by_id = {t["video_id"]: t for t in self.library}
        from ..core import favorites
        self.liked = favorites.liked_ids()

    def refresh_library(self) -> None:
        self._reload_library()
        self._build_sidebar()
        self.refresh_view()