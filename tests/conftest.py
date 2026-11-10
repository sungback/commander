import pytest

from commander import ops, shell


@pytest.fixture(autouse=True)
def _no_side_effects_outside_tmp(monkeypatch):
    """Tests never touch the real trash and never launch desktop apps or helper processes."""
    def fake_trash(path):
        ops.remove(path)
        return True

    monkeypatch.setattr(ops, "_move_to_trash", fake_trash)
    monkeypatch.setattr(shell, "launch", lambda argv, cwd=None: None)
    try:
        from PySide6.QtGui import QDesktopServices
    except ImportError:
        return
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: True)
