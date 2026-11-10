"""Offscreen UI tests for everyday-operation features (trash, conflicts, error summary)."""
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from test_smoke import _idle, _names, _select_file, _setup, _wait

from commander import ops
from commander.app import MainWindow


def _win(tmp_path, monkeypatch):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0  # offscreen focus is unreliable
    return win, a, b


def test_conflict_skip_keep_both_and_cancel(tmp_path, monkeypatch):
    win, a, b = _win(tmp_path, monkeypatch)
    (b / "f.txt").write_text("old")
    _select_file(win)

    monkeypatch.setattr(MainWindow, "_ask_conflict", lambda *_: None)  # cancel
    win.transfer(move=False)
    assert win._worker is None and (b / "f.txt").read_text() == "old"

    monkeypatch.setattr(MainWindow, "_ask_conflict", lambda *_: ("skip", False))
    win.transfer(move=True)
    _idle(win)
    assert (b / "f.txt").read_text() == "old" and (a / "f.txt").exists()

    monkeypatch.setattr(MainWindow, "_ask_conflict", lambda *_: ("rename", False))
    win.transfer(move=False)
    _idle(win)
    assert (b / "f.txt").read_text() == "old" and (b / "f (2).txt").read_text() == "x"


def test_conflict_apply_to_all_asks_once(tmp_path, monkeypatch):
    win, a, b = _win(tmp_path, monkeypatch)
    for name in ("f.txt", "g.txt", "h.txt"):
        (a / name).write_text("new")
        (b / name).write_text("old")
    (a / "only.txt").write_text("new")
    _wait(lambda: {"g.txt", "h.txt", "only.txt"} <= _names(win.panels[0]))
    win.panels[0].view.selectAll()
    asked = []

    def ask(self, name, remaining, allow_rename):
        asked.append((name, remaining, allow_rename))
        return ("skip", True)

    monkeypatch.setattr(MainWindow, "_ask_conflict", ask)
    win.transfer(move=False)
    _idle(win)
    assert len(asked) == 1 and asked[0][1:] == (2, True)
    assert [(b / n).read_text() for n in ("f.txt", "g.txt", "h.txt")] == ["old"] * 3
    assert (b / "only.txt").read_text() == "new"


