"""Offscreen UI tests for everyday-operation features (trash, conflicts, error summary)."""
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
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

    monkeypatch.setattr(MainWindow, "_ask_conflict", lambda *_: None)  # cancel stops the job
    win.transfer(move=False)
    _idle(win)
    assert (b / "f.txt").read_text() == "old"

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

    def ask(self, name, remaining, allow_rename, detail=""):
        asked.append((remaining, allow_rename))
        assert "기존 항목: 3 B" in detail and "새 항목: 3 B" in detail
        return ("skip", True)

    monkeypatch.setattr(MainWindow, "_ask_conflict", ask)
    win.transfer(move=False)
    _idle(win)
    assert asked == [(None, True)]  # asked when the job reaches the first conflict
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
    assert len(warnings) == 1 and warnings[0].startswith("1개 항목이 실패했습니다") and "f.txt" in warnings[0]


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
            if label is None:
                box.reject()  # Esc / Cancel
            else:
                next(b for b in box.buttons() if b.text() == label).click()
            seen.append(texts)
        QTimer.singleShot(0, act)

    seen = []
    press("둘 다 유지", True)
    assert win._ask_conflict("f.txt", 2, True) == ("rename", True)
    assert {"덮어쓰기", "건너뛰기", "둘 다 유지"} <= set(seen[-1])
    press("건너뛰기", False)
    assert win._ask_conflict("f.txt", 0, False) == ("skip", False)
    assert "둘 다 유지" not in seen[-1]
    press(None, False)
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
    assert p.status_bar.text().startswith("0 / 2개 선택")
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


def test_folder_shortcuts_button_add_go_remove_and_restore(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog

    app = QApplication.instance() or QApplication([])
    d = _nav_tree(tmp_path)
    state = tmp_path / "state.json"
    win = MainWindow(state_file=state)
    left, right = win.panels
    left.set_path(str(d))
    right.set_path(str(d / "docs"))
    assert left.favorites_button.text() == right.favorites_button.text() == "+"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(d))
    win._folder_shortcuts_menu(right).actions()[0].trigger()
    assert win.favorites == [str(d)]
    win.active_index = lambda: 0
    win._folder_shortcuts_menu(right).actions()[2].trigger()
    assert right.current_path() == str(d)
    assert left.current_path() == str(d)

    restored = MainWindow(state_file=state)
    assert restored.favorites == [str(d)]
    remove = win._folder_shortcuts_menu(left).actions()[-1].menu()
    remove.actions()[0].trigger()
    assert win.favorites == []
    assert MainWindow(state_file=state).favorites == []
    win.close()
    restored.close()
    app.processEvents()


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


def _row(panel, name):
    m = panel.view.model()
    root = panel.view.rootIndex()
    return m.index(_view_rows(panel).index(name), 0, root)


def _labels(menu):
    return [a.text().split("\t")[0] for a in menu.actions() if not a.isSeparator()]


def test_enter_opens_file_with_default_app(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtTest import QTest

    win, a, _b = _win(tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    _select_file(win)
    QTest.keyClick(win.panels[0].view, Qt.Key_Return)
    assert [Path(x) for x in opened] == [a / "f.txt"]  # Qt reports forward slashes on Windows
    assert win.panels[0].current_path() == str(a)


def test_context_menu_follows_panel_mode(tmp_path, monkeypatch):
    from commander import shell

    win, a, b = _win(tmp_path, monkeypatch)
    d = _nav_tree(tmp_path)
    p = win.panels[0]
    p.view.clearSelection()
    empty = _labels(win._context_menu(p))
    assert "복사" not in empty and {"경로 복사", "여기서 터미널 열기", "새 폴더"} <= set(empty)

    _select_file(win)
    labels = _labels(win._context_menu(p))
    assert labels[:3] == ["열기", "보기", "편집"]
    assert {"복사", "이동", "이름 변경", "휴지통으로 이동", "영구 삭제"} <= set(labels)

    launched = []
    monkeypatch.setattr(shell, "launch", lambda argv, cwd=None: launched.append((argv, cwd)))
    menu = win._context_menu(p)
    for a_ in menu.actions():
        if a_.text() in ("여기서 터미널 열기", "경로 복사\tCtrl+Shift+C") or "에서 보기" in a_.text():
            a_.trigger()
    assert QApplication.clipboard().text() == str(a / "f.txt")
    assert len(launched) == 2 and launched[1][1] == a
    assert str(a / "f.txt") in " ".join(launched[0][0]) or str(a) in launched[0][0]
    next(x for x in menu.actions() if x.text().startswith("복사")).trigger()
    _idle(win)
    assert (b / "f.txt").exists()

    p.set_path(str(d / "z.zip"))
    p.select_pattern("one.txt")
    arch = _labels(win._context_menu(p))
    assert arch == ["추출", "경로 복사"]
    win.copy_path()
    assert QApplication.clipboard().text() == f"{d / 'z.zip'}:/one.txt"


def test_right_click_selects_row_under_cursor(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    p = win.panels[0]
    seen = []
    monkeypatch.setattr(MainWindow, "show_context_menu", lambda self, panel, pos: seen.append(panel))
    p.view.clearSelection()
    p.view.customContextMenuRequested.emit(p.view.visualRect(_row(p, "f.txt")).center())
    assert [x.name for x in p.selected_paths()] == ["f.txt"] and seen == [p]


def _drop(view, paths, pos, move=False):
    """Deliver a drop offering copy+move; Shift makes move the proposed action, as in a real drag."""
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(x)) for x in paths])
    ev = QDropEvent(QPointF(pos), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton,
                    Qt.ShiftModifier if move else Qt.NoModifier)
    assert (ev.proposedAction() == Qt.MoveAction) == move
    view.dropEvent(ev)
    return ev.isAccepted(), ev.dropAction()  # the event must not outlive its mime data


