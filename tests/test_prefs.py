from app.core import log
from app.ui import prefs


def test_roundtrip(tmp_path):
    path = tmp_path / "p.json"
    prefs.save({"dark": True, "vol": 55}, path)
    assert prefs.load(path) == {"dark": True, "vol": 55}


def test_missing_file_gives_defaults(tmp_path):
    assert prefs.load(tmp_path / "nada.json") == {}


def test_corrupt_or_wrong_shape_gives_defaults(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{no es json", encoding="utf-8")
    assert prefs.load(path) == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert prefs.load(path) == {}


def test_save_failure_does_not_raise(tmp_path):
    prefs.save({"a": 1}, tmp_path / "no" / "existe" / "p.json")


def test_failure_logs_network_errors_briefly_and_others_with_trace(caplog):
    logger = log.get("test")
    with caplog.at_level("INFO", logger="sound"):
        log.failure(logger, "red", ConnectionError("sin internet"))
        log.failure(logger, "bug", KeyError("x"))
    network, bug = caplog.records
    assert network.levelname == "INFO" and network.exc_info is None
    assert bug.levelname == "WARNING" and bug.exc_info is not None
