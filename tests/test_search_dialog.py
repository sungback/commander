"""Search result selection and actions."""
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from test_smoke import _wait

from commander.search_dialog import SearchDialog

_app = None


@pytest.fixture
def make_dialog():
    global _app
    _app = QApplication.instance() or QApplication([])
    dialogs = []

    def make(root):
        dialog = SearchDialog(root, "*.txt")
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.worker.cancel()
        dialog.worker.wait()
        dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("action,button", [
    ("copy", "copy_btn"), ("move", "move_btn"), ("trash", "trash_btn"),
])
def test_selected_results_are_returned_for_action(tmp_path, action, button, make_dialog):
    for name in ("one.txt", "two.txt", "three.txt"):
        (tmp_path / name).write_text(name)
    dialog = make_dialog(tmp_path)
    _wait(lambda: dialog.results.count() == 3 and dialog.worker.isFinished())
    assert not getattr(dialog, button).isEnabled()
    first, second = dialog.results.item(0), dialog.results.item(1)
    first.setSelected(True)
    assert dialog.goto_btn.isEnabled()
    second.setSelected(True)
    assert not dialog.goto_btn.isEnabled()
    getattr(dialog, button).click()
    assert dialog.result() == QDialog.Accepted
    assert dialog.action == action
    assert set(dialog.selected_paths) == {Path(first.text()), Path(second.text())}
    assert not dialog.worker.isRunning()


def test_single_selected_result_goes_to_folder(tmp_path, make_dialog):
    path = tmp_path / "a.txt"
    path.write_text("a")
    dialog = make_dialog(tmp_path)
    _wait(lambda: dialog.results.count() == 1 and dialog.worker.isFinished())
    dialog.results.item(0).setSelected(True)
    dialog.goto_btn.click()
    assert dialog.chosen == path
    assert dialog.action is None
    assert dialog.result() == QDialog.Accepted
