"""Offscreen UI tests for compare / sync / diff / checksum commands."""
import hashlib

from PySide6.QtWidgets import QInputDialog, QMessageBox
from test_smoke import _idle, _names, _select_file, _setup, _wait

from commander.dialogs import TextDialog


def _win(tmp_path, monkeypatch):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    return win, a, b


def _capture_text_dialogs(monkeypatch):
    shown = []
    monkeypatch.setattr(
        TextDialog, "exec",
        lambda self: shown.append((self.windowTitle(), self.view.toPlainText(), self)))
    return shown


def test_checksum_compute_then_verify(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    shown = _capture_text_dialogs(monkeypatch)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_: ("SHA-256", True))
    assert [x.text() for x in win.tools_menu.actions()][:2] == ["체크섬 계산...", "체크섬 검증"]
    _select_file(win)
    win.actions_by_name["checksum"].trigger()
    _idle(win)
    title, text, dlg = shown[-1]
    assert title == "SHA-256 checksums"
    assert text == f"{hashlib.sha256(b'x').hexdigest()} *f.txt\n"
    assert dlg._save_as == a / "f.txt.sha256"

    (a / "f.txt.sha256").write_text(text + f"{'0' * 64} *missing.txt\n")
    p = win.panels[0]
    _wait(lambda: "f.txt.sha256" in _names(p))
    win.actions_by_name["verify"].trigger()  # f.txt is selected: not a checksum list
    assert len(shown) == 1 and not win.busy()
    p.view.clearSelection()
    p.select_pattern("*.sha256")
    win.actions_by_name["verify"].trigger()
    _idle(win)
    title, text, dlg = shown[-1]
    assert title == "Verify f.txt.sha256"
    assert text.split() == ["OK", "f.txt", "MISSING", "missing.txt"]


def test_text_dialog_copy_and_save(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog

    win, a, _b = _win(tmp_path, monkeypatch)
    dlg = TextDialog("t", "abc\n", win, summary="sum", save_as=a / "out.txt")
    dlg.copy()
    assert QApplication.clipboard().text() == "abc\n"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(a / "out.txt"), ""))
    dlg.save()
    assert (a / "out.txt").read_text() == "abc\n"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: ("", ""))
    dlg.save()  # cancelled: nothing happens


def test_local_only_commands_are_refused_in_archives(tmp_path, monkeypatch):
    import zipfile

    win, a, _b = _win(tmp_path, monkeypatch)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    with zipfile.ZipFile(a / "z.zip", "w") as z:
        z.writestr("in.txt", "hi")
    win.panels[0].set_path(str(a / "z.zip"))
    win.panels[0].select_pattern("*")
    win.checksum()
    win.verify_checksum()
    assert len(errors) == 2 and not win.busy()
