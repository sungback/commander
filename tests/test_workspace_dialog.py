"""Named workspace interaction and confirmation."""

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from commander import workspaces
from commander.workspace_dialog import WorkspaceDialog


def test_save_restore_delete_with_confirmations(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    tab = {"path": str(tmp_path), "locked": True}
    state = {"panels": [{"active": 0, "tabs": [tab]}, {"active": 0, "tabs": [tab]}],
             "active_panel": 0}
    path = tmp_path / "workspaces.json"
    dialog = WorkspaceDialog(path, state)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("업무", True))
    dialog.save_btn.click()
    assert workspaces.load(path) == {"업무": state}
    assert dialog.restore_btn.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    dialog.delete_btn.click()
    assert workspaces.load(path) == {"업무": state}
    dialog.restore_btn.click()
    assert dialog.selected_state == state
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert app


def test_corrupt_existing_file_disables_changes(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "workspaces.json"
    path.write_text("bad", encoding="utf-8")
    dialog = WorkspaceDialog(path, {})
    assert not dialog.save_btn.isEnabled()
    assert "읽을 수 없습니다" in dialog.status.text()
    assert path.read_text(encoding="utf-8") == "bad"
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert app


def test_cancel_overwrite_and_corruption_after_open_preserves_file(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    tab = {"path": str(tmp_path)}
    state = {"panels": [{"active": 0, "tabs": [tab]}, {"active": 0, "tabs": [tab]}],
             "active_panel": 0}
    path = tmp_path / "workspaces.json"
    workspaces.save(path, {"업무": state})
    changed = {**state, "active_panel": 1}
    dialog = WorkspaceDialog(path, changed)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("업무", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    dialog.save_btn.click()
    assert workspaces.load(path) == {"업무": state}
    path.write_text("corrupted", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    dialog.save_btn.click()
    assert warnings and path.read_text(encoding="utf-8") == "corrupted"
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert app
