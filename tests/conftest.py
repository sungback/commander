import pytest

from commander import ops


@pytest.fixture(autouse=True)
def _no_real_trash(monkeypatch):
    """Tests must never touch the user's real trash: 'trashing' just removes inside tmp_path."""
    def fake(path):
        ops.remove(path)
        return True

    monkeypatch.setattr(ops, "_move_to_trash", fake)
