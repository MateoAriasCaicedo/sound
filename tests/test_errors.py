from app.core import errors


def test_ansi_and_noise_are_cleaned():
    assert errors.clean("\x1b[0;31mERROR:\x1b[0m  algo   roto") == "ERROR: algo roto"


def test_bot_check_is_detected_and_has_a_cookie_hint():
    message = "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies..."
    assert errors.is_bot_check(message)
    assert errors.needs_cookies(message)
    friendly = errors.friendly(message)
    assert "bot" in friendly and "Cookies" in friendly


def test_age_restriction_asks_for_login():
    message = "Sign in to confirm your age. This video may be inappropriate for some users."
    assert errors.needs_cookies(message)
    assert "iniciar sesión" in errors.friendly(message)


def test_unavailable_is_not_confused_with_a_network_error():
    message = "ERROR: [youtube] abc: Video unavailable"
    friendly = errors.friendly(message)
    assert "no está disponible" in friendly
    assert "conexión" not in friendly


def test_throttle_and_outdated_get_actionable_messages():
    assert "limitando" in errors.friendly("HTTP Error 429: Too Many Requests")
    assert "desactualizado" in errors.friendly("Unable to extract player")


def test_wrong_duration_is_explained():
    assert "no coincide" in errors.friendly("dura 90s, se esperaban 200s")


def test_unknown_error_is_shortened_and_never_empty():
    assert len(errors.friendly("x" * 1000)) <= 200
    assert errors.friendly("") == "La descarga falló por un motivo desconocido."
