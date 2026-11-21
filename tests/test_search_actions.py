"""Search result actions use the ordinary, confirmed background file operations."""
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QInputDialog
from test_smoke import _idle, _setup, _wait

from commander import ops, transfer
from commander.search_dialog import SearchDialog


@pytest.fixture
def window(tmp_path, monkeypatch):
    app, win, source, destination = _setup(tmp_path, monkeypatch)
    yield app, win, source, destination
    # Dispose of this test's window while QApplication and its workers still exist.
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


def _choose_results(monkeypatch, paths, action):
    answers = iter([("*", True), ("", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: next(answers))

    def execute(dialog):
        wanted = {str(path) for path in paths}
        _wait(lambda: dialog.worker.isFinished() and wanted <= {
            dialog.results.item(i).text() for i in range(dialog.results.count())})
        for i in range(dialog.results.count()):
            item = dialog.results.item(i)
            item.setSelected(item.text() in wanted)
        getattr(dialog, f"{action}_btn").click()
        assert not dialog.worker.isRunning()

    monkeypatch.setattr(SearchDialog, "exec", execute)


@pytest.mark.parametrize("action", ["copy", "move", "trash"])
def test_search_actions_handle_nested_selection_once(window, monkeypatch, action):
    _app, win, source, destination = window
    win.active_index = lambda: 0
    folder = source / "folder"
    folder.mkdir()
    child = folder / "child.txt"
    child.write_text("nested")
    another = source / "other.txt"
    another.write_text("other")
    _choose_results(monkeypatch, [folder, child, another], action)

    seen = []
    original_transfer = transfer.transfer
    original_trash = ops.trash

    def transfer_spy(paths, *args, **kwargs):
        seen.extend(map(Path, paths))
        return original_transfer(paths, *args, **kwargs)

    def trash_spy(paths, *args, **kwargs):
        seen.extend(map(Path, paths))
        return original_trash(paths, *args, **kwargs)

    monkeypatch.setattr(transfer, "transfer", transfer_spy)
    monkeypatch.setattr(ops, "trash", trash_spy)
    win.search()
    _idle(win)

    assert set(seen) == {folder, another}
    assert len(seen) == 2
    if action == "trash":
        assert not folder.exists() and not another.exists()
    else:
        assert (destination / "folder" / "child.txt").read_text() == "nested"
        assert (destination / "other.txt").read_text() == "other"
        assert folder.exists() == (action == "copy")


@pytest.mark.parametrize("action", ["copy", "move", "trash"])
def test_search_action_cancel_keeps_files(window, monkeypatch, action):
    _app, win, source, destination = window
    win.active_index = lambda: 0
    path = source / "f.txt"
    _choose_results(monkeypatch, [path], action)
    monkeypatch.setattr(win, "_ask", lambda *_: False)

    win.search()

    assert not win.jobs
    assert path.read_text() == "x"
    assert not (destination / path.name).exists()


def test_search_copy_rejects_remote_destination(window, monkeypatch):
    _app, win, source, destination = window
    win.active_index = lambda: 0
    _choose_results(monkeypatch, [source / "f.txt"], "copy")
    remote_panel = win.panels[1]
    panel_type = type(remote_panel)
    original = panel_type.in_remote.fget
    monkeypatch.setattr(panel_type, "in_remote", property(
        lambda panel: panel is remote_panel or original(panel)))
    errors = []
    monkeypatch.setattr(win, "_error", lambda error: errors.append(str(error)))

    win.search()

    assert errors
    assert not win.jobs
    assert (source / "f.txt").exists()
    assert not (destination / "f.txt").exists()


def test_search_copy_mixed_sources_duplicates_destination_file(window, monkeypatch):
    _app, win, source, destination = window
    tmp_path = source.parent
    win.active_index = lambda: 0
    local = destination / "local.txt"
    local.write_text("local")
    other = source / "f.txt"
    win.panels[0].set_path(str(tmp_path))
    _choose_results(monkeypatch, [local, other], "copy")
    win.search()
    _idle(win)
    assert local.read_text() == "local"
    assert (destination / "local (2).txt").read_text() == "local"
    assert (destination / "f.txt").read_text() == "x"
