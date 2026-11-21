"""Versiones nuevas de Sound: sin red (las respuestas de GitHub son de mentira)."""
import hashlib
import io
import json
import os
import time

import pytest

from app.core import app_update as au

INSTALLER = b"MZ" + b"instalador de prueba" * 5000


def _release_json(tag="v1.4.0", with_sum=True, **extra):
    assets = [
        {"name": f"Sound-{tag.lstrip('v')}-windows.zip", "browser_download_url": "https://x/zip",
         "size": 10},
        {"name": f"Sound-{tag.lstrip('v')}-setup.exe", "browser_download_url": "https://x/setup",
         "size": len(INSTALLER)},
    ]
    if with_sum:
        assets.append({"name": f"Sound-{tag.lstrip('v')}-setup.exe.sha256",
                       "browser_download_url": "https://x/sum", "size": 90})
    data = {"tag_name": tag, "body": "## Novedades\n- **Atrás** y Adelante\n- [Listas](https://x)",
            "html_url": "https://github.com/x/releases/tag/" + tag, "assets": assets}
    data.update(extra)
    return data


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(routes: dict):
    def open_(request, timeout=None):
        body = routes[request.full_url]
        if isinstance(body, Exception):
            raise body
        return _Response(body)
    return open_


def test_versions_compare_numerically():
    assert au.parse_version("v1.10.0") == (1, 10, 0)
    assert au.parse_version("1.3.0-beta") is None and au.parse_version("") is None
    assert au.is_newer("1.10.0", "1.9.3") and au.is_newer("v1.3", "1.2.9")
    assert not au.is_newer("1.2.0", "1.2.0") and not au.is_newer("1.2", "1.2.0")
    assert not au.is_newer("nightly", "1.2.0")


def test_release_finds_the_installer_and_its_checksum():
    release = au.parse_release(_release_json())
    assert release.version == "1.4.0" and release.installable
    assert release.installer_name == "Sound-1.4.0-setup.exe"
    assert release.checksum_url == "https://x/sum"
    assert not au.parse_release(_release_json(with_sum=False)).installable
    assert au.parse_release(_release_json(prerelease=True)) is None
    assert au.parse_release(_release_json(draft=True)) is None
    assert au.parse_release({"tag_name": "latest"}) is None


def test_fetch_latest_only_returns_newer_versions():
    routes = {au.LATEST_API: json.dumps(_release_json("v1.4.0")).encode()}
    assert au.fetch_latest("1.3.0", opener=_opener(routes)).version == "1.4.0"
    assert au.fetch_latest("1.4.0", opener=_opener(routes)) is None
    with pytest.raises(au.UpdateError):
        au.fetch_latest("1.3.0", opener=_opener({au.LATEST_API: OSError("sin red")}))
    with pytest.raises(au.UpdateError):
        au.fetch_latest("1.3.0", opener=_opener({au.LATEST_API: b"<html>"}))


def test_the_request_says_nothing_about_the_user():
    seen = {}

    def open_(request, timeout=None):
        seen.update(request.headers)
        return _Response(json.dumps(_release_json()).encode())

    au.fetch_latest("1.3.0", opener=open_)
    assert seen == {"User-agent": "Sound/1.3.0 (actualizaciones)",
                    "Accept": "application/vnd.github+json"}


def test_summary_gives_one_line_per_changelog_point():
    notes = ("### Navegación\n"
             "- **Atrás y Adelante, como en un navegador.** Botones junto al buscador,\n"
             "  Alt+← / Alt+→ y los botones laterales del ratón.\n"
             "  - un sub-punto que no cabe\n"
             "- La ruta `Todo › Artista` sobre el título: cada tramo lleva a su nivel.\n"
             "* [Listas](https://x) con portada\n<!-- x -->\n"
             "**Full Changelog**: https://github.com/x/compare/v1...v2\n")
    assert au.summary(notes) == ("• Atrás y Adelante, como en un navegador.\n"
                                 "• La ruta Todo › Artista sobre el título\n"
                                 "• Listas con portada")
    assert len(au.summary("\n".join(f"- punto {i}" for i in range(20))).splitlines()) == 6
    assert au.summary("**Full Changelog**: https://x") == ""


