"""Agrupación de canciones por álbum en la cuadrícula (función pura, sin Tk)."""
from app.ui.cover_grid import album_key, group_albums


def _t(vid, album, artist="Artista", path=True):
    track = {"video_id": vid, "title": f"Canción {vid}", "artist": artist, "album": album}
    if path:
        track["path"] = f"C:/musica/{vid}.m4a"
    return track


def test_album_key_ignores_case_accents_and_remote_tracks():
    assert album_key(_t("a", "Canción", "Él")) == album_key(_t("b", "cancion", "el"))
    assert album_key(_t("a", "Álbum", path=False)) is None  # resultado de YouTube
    assert album_key(_t("a", "")) is None
    assert album_key(_t("a", None)) is None


def test_songs_of_the_same_album_share_one_card_in_place_of_the_first():
    rows = [_t("1", "Rumours"), _t("2", "Single"), _t("3", "Rumours"), _t("4", "Rumours")]
    cards, members = group_albums(rows)
    assert len(cards) == 2
    assert "_album" in cards[0] and cards[0]["title"] == "Rumours"
    assert members[0] == (0, 2, 3)
    assert cards[0]["video_id"] == "1"  # la portada es la de la primera canción
    assert [t["video_id"] for t in cards[0]["_tracks"]] == ["1", "3", "4"]
    assert cards[1] is rows[1] and members[1] == (1,)


def test_single_song_albums_and_remote_results_stay_as_songs():
    rows = [_t("1", "Solo"), _t("2", "Remoto", path=False), _t("3", "Remoto", path=False)]
    cards, members = group_albums(rows)
    assert cards == rows
    assert members == [(0,), (1,), (2,)]


def test_same_album_name_by_different_artists_is_not_merged():
    rows = [_t("1", "Greatest Hits", "Queen"), _t("2", "Greatest Hits", "ABBA"),
            _t("3", "Greatest Hits", "Queen")]
    cards, members = group_albums(rows)
    assert members == [(0, 2), (1,)]


def test_every_row_belongs_to_exactly_one_card():
    rows = [_t(str(i), f"Álbum {i % 3}") for i in range(10)] + [_t("x", "")]
    _cards, members = group_albums(rows)
    flat = sorted(row for group in members for row in group)
    assert flat == list(range(len(rows)))


def test_artist_key_uses_the_main_artist_without_accents():
    from app.ui.cover_grid import artist_key
    assert artist_key({"artist": "Rubén Blades, Willie Colón"}) == "ruben blades"
    assert artist_key({"artist": "ABBA"}) == artist_key({"artist": "abba"})
    assert artist_key({"artist": None}) == ""


def test_decade_labels():
    from app.ui.main import App
    assert [App._decade_label(d) for d in (1960, 1980, 2000, 2020)] == [
        "60s", "80s", "2000s", "2020s"]
