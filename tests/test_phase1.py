"""Offscreen UI tests for everyday-operation features (trash, conflicts, error summary)."""
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
