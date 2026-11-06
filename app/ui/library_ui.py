"""Mixin de gestión de biblioteca para la ventana principal."""


class LibraryMixin:
    def _reload_library(self) -> None:
        from .main import load_library

        self.library = load_library()
        self.lib_by_id = {t["video_id"]: t for t in self.library}
        from ..core import favorites

        self.liked = favorites.liked_ids()
        self._rebuild_search_cache()

    def refresh_library(self) -> None:
        self._reload_library()
        self._build_sidebar()
        self.refresh_view()