def test_drag_and_drop_copies_and_moves(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt

    win, a, b = _win(tmp_path, monkeypatch)
    (b / "sub").mkdir()
    dst = win.panels[1]
    _wait(lambda: "sub" in _view_rows(dst))
    below_rows = QPoint(5, dst.view.viewport().height() - 2)

    assert _drop(dst.view, [a / "f.txt"], below_rows) == (True, Qt.CopyAction)
    _wait(lambda: (b / "f.txt").exists())
    _idle(win)
    assert (a / "f.txt").exists()

    # Dropping on a folder row targets that folder; a move is still reported as a copy
    on_sub = dst.view.visualRect(_row(dst, "sub")).center()
    assert _drop(dst.view, [a / "f.txt"], on_sub, move=True) == (True, Qt.CopyAction)
    _wait(lambda: (b / "sub" / "f.txt").exists())
    _idle(win)
    assert not (a / "f.txt").exists()

    src = win.panels[0]
    (a / "g.txt").write_text("g")
    _wait(lambda: "g.txt" in _view_rows(src))
    assert src.model.flags(_row(src, "g.txt")) & Qt.ItemIsDragEnabled
    assert not src.model.flags(_row(src, "..")) & Qt.ItemIsDragEnabled

    # Dropping a file onto the folder it already lives in, or '..' itself, does nothing
    _drop(dst.view, [b / "f.txt", b / ".."], below_rows, move=True)
    QApplication.processEvents()
    assert not win.busy() and (b / "f.txt").exists()


def test_drop_is_rejected_in_archive_mode(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint

    win, a, _b = _win(tmp_path, monkeypatch)
    d = _nav_tree(tmp_path)
    dst = win.panels[1]
    dst.set_path(str(d / "z.zip"))
    accepted, _action = _drop(dst.view, [a / "f.txt"], QPoint(5, 5))
    QApplication.processEvents()
    assert not accepted and not win.busy() and (a / "f.txt").exists()


def test_shell_commands_per_platform(tmp_path):
    from commander import shell

    f = tmp_path / "f.txt"
    f.write_text("x")
    assert shell.reveal_command(f, "darwin") == ["open", "-R", str(f)]
    assert shell.reveal_command(f, "win32") == ["explorer", f"/select,{f}"]
    assert shell.reveal_command(f, "linux") == ["xdg-open", str(tmp_path)]
    assert shell.reveal_command(tmp_path, "linux") == ["xdg-open", str(tmp_path)]
    assert shell.terminal_command("darwin")[:3] == ["open", "-a", "Terminal"]
    assert shell.terminal_command("win32")[0] == "cmd.exe"


def test_conflict_dialog_always_offers_apply_to_all_when_count_unknown(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    win, _a, _b = _win(tmp_path, monkeypatch)
    monkeypatch.undo()
    seen = []

    def act():
        box = QApplication.activeModalWidget()
        seen.append((box.checkBox().text(), box.informativeText()))
        box.checkBox().setChecked(True)
        next(b for b in box.buttons() if b.text() == "덮어쓰기").click()

    QTimer.singleShot(0, act)
    assert win._ask_conflict("f.txt", None, True, "기존 항목: 1 B") == ("overwrite", True)
    assert seen == [("모두 적용", "기존 항목: 1 B")]
