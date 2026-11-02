import pytest

from app.core import language


# Títulos reales de los resultados de búsqueda medidos (con su álbum cuando ayuda a ver el caso).
@pytest.mark.parametrize(
    "title",
    [
        "La Chica de Humo", "Esa Chica Es Mía", "Te amaré, te amo, y te querré", "El Día Que Puedas",
        "Cuando Vuelvas", "Un Beso Y Una Flor", "¿Y Cómo Es Él?", "Entre dos tierras",
        "Me Olvide De Vivir",  # sin tildes: 'de' basta
        "Suavemente", "Insoportablemente Bella",  # una palabra: la terminación -mente
    ],
)
def test_spanish_titles(title):
    assert language.guess_language(title) == "es"


@pytest.mark.parametrize(
    "title",
    [
        "Another Day in Paradise", "Everybody Wants To Rule The World", "Time After Time", "Take on Me",
        "Sultans Of Swing", "Highway to Hell", "Love Hurts", "Losing My Religion",
        "Have You Ever Seen The Rain", "Girls Just Want to Have Fun",
    ],
)
def test_english_titles(title):
    assert language.guess_language(title) == "en"


@pytest.mark.parametrize("title", ["Hotel California", "Africa", "Hysteria", "Funkytown", "Mojada", "Kumbala"])
def test_titles_without_clues_are_unknown_not_guessed(title):
    assert language.guess_language(title) is None


def test_a_weak_album_clue_does_not_override_the_title():
    # Banda en español con un álbum llamado 'Big Bang': antes salía como inglés
    assert language.guess_language("Lamento Boliviano", "Big Bang") is None
    assert language.guess_language("Mi Primer Dia Sin Ti", "Big Bang") == "es"
    assert language.guess_language("Thunderstruck", "The Razors Edge") is None


def test_an_album_with_enough_evidence_does_count():
    assert language.guess_language("Untitled", "Songs From The Big Chair") == "en"
    # visto en los datos reales: el título solo no dice nada, pero el álbum sí ('de' + la tilde)
    assert language.guess_language("Maldito duende") is None
    assert language.guess_language("Maldito duende", "Senderos De Traición") == "es"


def test_artist_vote_resolves_titles_without_clues():
    tracks = [
        {"artist": "Guns N' Roses", "title": "Welcome To The Jungle", "album": "Appetite For Destruction"},
        {"artist": "Guns N' Roses", "title": "Sweet Child O' Mine", "album": "Appetite For Destruction"},
        {"artist": "Hombres G", "title": "Devuélveme a mi chica", "album": None},
        {"artist": "Hombres G", "title": "Marta Tiene un Marcapasos", "album": None},
    ]
    votes = language.vote_by_artist(tracks)

    assert language.language_of(tracks[1], votes) == "en"
    assert votes["hombres g"] == "es"


def test_artist_vote_needs_a_clear_majority():
    tracks = [
        {"artist": "Dueto", "title": "Love Hurts", "album": None},  # en
        {"artist": "Dueto", "title": "Corazón de Hielo", "album": None},  # es
    ]

    assert "dueto" not in language.vote_by_artist(tracks)  # empate: no se decide por el artista


def test_a_title_with_clues_wins_over_the_artist_vote():
    track = {"artist": "Guns N' Roses", "title": "Sueño de Amor", "album": None}

    assert language.language_of(track, {"guns n' roses": "en"}) == "es"
