from app.core import atomic


def test_write_text_atomic_replaces_and_creates_parents(tmp_path):
    target = tmp_path / "a" / "b.json"
    atomic.write_text_atomic(target, "uno")
    atomic.write_text_atomic(target, "dos")
    assert target.read_text(encoding="utf-8") == "dos"
    assert list(target.parent.glob("*.tmp")) == []


def test_write_text_atomic_keeps_the_original_if_writing_fails(tmp_path, monkeypatch):
    target = tmp_path / "p.json"
    target.write_text("bueno", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(atomic.Path, "write_text", boom)
    try:
        atomic.write_text_atomic(target, "nuevo")
    except OSError:
        pass
    assert target.read_text(encoding="utf-8") == "bueno"
