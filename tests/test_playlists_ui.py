"""«Tus listas» en la ventana, con una ventana de mentira (la base de datos es la de pruebas)."""
from app.core import playlists
from app.ui.navigation import History
from app.ui.playlists_ui import PlaylistsMixin


class _Toast:
    def __init__(self):
        self.shown: list[tuple] = []

    def show(self, text, action="", on_action=None, on_expire=None):
        self.shown.append((text, action, on_action))


class _FakeApp(PlaylistsMixin):
    def __init__(self):
        self.nav = History()
        self.toast = _Toast()
        self.view_kind = "home"
        self.playlist_id = None
        self.sidebar: list[int] = []
        self.went_home = 0
        self._build_sidebar()

    def _build_sidebar(self):
        self.playlists = playlists.all_playlists()
        self.sidebar = [pl["id"] for pl in self.playlists]

    def go_home(self):
        self.went_home += 1
        self.view_kind, self.playlist_id = "home", None

    def _sync_nav(self):
        pass

    def _select_playlist(self, pid):
        self._build_sidebar()
        self.view_kind, self.playlist_id = "playlist", pid


def test_deleting_the_open_list_also_removes_it_from_the_sidebar():
    pid = playlists.create("Para borrar")
    app = _FakeApp()
    app._select_playlist(pid)
    app.delete_playlist(pid)
    assert pid not in app.sidebar  # antes seguía en el lateral hasta el siguiente repintado
    assert app.went_home == 1 and app.view_kind == "home"
    text, action, undo = app.toast.shown[-1]
    assert action == "Deshacer"
    undo()
    assert app.view_kind == "playlist" and app.playlist_id in app.sidebar


def test_deleting_another_list_keeps_the_view():
    keep, gone = playlists.create("Se queda"), playlists.create("Se va")
    app = _FakeApp()
    app._select_playlist(keep)
    app.delete_playlist(gone)
    assert gone not in app.sidebar and keep in app.sidebar
    assert app.went_home == 0 and app.playlist_id == keep
