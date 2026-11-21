"""Duplicate review does not select or delete files automatically."""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from test_smoke import _wait

from commander import duplicates
from commander.duplicates_dialog import DuplicatesDialog

_app = None


@pytest.fixture
def make_dialog():
    global _app
    _app = QApplication.instance() or QApplication([])
    dialogs = []

    def make(root):
        dialog = DuplicatesDialog(root)
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.worker.cancel()
        dialog.worker.wait()
        dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_group_review_requires_an_unselected_file(tmp_path, make_dialog):
    for name in ("one", "two", "three"):
        (tmp_path / name).write_text("identical")
    dialog = make_dialog(tmp_path)
    _wait(lambda: dialog.worker.isFinished() and dialog.tree.topLevelItemCount() == 1)
    group = dialog.tree.topLevelItem(0)
    assert group.childCount() == 3
    assert not dialog.trash_btn.isEnabled()
    for index in range(3):
        group.child(index).setSelected(True)
    assert not dialog.trash_btn.isEnabled()
    assert "하나 이상" in dialog.status.text()
    group.child(0).setSelected(False)
    assert dialog.trash_btn.isEnabled()
    expected = {group.child(i).text(0) for i in (1, 2)}
    dialog.trash_btn.click()
    assert dialog.result() == QDialog.Accepted
    assert {str(path) for path in dialog.selected_paths} == expected
    assert dialog.result_data is not None
    assert len(dialog.selected_groups) == 1
    assert all(path.exists() for path in tmp_path.iterdir())


def test_cancel_discards_results(tmp_path, make_dialog, monkeypatch):
    def scan(_root, *, cancelled, on_error, on_progress):
        while not cancelled():
            time.sleep(0.005)
        raise duplicates.DuplicateScanCancelled

    monkeypatch.setattr(duplicates, "find_duplicates", scan)
    dialog = make_dialog(tmp_path)
    dialog.stop_btn.click()
    _wait(lambda: dialog.worker.isFinished() and not dialog.stop_btn.isEnabled())
    assert dialog.result_data is None
    assert not dialog.trash_btn.isEnabled()
    assert dialog.tree.topLevelItemCount() == 0


def test_scan_errors_are_shown(tmp_path, make_dialog, monkeypatch):
    def scan(root, *, cancelled, on_error, on_progress):
        on_error(root / "unreadable", PermissionError("denied"))
        return duplicates.ScanResult([], {})

    monkeypatch.setattr(duplicates, "find_duplicates", scan)
    dialog = make_dialog(tmp_path)
    _wait(lambda: dialog.worker.isFinished() and bool(dialog.errors.text()))
    assert "오류 1개" in dialog.errors.text()
    assert "unreadable" in dialog.errors.text()
    assert not dialog.trash_btn.isEnabled()
