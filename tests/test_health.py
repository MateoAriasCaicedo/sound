import sys

from app.core import health


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
