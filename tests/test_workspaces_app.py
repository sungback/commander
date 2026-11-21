"""Named workspaces restore both panels without silently losing live layouts."""
import copy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
from test_smoke import _setup

from commander import workspaces
from commander.app import MainWindow
from commander.workspace_dialog import WorkspaceDialog


@pytest.fixture
def window(tmp_path, monkeypatch):
    app, win, left, right = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    win._state_file = tmp_path / "state.json"
    yield app, win, left, right
    for panel in win.panels:
        for index in range(panel.tabs.count()):
            tab = panel.tabs.widget(index)
            worker = tab._size_worker
            tab._stop_size_worker()
            if worker is not None:
                worker.wait()
    win._state_file = None
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(win, QEvent.DeferredDelete)
    QApplication.processEvents()


def _restore_dialog(monkeypatch, snapshot):
    def execute(dialog):
        dialog.selected_state = copy.deepcopy(snapshot)
        return QDialog.Accepted

    monkeypatch.setattr(WorkspaceDialog, "exec", execute)


def test_save_restore_workspace_and_session(window, tmp_path, monkeypatch):
    _app, win, left, right = window
    extra_left = tmp_path / "left-extra"
    extra_right = tmp_path / "right-extra"
    extra_left.mkdir()
    extra_right.mkdir()
    lp, rp = win.panels
    lp.new_tab(str(extra_left))
    lp.set_tab_locked(0, True)
    rp.new_tab(str(extra_right))
    rp.set_tab_locked(1, True)
    rp.tabs.setCurrentIndex(0)
    win._active = 1
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("업무", True))

    def save(dialog):
        dialog.save_btn.click()
        return QDialog.Rejected

    monkeypatch.setattr(WorkspaceDialog, "exec", save)
    win.open_workspaces()
    path = tmp_path / "workspaces.json"
    saved = workspaces.load(path)["업무"]
    assert saved["panels"][0]["tabs"][0]["locked"]
    assert saved["panels"][1]["tabs"][1]["locked"]

    lp.new_tab(str(right))
    rp.new_tab(str(left))
    win._active = 0
    _restore_dialog(monkeypatch, saved)
    win.open_workspaces()
    assert [lp.tabs.widget(i).current_path() for i in range(lp.tabs.count())] == [str(left), str(extra_left)]
    assert [rp.tabs.widget(i).current_path() for i in range(rp.tabs.count())] == [str(right), str(extra_right)]
    assert (lp.tabs.currentIndex(), rp.tabs.currentIndex()) == (1, 0)
    assert lp.tabs.widget(0).locked and rp.tabs.widget(1).locked
    assert win.active_index() == 1

    win.save_state()
    reopened = MainWindow(state_file=tmp_path / "state.json")
    try:
        assert reopened.panels[0].tabs.count() == 2
        assert reopened.panels[0].tabs.widget(0).locked
        assert reopened.panels[1].tabs.widget(1).locked
    finally:
        for panel in reopened.panels:
            for index in range(panel.tabs.count()):
                tab = panel.tabs.widget(index)
                worker = tab._size_worker
                tab._stop_size_worker()
                if worker is not None:
                    worker.wait()
        reopened._state_file = None
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(reopened, QEvent.DeferredDelete)


def test_missing_folder_does_not_replace_either_panel(window, tmp_path, monkeypatch):
    _app, win, _left, _right = window
    snapshot = {"panels": [p.to_state() for p in win.panels], "active_panel": 0}
    before = [p.current_path() for p in win.panels]
    snapshot["panels"][1]["tabs"][0]["path"] = str(tmp_path / "missing")
    errors = []
    monkeypatch.setattr(win, "_error", lambda error: errors.append(str(error)))
    _restore_dialog(monkeypatch, snapshot)
    win.open_workspaces()
    assert [p.current_path() for p in win.panels] == before
    assert errors


def test_cancel_restore_preserves_existing_tabs(window, monkeypatch):
    _app, win, left, _right = window
    panel = win.panels[0]
    panel.new_tab(str(left))
    panel.set_tab_locked(0, True)
    snapshot = {"panels": [p.to_state() for p in win.panels], "active_panel": 0}
    _restore_dialog(monkeypatch, snapshot)
    monkeypatch.setattr(win, "_ask", lambda *_: False)
    existing = [panel.tabs.widget(i) for i in range(panel.tabs.count())]
    win.open_workspaces()
    assert [panel.tabs.widget(i) for i in range(panel.tabs.count())] == existing
    assert panel.tabs.widget(0).locked


def test_busy_restore_preserves_existing_panels(window, monkeypatch):
    _app, win, _left, _right = window
    snapshot = {"panels": [p.to_state() for p in win.panels], "active_panel": 0}
    before = [p._tab for p in win.panels]
    _restore_dialog(monkeypatch, snapshot)
    monkeypatch.setattr(win, "busy", lambda: True)
    win.open_workspaces()
    assert [p._tab for p in win.panels] == before


def test_job_started_while_workspace_dialog_open_blocks_restore(window, monkeypatch):
    _app, win, _left, _right = window
    snapshot = {"panels": [p.to_state() for p in win.panels], "active_panel": 0}
    before = [p._tab for p in win.panels]
    _restore_dialog(monkeypatch, snapshot)
    checks = iter((False, True))
    monkeypatch.setattr(win, "busy", lambda: next(checks))
    win.open_workspaces()
    assert [p._tab for p in win.panels] == before


def test_remote_tab_blocks_workspace_dialog(window, monkeypatch):
    _app, win, _left, _right = window
    remote_tab = win.panels[1]._tab
    original = type(remote_tab).in_remote.fget
    monkeypatch.setattr(type(remote_tab), "in_remote", property(
        lambda tab: tab is remote_tab or original(tab)))

    def should_not_open(_dialog):
        pytest.fail("remote tabs cannot be restored as local workspace tabs")

    monkeypatch.setattr(WorkspaceDialog, "exec", should_not_open)
    win.open_workspaces()


def test_missing_archive_does_not_replace_either_panel(window, tmp_path, monkeypatch):
    _app, win, _left, _right = window
    snapshot = {"panels": [p.to_state() for p in win.panels], "active_panel": 0}
    before = [p._tab for p in win.panels]
    snapshot["panels"][1]["tabs"][0]["archive"] = str(tmp_path / "missing.zip")
    errors = []
    monkeypatch.setattr(win, "_error", lambda error: errors.append(str(error)))
    _restore_dialog(monkeypatch, snapshot)
    win.open_workspaces()
    assert [p._tab for p in win.panels] == before
    assert errors
