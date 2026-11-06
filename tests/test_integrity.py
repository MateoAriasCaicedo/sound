"""Integridad: una sola regla de duración y cuarentena en vez de borrado automático."""
from app.core import media


def test_duration_mismatch_uses_the_shared_tolerance():
    assert not media.duration_mismatch(200, 205)
    assert not media.duration_mismatch(20, 19)
    assert media.duration_mismatch(200, 100)
    assert media.duration_mismatch(20, 4)


def test_duration_mismatch_ignores_unknown_values():
    assert not media.duration_mismatch(None, 100)
    assert not media.duration_mismatch(200, None)
    assert not media.duration_mismatch("no es un número", 100)


def test_quarantine_renames_instead_of_deleting(tmp_path):
    original = tmp_path / "Tema.m4a"
    original.write_bytes(b"audio")

    moved = media.quarantine(original)

    assert moved is not None and moved.exists()
    assert not original.exists()
    assert moved.read_bytes() == b"audio"
    assert moved.name.startswith("Tema.damaged-")


def test_quarantine_does_not_overwrite_an_existing_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "time", lambda: 1)
    original = tmp_path / "Tema.m4a"
    original.write_bytes(b"nuevo")
    old = tmp_path / "Tema.damaged-1.m4a"
    old.write_bytes(b"viejo")

    moved = media.quarantine(original)

    assert moved is not None and moved != old
    assert old.read_bytes() == b"viejo"
    assert moved.read_bytes() == b"nuevo"