def test_copy_onto_itself_reports_error_and_keeps_file(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    win.panels[1].set_path(str(a))  # both panels show the same folder
    _select_file(win)
    win.transfer(move=False)  # conflict answer is "overwrite"
    _idle(win)
    assert (a / "f.txt").read_text() == "x"
    assert len(warnings) == 1 and "f.txt" in warnings[0]


def test_delete_goes_to_trash_and_shift_deletes_permanently(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    (a / "g.txt").write_text("y")
    _wait(lambda: "g.txt" in _names(win.panels[0]))
    trashed = []

    def fake_trash(path):
        trashed.append(path.name)
        ops.remove(path)
        return True

    monkeypatch.setattr(ops, "_move_to_trash", fake_trash)
    _select_file(win)
    win.delete()
    _idle(win)
    assert trashed == ["f.txt"] and not (a / "f.txt").exists()

    _wait(lambda: "f.txt" not in _names(win.panels[0]))
    win.panels[0].view.selectAll()
    win.delete(permanent=True)
    _idle(win)
    assert trashed == ["f.txt"] and not (a / "g.txt").exists()


def test_failed_items_are_summarised_and_rest_continue(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    (a / "g.txt").write_text("y")
    _wait(lambda: "g.txt" in _names(win.panels[0]))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    def picky_trash(path):
        if path.name == "f.txt":
            return False
        ops.remove(path)
        return True

    monkeypatch.setattr(ops, "_move_to_trash", picky_trash)
    win.panels[0].view.selectAll()
    win.delete()
    _idle(win)
    assert (a / "f.txt").exists() and not (a / "g.txt").exists()
    assert len(warnings) == 1 and warnings[0].startswith("1 item(s) failed") and "f.txt" in warnings[0]


def test_conflict_dialog_buttons(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    win, _a, _b = _win(tmp_path, monkeypatch)
    monkeypatch.undo()  # use the real dialog

    def press(label, check_all):
        def act():
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox)
            texts = [b.text() for b in box.buttons()]
            if check_all:
                box.checkBox().setChecked(True)
            next(b for b in box.buttons() if b.text() == label).click()
            seen.append(texts)
        QTimer.singleShot(0, act)

    seen = []
    press("Keep both", True)
    assert win._ask_conflict("f.txt", 2, True) == ("rename", True)
    assert {"Overwrite", "Skip", "Keep both"} <= set(seen[-1])
    press("Skip", False)
    assert win._ask_conflict("f.txt", 0, False) == ("skip", False)
    assert "Keep both" not in seen[-1]
    press("Cancel", False)
    assert win._ask_conflict("f.txt", 0, False) is None


def _view_rows(panel):
    m = panel.view.model()
    root = panel.view.rootIndex()
    return [m.index(r, 0, root).data() for r in range(m.rowCount(root))]


def _nav_tree(tmp_path):
    import zipfile

    d = tmp_path / "nav"
    (d / "docs").mkdir(parents=True)
    for name in ("a.txt", "b.txt", "c.md"):
        (d / name).write_text(name)
    with zipfile.ZipFile(d / "z.zip", "w") as z:
        for name in ("one.txt", "two.md", "sub/x.txt"):
            z.writestr(name, "1")
    return d


def test_quick_filter_in_local_and_archive(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    d = _nav_tree(tmp_path)
    p = MainWindow().panels[0]
    p.set_path(str(d))
    _wait(lambda: len(_view_rows(p)) == 6)

    p.set_filter("*.txt")
    assert _view_rows(p) == ["..", "a.txt", "b.txt"]
    assert p.status_bar.text().startswith("0 / 2 selected")
    p.view.sortByColumn(0, Qt.DescendingOrder)
    assert _view_rows(p) == ["..", "b.txt", "a.txt"]
    p.view.sortByColumn(0, Qt.AscendingOrder)
    p.set_filter("DOC;.md")  # masks without wildcards are case-insensitive substrings
    assert _view_rows(p) == ["..", "docs", "c.md"]
    QTest.keyClick(p.view, Qt.Key_Escape)
    assert p.filter_text == "" and len(_view_rows(p)) == 6

    p.set_filter("docs")
    p.set_path(str(d / "docs"))  # navigating drops the filter
    assert p.filter_text == ""
    p.set_path(str(d))
    _wait(lambda: len(_view_rows(p)) == 6)

    p.set_path(str(d / "z.zip"))
    assert _view_rows(p) == ["..", "sub", "one.txt", "two.md"]
    p.set_filter("*.md")
    assert _view_rows(p) == ["..", "two.md"]
    p._tab.filter_bar.setText("sub")  # typing in the filter box
    assert _view_rows(p) == ["..", "sub"]
    p.view.setCurrentIndex(p.view.model().index(1, 0))
    QTest.keyClick(p.view, Qt.Key_Return)
    assert p.filter_text == "" and _view_rows(p) == ["..", "x.txt"]


def test_history_back_and_forward(tmp_path):
    import shutil

    d = _nav_tree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    p = MainWindow().panels[0]
    for path in (d, d / "docs", other):
        p.set_path(str(path))
    assert p.go_back() and p.current_path() == str(d / "docs")
    assert p.go_back() and p.current_path() == str(d)
    assert p.go_forward() and p.current_path() == str(d / "docs")
    p.set_path(str(d / "z.zip"))  # archives are left by going back to a local folder
    assert p.in_archive
    assert p.go_back() and not p.in_archive and p.current_path() == str(d)
    p.set_path(str(other))  # a new visit truncates the forward history
    assert not p.go_forward()
    shutil.rmtree(d)  # vanished folders are skipped: back lands on the initial (home) folder
    assert p.go_back() and Path(p.current_path()) == Path.home()
    assert not p.go_back()
    assert p.go_forward() and p.current_path() == str(other)


def test_favorites_menu_and_persistence(tmp_path, monkeypatch):
    d = _nav_tree(tmp_path)
    state = tmp_path / "state.json"
    win = MainWindow(state_file=state)
    win.active_index = lambda: 0
    win.panels[0].set_path(str(d))
    labels = [a.text() for a in win._favorites_menu().actions()]
    assert labels == ["현재 폴더를 즐겨찾기에 추가"]
    win._favorites_menu().actions()[0].trigger()
    win.add_favorite(str(d))  # duplicates are ignored
    assert win.favorites == [str(d)]

    win.panels[0].set_path(str(d / "docs"))
    win2 = MainWindow(state_file=state)  # favorites are saved as soon as they change
    assert win2.favorites == [str(d)]
    menu = win._favorites_menu()
    menu.actions()[0].trigger()
    assert win.panels[0].current_path() == str(d)
    assert win._favorites_menu().actions()[-1].text() == "현재 폴더를 즐겨찾기에서 제거"
    win._favorites_menu().actions()[-1].trigger()
    assert win.favorites == []

    win.panels[0].set_path(str(d / "z.zip"))  # only local folders can be added
    assert not win._favorites_menu().actions()[-1].isEnabled()


def test_select_by_pattern_and_invert(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog

    d = _nav_tree(tmp_path)
    p = MainWindow().panels[0]
    p.set_path(str(d))
    _wait(lambda: len(_view_rows(p)) == 6)

    def selected():
        return sorted(i.data() for i in p.view.selectionModel().selectedRows(0))

    p.view.clearSelection()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("*.txt", True))
    QTest.keyClick(p.view, Qt.Key_Plus)
    assert selected() == ["a.txt", "b.txt"]
    QTest.keyClick(p.view, Qt.Key_Asterisk)  # invert never touches '..'
    assert selected() == ["c.md", "docs", "z.zip"]
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("z", True))
    QTest.keyClick(p.view, Qt.Key_Minus)
    assert selected() == ["c.md", "docs"]
    assert sorted(x.name for x in p.selected_paths()) == selected()

    p.set_path(str(d / "z.zip"))
    assert p.select_pattern("*.txt;sub") == 2
    assert sorted(p.archive_selection()) == ["one.txt", "sub"]
    p.invert_selection()
    assert p.archive_selection() == ["two.md"]


def test_active_panel_follows_focus(tmp_path, monkeypatch):
    _app, win, _a, _b = _setup(tmp_path, monkeypatch)
    win._on_focus_changed(None, win.panels[1].view)
    assert win.active_index() == 1
    win._on_focus_changed(None, win.panels[1]._tab.filter_bar)  # filter box keeps its panel active
    assert win.active_index() == 1
    win._on_focus_changed(None, win.panels[0].path_bar)
    assert win.active_index() == 0
    win._on_focus_changed(None, None)
    assert win.active_index() == 0
