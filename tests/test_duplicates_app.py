"""Duplicate finding is connected to confirmed, queued trash operations."""
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from test_smoke import _idle, _setup, _wait

from commander.duplicates_dialog import DuplicatesDialog


@pytest.fixture
def window(tmp_path, monkeypatch):
    app, win, source, destination = _setup(tmp_path, monkeypatch)
    yield app, win, source, destination
    for panel in win.panels:
        for index in range(panel.tabs.count()):
            tab = panel.tabs.widget(index)
            worker = tab._size_worker
            tab._stop_size_worker()
            if worker is not None:
                worker.wait()
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(win, QEvent.DeferredDelete)
    QApplication.processEvents()


def _choose_duplicate(monkeypatch, path, after_scan=None):
    def execute(dialog):
        _wait(lambda: dialog.result_data is not None)
        for index in range(dialog.tree.topLevelItemCount()):
            group = dialog.tree.topLevelItem(index)
            for row in range(group.childCount()):
                item = group.child(row)
                if item.text(0) == str(path):
                    item.setSelected(True)
        assert dialog.trash_btn.isEnabled()
        if after_scan:
            after_scan()
        dialog.trash_btn.click()
        assert dialog.selected_paths == [path]
        return dialog.result()

    monkeypatch.setattr(DuplicatesDialog, "exec", execute)


def test_duplicate_trash_runs_in_queue_and_preserves_other_copy(window, monkeypatch):
    _app, win, source, _destination = window
    win.active_index = lambda: 0
    original = source / "f.txt"
    duplicate = source / "copy.txt"
    duplicate.write_text("x")
    _choose_duplicate(monkeypatch, duplicate)
    win.find_duplicates()
    _idle(win)
    assert original.read_text() == "x"
    assert not duplicate.exists()


def test_duplicate_trash_cancel_keeps_both_files(window, monkeypatch):
    _app, win, source, _destination = window
    win.active_index = lambda: 0
    duplicate = source / "copy.txt"
    duplicate.write_text("x")
    _choose_duplicate(monkeypatch, duplicate)
    monkeypatch.setattr(win, "_ask", lambda *_: False)
    win.find_duplicates()
    assert not win.jobs
    assert duplicate.read_text() == "x"
    assert (source / "f.txt").read_text() == "x"


@pytest.mark.parametrize("change_survivor", [False, True])
def test_duplicate_changed_after_scan_is_not_trashed(window, monkeypatch, change_survivor):
    _app, win, source, _destination = window
    win.active_index = lambda: 0
    duplicate = source / "copy.txt"
    duplicate.write_text("x")
    changed = source / "f.txt" if change_survivor else duplicate
    _choose_duplicate(monkeypatch, duplicate, lambda: changed.write_text("changed"))
    win.find_duplicates()
    _idle(win)
    assert duplicate.exists()
    assert changed.read_text() == "changed"


def test_duplicate_changed_while_queued_is_not_trashed(window, monkeypatch):
    _app, win, source, _destination = window
    win.active_index = lambda: 0
    duplicate = source / "copy.txt"
    duplicate.write_text("x")
    blocker_started = threading.Event()
    release = threading.Event()

    def block(job):
        blocker_started.set()
        while not release.wait(0.01):
            job.control.checkpoint()

    win.start_job("blocker", block)
    _wait(blocker_started.is_set)
    _choose_duplicate(monkeypatch, duplicate)
    win.find_duplicates()
    assert len(win.jobs) == 2
    duplicate.write_text("changed")
    release.set()
    _idle(win)
    assert duplicate.read_text() == "changed"
    assert (source / "f.txt").read_text() == "x"


@pytest.mark.parametrize("mode", ["in_remote", "in_archive"])
def test_duplicate_search_rejects_nonlocal_panel(window, monkeypatch, mode):
    _app, win, _source, _destination = window
    win.active_index = lambda: 0
    panel = win.panels[0]
    panel_type = type(panel)
    original = getattr(panel_type, mode).fget
    monkeypatch.setattr(panel_type, mode, property(
        lambda value: value is panel or original(value)))
    errors = []
    monkeypatch.setattr(win, "_error", lambda error: errors.append(str(error)))
    monkeypatch.setattr(DuplicatesDialog, "exec", lambda _dialog: pytest.fail("unexpected scan"))
    win.find_duplicates()
    assert errors
    assert not win.jobs
