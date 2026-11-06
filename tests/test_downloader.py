from app.core import cookies, downloader


def test_downloader_uses_the_shared_cookie_browser(monkeypatch):
    monkeypatch.setattr(cookies, "_browser", "")
    monkeypatch.setattr(cookies, "_generation", 0)

    downloader.configure_cookies("edge")

    assert downloader.cookies_browser() == "edge"
    assert downloader._options()["cookiesfrombrowser"] == ("edge",)


def test_safe_name_strips_forbidden_chars():
    assert downloader.safe_name('AC/DC: "Back" in Black?') == "AC_DC_ _Back_ in Black_"


def test_safe_name_never_empty():
    assert downloader.safe_name("...") == "sin_nombre"


def test_safe_name_avoids_windows_reserved_names():
    assert downloader.safe_name("CON") == "_CON"
    assert downloader.safe_name("aux.m4a") == "_aux.m4a"
    assert downloader.safe_name("Comunicacion") == "Comunicacion"


def test_disk_level_thresholds():
    assert downloader.disk_level(downloader.LOW_FREE_BYTES + 1) == "ok"
    assert downloader.disk_level(downloader.LOW_FREE_BYTES - 1) == "low"
    assert downloader.disk_level(downloader.MIN_FREE_BYTES - 1) == "critical"


def test_disk_level_uses_hysteresis_to_avoid_flapping():
    just_below_low = downloader.LOW_FREE_BYTES - 1
    recovering = downloader.LOW_FREE_BYTES + downloader.DISK_RECOVER_BYTES - 1
    recovered = downloader.LOW_FREE_BYTES + downloader.DISK_RECOVER_BYTES + 1
    assert downloader.disk_level(just_below_low, "low") == "low"
    assert downloader.disk_level(recovering, "low") == "low"
    assert downloader.disk_level(recovered, "low") == "ok"
    assert downloader.disk_level(downloader.MIN_FREE_BYTES + 1, "critical") == "critical"
    assert downloader.disk_level(
        downloader.MIN_FREE_BYTES + downloader.DISK_RECOVER_BYTES + 1, "critical") == "low"


def test_estimate_track_bytes_uses_duration_with_a_floor():
    assert downloader.estimate_track_bytes({"duration": 30}) == downloader.EST_TRACK_BYTES
    assert downloader.estimate_track_bytes({"duration": None}) == downloader.EST_TRACK_BYTES
    assert downloader.estimate_track_bytes({"duration": 600}) > downloader.EST_TRACK_BYTES
    assert downloader.estimate_pending_bytes([{"duration": 30}, {"duration": 600}]) > \
        downloader.EST_TRACK_BYTES
