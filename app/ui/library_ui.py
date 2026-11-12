"""Mixin de gestión de biblioteca para la ventana principal."""


class LibraryMixin:
    def _reload_library(self) -> None:
        from .main import load_library

        hidden = getattr(self, "_hidden", set())  # borradas que aún se pueden deshacer
        self.library = [t for t in load_library() if t["video_id"] not in hidden]
        self.lib_by_id = {t["video_id"]: t for t in self.library}
        from ..core import favorites

        self.liked = favorites.liked_ids() - hidden
        self._rebuild_search_cache()

    def refresh_library(self) -> None:
        self._reload_library()
        self._build_sidebar()
        self.refresh_view()
        self._refresh_now_heart()
        if getattr(self, "now_open", False):  # una descarga pudo completar su álbum
            self._now_album()