def test_checksum_file_formats():
    digest = "a" * 64
    assert au._expected_hash(f"{digest}  Sound-1.4.0-setup.exe", "Sound-1.4.0-setup.exe") == digest
    assert au._expected_hash(digest.upper(), "Sound-1.4.0-setup.exe") == digest
    sums = f"{'b' * 64}  otra.zip\n{digest} *Sound-1.4.0-setup.exe\n"
    assert au._expected_hash(sums, "Sound-1.4.0-setup.exe") == digest
    assert au._expected_hash("nada", "Sound-1.4.0-setup.exe") is None


def test_download_checks_the_sha256(tmp_path):
    release = au.parse_release(_release_json())
    good = hashlib.sha256(INSTALLER).hexdigest()
    routes = {"https://x/sum": f"{good}  Sound-1.4.0-setup.exe".encode(),
              "https://x/setup": INSTALLER}
    seen = []
    path = au.download(release, tmp_path, "1.3.0", progress=lambda d, t: seen.append((d, t)),
                       opener=_opener(routes))
    assert path.read_bytes() == INSTALLER and path.name == "Sound-1.4.0-setup.exe"
    assert seen[-1] == (len(INSTALLER), len(INSTALLER))
    assert not list(tmp_path.glob("*.part"))


def test_a_tampered_download_is_deleted_and_not_installed(tmp_path):
    release = au.parse_release(_release_json())
    routes = {"https://x/sum": ("c" * 64).encode(), "https://x/setup": INSTALLER}
    with pytest.raises(au.UpdateError, match="no coincide"):
        au.download(release, tmp_path, "1.3.0", opener=_opener(routes))
    assert list(tmp_path.iterdir()) == []


def test_without_a_published_checksum_nothing_is_downloaded(tmp_path):
    release = au.parse_release(_release_json(with_sum=False))
    with pytest.raises(au.UpdateError, match="GitHub"):
        au.download(release, tmp_path, "1.3.0", opener=_opener({}))


def test_a_cancelled_download_leaves_nothing(tmp_path):
    release = au.parse_release(_release_json())
    good = hashlib.sha256(INSTALLER).hexdigest()
    routes = {"https://x/sum": good.encode(), "https://x/setup": INSTALLER}
    with pytest.raises(au.UpdateError, match="cancelada"):
        au.download(release, tmp_path, "1.3.0", cancelled=lambda: True, opener=_opener(routes))
    assert list(tmp_path.iterdir()) == []


def test_install_command_is_silent_relaunches_and_keeps_autostart_as_it_is(tmp_path):
    exe = tmp_path / "Sound-1.4.0-setup.exe"
    command = au.install_command(exe, autostart=False)
    assert command[0] == str(exe)
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/RELAUNCH", "/CLOSEAPPLICATIONS"} <= set(command)
    assert command[-1] == "/MERGETASKS=!autostart"
    assert au.install_command(exe, autostart=True)[-1] == "/MERGETASKS=autostart"
    assert not any(c.startswith("/MERGETASKS") for c in au.install_command(exe))


def test_clean_removes_old_installers_only(tmp_path):
    old = tmp_path / "Sound-1.3.0-setup.exe"
    part = tmp_path / "Sound-1.4.0-setup.exe.part"
    fresh = tmp_path / "Sound-1.4.0-setup.exe"
    other = tmp_path / "notas.txt"
    for f in (old, part, fresh, other):
        f.write_bytes(b"x")
    hour_ago = time.time() - 7200
    os.utime(old, (hour_ago, hour_ago))
    os.utime(part, (hour_ago, hour_ago))
    au.clean(tmp_path, older_than_s=3600)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Sound-1.4.0-setup.exe", "notas.txt"]
