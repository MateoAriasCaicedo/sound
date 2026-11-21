"""Bienvenida con la biblioteca vacía: cuándo sale y qué dice (sin abrir ventanas)."""
from app.ui.welcome import WelcomeMixin, fit_count, progress_text, wanted


def test_it_shows_only_with_an_empty_library_in_home_or_all():
    assert wanted(0, "home", "", None, False, False)
    assert wanted(0, "all", "  ", None, False, False)
    assert not wanted(1, "home", "", None, False, False)          # ya hay música
    assert not wanted(0, "liked", "", None, False, False)         # cada vista tiene su aviso
    assert not wanted(0, "playlist", "", None, False, False)
    assert not wanted(0, "home", "queen", None, False, False)     # buscando
    assert not wanted(0, "all", "", None, True, False)
    assert not wanted(0, "all", "", None, False, True)            # un género de YouTube
    assert not wanted(0, "all", "", {"kind": "artist"}, False, False)


def test_progress_text():
    assert progress_text(0) is None
    assert "queda 1." in progress_text(1)
    assert "quedan 12." in progress_text(12)


def test_fit_count_keeps_whole_chips():
    assert fit_count([50, 50, 50], 120) == 2
    assert fit_count([50, 50, 50], 150) == 3
    assert fit_count([50, 50, 50], 10) == 0
    assert fit_count([], 100) == 0


class _Genres:
    def __init__(self):
        self.selected: list[int] = []
        self.seen: list[int] = []

    def selection_clear(self):
        self.selected = []

    def selection_set(self, i):
        self.selected.append(i)

    def see(self, i):
        self.seen.append(i)


class _FakeApp(WelcomeMixin):
    def __init__(self, folded=()):
        self._side_folded = set(folded)
        self.genres = _Genres()
        self.side_items: list[tuple] = []
        self.picked: list[list[int]] = []
        self.saved = 0
        self._build_sidebar()

    def _build_sidebar(self):
        items = [("home", None, "Inicio"), ("hdr", "yt", "YouTube")]
        if "yt" not in self._side_folded:
            items += [("yt", "Salsa", "Salsa"), ("yt", "Bachata", "Bachata")]
        self.side_items = items

    def save_prefs(self):
        self.saved += 1

    def _pick_genre(self):
        self.picked.append(list(self.genres.selected))


def test_a_genre_chip_opens_it_like_the_sidebar():
    app = _FakeApp()
    app._pick_yt_genre("Bachata")
    assert app.picked == [[3]] and app.genres.seen == [3] and not app.saved


def test_a_genre_chip_unfolds_the_youtube_section_first():
    app = _FakeApp(folded={"yt"})
    app._pick_yt_genre("Salsa")
    assert "yt" not in app._side_folded and app.saved == 1
    assert app.picked == [[2]]


def test_an_unknown_genre_does_nothing():
    app = _FakeApp()
    app._pick_yt_genre("Polka")
    assert app.picked == []
