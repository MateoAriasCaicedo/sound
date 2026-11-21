"""Cabecera de portada (mosaico) y arrastrar dentro de una lista, sin abrir ventanas."""
from PIL import Image

from app.ui.drag import new_order
from app.ui.hero import art_rows, compose_art


def test_dropping_moves_the_picked_songs_together_before_the_slot():
    order = ["a", "b", "c", "d", "e"]
    assert new_order(order, {"d"}, 1) == ["a", "d", "b", "c", "e"]
    assert new_order(order, {"b", "d"}, 5) == ["a", "c", "e", "b", "d"]  # al final
    assert new_order(order, {"b", "d"}, 0) == ["b", "d", "a", "c", "e"]
    assert new_order(order, {"c"}, 2) == order  # en su sitio: nada cambia
    assert new_order(order, {"c"}, 3) == order


def _img(color):
    return Image.new("RGB", (50, 40), color)


def test_mosaic_uses_four_covers_and_keeps_the_size():
    art = compose_art([_img("red"), _img("green"), _img("blue"), _img("white")], 101, "x")
    assert art.size == (101, 101) and art.mode == "RGBA"
    assert art.getpixel((25, 25))[:3] == (255, 0, 0)       # arriba a la izquierda
    assert art.getpixel((75, 75))[:3] == (255, 255, 255)   # abajo a la derecha
    assert art.getpixel((0, 0))[3] < 255                   # esquina redondeada


def test_single_cover_placeholder_and_artist_circle():
    one = compose_art([_img("red"), None], 64, "x")
    assert one.getpixel((32, 32))[:3] == (255, 0, 0)
    empty = compose_art([], 64, "una lista")
    assert empty.getpixel((32, 32))[3] == 255  # degradado propio, opaco en el centro
    circle = compose_art([_img("red")] * 4, 64, "x", circle=True)
    assert circle.getpixel((32, 32))[:3] == (255, 0, 0)  # el artista: una portada, en círculo
    assert circle.getpixel((6, 6))[3] == 0


def test_art_rows_pick_one_song_per_album():
    rows = [{"video_id": "1", "album": "Rumours", "artist": "Fleetwood Mac"},
            {"video_id": "2", "album": "Rumours", "artist": "Fleetwood Mac"},
            {"video_id": "3", "album": "Tango in the Night", "artist": "Fleetwood Mac"},
            {"video_id": "4", "album": None, "artist": "ABBA"},
            {"video_id": "5", "album": None, "artist": "ABBA"}]
    assert [t["video_id"] for t in art_rows(rows)] == ["1", "3", "4", "5"]
    assert len(art_rows(rows, limit=2)) == 2
