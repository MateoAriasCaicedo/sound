"""Historial de navegación (Atrás / Adelante) sin abrir ventanas."""
from app.ui.navigation import History, NavigationMixin, Place, section_key

HOME = Place(section=(("home", None),))
ALL = Place(section=(("all", None),))
ROCK = Place(section=(("lib", "Rock"),))
ARTIST = Place(section=(("all", None),), views=(("artist", "queen", "Queen", "Queen"),))
ALBUM = Place(section=(("all", None),), views=(("artist", "queen", "Queen", "Queen"),
                                               ("album", ("queen", "the works"), "The Works",
                                                "Queen")))
LIST7 = Place(section=(("pl", 7),))


def test_visit_back_and_forward_walk_the_places_in_order():
    h = History()
    for place in (HOME, ALL, ARTIST, ALBUM):
        assert h.visit(place)
    assert h.back() == ARTIST and h.back() == ALL
    assert h.can_back and h.can_forward
    assert h.forward() == ARTIST
    assert h.peek_back() == ALL and h.peek_forward() == ALBUM


def test_going_somewhere_new_after_going_back_drops_forward():
    h = History()
    for place in (HOME, ALL, ARTIST):
        h.visit(place)
    h.back()
    h.visit(ROCK)
    assert h.entries == [HOME, ALL, ROCK] and not h.can_forward


def test_same_place_is_updated_not_stacked():
    h = History()
    h.visit(ALL)
    assert not h.visit(Place(section=ALL.section, decade=1980, scroll=0.4))
    assert len(h.entries) == 1 and h.current.decade == 1980
    h.note(scroll=0.7, selected=("abc",))
    assert h.current.scroll == 0.7 and h.current.selected == ("abc",)


def test_searching_is_its_own_place_and_refining_it_is_not():
    h = History()
    h.visit(ALL)
    assert h.visit(Place(section=ALL.section, query="que"))
    assert not h.visit(Place(section=ALL.section, query="queen"))
    assert len(h.entries) == 2 and h.current.query == "queen"
    assert h.visit(Place(section=ALL.section, query=""))  # borrar la búsqueda es otro sitio


def test_pages_and_album_views_are_places():
    settings = Place(section=ALBUM.section, views=ALBUM.views, page="settings")
    assert not settings.same(ALBUM) and not ALBUM.same(ARTIST)
    # cambiar de década o desplazar no cambia de sitio
    assert Place(section=ALBUM.section, views=ALBUM.views, decade=1990, scroll=0.5).same(ALBUM)


def test_forget_removes_a_deleted_list_and_merges_neighbours():
    h = History()
    for place in (HOME, LIST7, HOME, ALL, LIST7):
        h.visit(place)
    lost = h.forget(lambda p: ("pl", 7) in p.section)
    assert lost  # estaba en la lista borrada
    assert h.entries == [HOME, ALL] and h.current == ALL


def test_forget_keeps_the_position_when_the_current_place_survives():
    h = History()
    for place in (HOME, LIST7, ALL, ROCK):
        h.visit(place)
    h.back()  # en Todo
    assert not h.forget(lambda p: ("pl", 7) in p.section)
    assert h.current == ALL and h.entries == [HOME, ALL, ROCK]


def test_history_has_a_limit():
    h = History(limit=5)
    for i in range(12):
        h.visit(Place(section=(("lib", f"g{i}"),)))
    assert len(h.entries) == 5 and h.current.section == (("lib", "g11"),)
    assert h.index == 4


def test_section_key_orders_mixed_names():
    assert section_key({("lib", "Rock"), ("lib", "Jazz")}) == (("lib", "Jazz"), ("lib", "Rock"))
    assert section_key({("pl", 3), ("home", None)}) == (("home", None), ("pl", 3))


# ---------- la mezcla con la ventana, con una ventana de mentira ----------
class _Query:
    def __init__(self):
        self.text = ""

    def get(self):
        return self.text


class _FakeApp(NavigationMixin):
    def __init__(self):
        self._init_navigation()
        self.sel_keys = {("home", None)}
        self.views: list[dict] = []
        self.pages: dict = {}
        self.active_page = None
        self.query = _Query()
        self.decade = None
        self.now_open = False
        self.scroll = 0.0
        self.side_items = [("home", None, "Inicio"), ("all", None, "Todo"),
                           ("lib", "Rock", "Rock")]
        self.restored: list[Place] = []
        self.synced = 0

    # lo que la mezcla pide a la App
    def _selected(self):
        return []

    def _scroll_fraction(self):
        return self.scroll

    def close_now_view(self):
        self.now_open = False

    def _restore_place(self, place):
        self.restored.append(place)
        self.sel_keys = set(place.section)
        self.views = [{"kind": k, "key": key, "title": t, "artist": a}
                      for k, key, t, a in place.views]
        self.query.text = place.query

    def _sync_nav(self):
        self.synced += 1

    # acciones de prueba
    def pick(self, key):
        with self._navigating():
            self.sel_keys = {key}
            self.views = []

    def open_artist(self, name):
        with self._navigating():
            self.views.append({"kind": "artist", "key": name.lower(), "title": name,
                               "artist": name})


def test_navigating_notes_the_place_left_and_stacks_the_new_one():
    app = _FakeApp()
    app._visited()
    app.scroll = 0.6  # bajó por Inicio
    app.pick(("all", None))
    app.open_artist("Queen")
    assert [p.section for p in app.nav.entries] == [(("home", None),), (("all", None),),
                                                    (("all", None),)]
    assert app.nav.entries[0].scroll == 0.6
    app.go_back()
    assert app.restored[-1].section == (("all", None),) and not app.restored[-1].views
    assert app._place_label(app.nav.peek_back()) == "Inicio"
    assert app._place_label(app.nav.peek_forward()) == "Queen"


def test_nested_navigation_counts_once():
    app = _FakeApp()
    app._visited()
    with app._navigating():
        app.pick(("all", None))  # dentro de otra acción: no apila por su cuenta
        app.open_artist("ABBA")
    assert len(app.nav.entries) == 2


def test_clearing_the_search_goes_back_to_where_you_were():
    app = _FakeApp()
    app._visited()  # Inicio
    app.query.text = "queen"
    app.sel_keys = {("all", None)}  # buscar desde Inicio busca en todo
    app._visited()
    assert app.nav.current.searching and len(app.nav.entries) == 2
    app.query.text = ""
    assert app._leave_search()
    assert app.restored[-1].section == (("home", None),)
    assert app.nav.can_forward  # la búsqueda sigue en «Adelante»


def test_back_first_closes_the_big_now_playing_view():
    app = _FakeApp()
    app._visited()
    app.pick(("all", None))
    app.now_open = True
    app.go_back()
    assert not app.now_open and not app.restored  # solo se cerró la vista en grande


def test_mouse_side_buttons_do_not_fire_twice_for_one_click():
    app = _FakeApp()
    app._visited()
    app.pick(("all", None))
    app.pick(("lib", "Rock"))
    app._mouse_side(False)
    app._mouse_side(False)  # el mismo clic, repetido en el mismo instante
    assert app.nav.index == 1


def test_place_labels():
    app = _FakeApp()
    assert app._place_label(HOME) == "Inicio"
    assert app._place_label(ALBUM) == "The Works"
    assert app._place_label(Place(section=ALL.section, query=" abba ")) == "«abba»"
    assert app._section_label((("lib", "Rock"), ("all", None))) == "Todo y 1 más"
