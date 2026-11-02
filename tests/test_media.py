from app.core.media import is_truncated


def test_complete_file_is_not_truncated():
    assert not is_truncated(200, 200_000)


def test_small_metadata_difference_is_tolerated():
    # 15 s menos en una canción de 200 s: dentro del margen (20 % = 40 s)
    assert not is_truncated(200, 185_000)


def test_much_shorter_audio_is_truncated():
    # la mitad de una canción de 200 s: archivo incompleto
    assert is_truncated(200, 100_000)


def test_short_tracks_use_the_absolute_margin():
    assert not is_truncated(20, 19_000)  # 1 s de diferencia
    assert is_truncated(20, 4_000)       # 16 s de diferencia


def test_unknown_values_are_not_flagged():
    assert not is_truncated(None, 100_000)
    assert not is_truncated(200, None)
    assert not is_truncated(0, 100_000)
    assert not is_truncated(200, 0)


def test_text_and_invalid_values_do_not_break():
    assert is_truncated("200", 100_000)
    assert not is_truncated("no es un número", 100_000)
