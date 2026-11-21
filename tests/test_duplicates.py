
import pytest

from commander import duplicates
from commander.transfer import Cancelled, Control


def test_groups_identical_files_including_empty_and_skips_symlinks(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("a", "sub/b"):
        (tmp_path / name).write_bytes(b"same")
    (tmp_path / "other").write_bytes(b"diff")
    (tmp_path / "empty1").touch()
    (tmp_path / "empty2").touch()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "a")
    except OSError:
        pass
    result = duplicates.find_duplicates(tmp_path)
    assert {frozenset(group) for group in result.groups} == {
        frozenset((tmp_path / "a", tmp_path / "sub/b")),
        frozenset((tmp_path / "empty1", tmp_path / "empty2")),
    }
    assert set(result.snapshots) == {p for group in result.groups for p in group}


def test_cancel_never_returns_partial_results(tmp_path):
    (tmp_path / "a").write_text("one")
    with pytest.raises(duplicates.DuplicateScanCancelled):
        duplicates.find_duplicates(tmp_path, cancelled=lambda: True)


def test_changed_or_unreadable_files_are_reported_and_excluded(tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text("same")
    original = duplicates._digest
    errors = []

    def fail_one(path, signature, cancelled):
        if path.name == "b":
            raise PermissionError("denied")
        if path.name == "c":
            path.write_text("changed")
        return original(path, signature, cancelled)

    monkeypatch.setattr(duplicates, "_digest", fail_one)
    result = duplicates.find_duplicates(tmp_path, on_error=lambda p, e: errors.append((p, e)))
    assert result.groups == []
    assert {p.name for p, _ in errors} == {"b", "c"}


def test_trash_requires_unchanged_unselected_duplicate(tmp_path):
    a, b, c = (tmp_path / name for name in ("a", "b", "c"))
    for p in (a, b, c):
        p.write_text("same")
    result = duplicates.find_duplicates(tmp_path)
    errors = []
    handler = lambda p, e: errors.append((p, e))
    duplicates.trash_duplicates(result, [a, b, c], control=Control(), on_error=handler)
    assert all(p.exists() for p in (a, b, c)) and len(errors) == 3

    errors.clear()
    c.write_text("newer")
    duplicates.trash_duplicates(result, [a, b], control=Control(), on_error=handler)
    assert a.exists() and b.exists()
    assert errors

    c.write_text("same")  # Even restored bytes have a changed snapshot.
    duplicates.trash_duplicates(result, [a], control=Control(), on_error=handler)
    assert not a.exists() and b.exists() and c.exists()


def test_cancelled_trash_preserves_files(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("same")
    b.write_text("same")
    result = duplicates.find_duplicates(tmp_path)
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):
        duplicates.trash_duplicates(result, [a], control=control, on_error=lambda p, e: None)
    assert a.exists() and b.exists()
