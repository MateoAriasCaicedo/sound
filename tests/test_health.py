import io
import sys
import zipfile

from app.core import config, health


def test_problems_reports_missing_node():
    assert "Node.js" in health.problems(which=lambda name: None)[0]


def test_problems_empty_when_node_present():
    assert health.problems(which=lambda name: "C:/node.exe") == []


def test_looks_outdated_matches_real_ytdlp_breakage():
    assert health.looks_outdated(["ERROR: [youtube] abc: Unable to extract player"])
    assert health.looks_outdated(["nsig extraction failed: Some formats may be missing"])


def test_looks_outdated_ignores_permanent_and_network_errors():
    assert not health.looks_outdated(["ERROR: [youtube] zzz: This video is unavailable"])
    assert not health.looks_outdated(["HTTP Error 403: Forbidden", "timed out"])
    assert not health.looks_outdated([])


def test_update_components_runs_pip_for_both_packages():
    calls = []

    class Done:
        returncode = 0

    status = health.update_components(run=lambda cmd: calls.append(cmd) or Done())

    assert status == 0
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert "yt-dlp" in calls[0] and "ytmusicapi" in calls[0]
    assert "--dry-run" not in calls[0]


def test_update_components_dry_run_flag():
    calls = []

    class Done:
        returncode = 3

    status = health.update_components(run=lambda cmd: calls.append(cmd) or Done(), dry_run=True)

    assert status == 3
    assert calls[0][-1] == "--dry-run"


def test_needs_cookies_recognizes_bot_and_age_messages():
    assert health.needs_cookies(["Sign in to confirm you're not a bot"])
    assert health.needs_cookies(["Sign in to confirm your age"])
    assert not health.needs_cookies(["Video unavailable"])


def test_diagnostics_summarizes_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    checks = health.diagnostics(
        which=lambda name: "C:/node.exe" if name == "node" else None,
        online=lambda: True,
        cookies="chrome",
        data_dir=tmp_path,
    )
    by_name = {check["name"]: check for check in checks}
    assert by_name["Node.js"]["status"] == "ok"
    assert by_name["ffmpeg"]["status"] == "warn"
    assert by_name["Internet"]["status"] == "ok"
    assert by_name["Cookies de YouTube"]["status"] == "ok"
    assert by_name["Carpeta de datos"]["status"] == "ok"


def test_frozen_update_downloads_a_verified_wheel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VENDOR_DIR", tmp_path / "vendor")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("yt_dlp/__init__.py", "")
        archive.writestr("yt_dlp/version.py", "__version__ = '9.9.9'")
    payload = buffer.getvalue()
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()

    class Response:
        def __init__(self, data=None):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"info": {"version": "9.9.9"}, "urls": [{
                "packagetype": "bdist_wheel",
                "filename": "yt_dlp-9.9.9-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/x.whl",
                "digests": {"sha256": digest},
            }]}

        @property
        def content(self):
            return self._data

    calls = []

    def fetch(url, timeout=30):
        calls.append(url)
        return Response(payload) if url.endswith(".whl") else Response()

    assert health.update_ytdlp_vendor(fetch=fetch) == "9.9.9"
    assert (tmp_path / "vendor" / "current.json").exists()
    assert (tmp_path / "vendor" / "current.json").read_text(encoding="utf-8").find("9.9.9") >= 0
    dirs = [p for p in (tmp_path / "vendor").iterdir() if p.is_dir()]
    assert dirs and (dirs[0] / "yt_dlp" / "__init__.py").exists()


def test_frozen_update_rejects_a_bad_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VENDOR_DIR", tmp_path / "vendor")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"info": {"version": "9.9.9"}, "urls": [{
                "packagetype": "bdist_wheel", "filename": "x-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/x.whl",
                "digests": {"sha256": "00"},
            }]}

        content = b"no es un wheel"

    def fetch(url, timeout=30):
        return Response()

    assert health.update_ytdlp_vendor(fetch=fetch) is None
