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
    assert {"폴더 비교 (다른 항목 선택)", "폴더 동기화...", "파일 내용 비교", "체크섬 계산...",
            "체크섬 검증"} <= {x.text() for x in win.tools_menu.actions()}
    _select_file(win)
    win.actions_by_name["checksum"].trigger()
    _idle(win)
    title, text, dlg = shown[-1]
    assert title == "SHA-256 체크섬"
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
    assert title == "검증 - f.txt.sha256"
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


def _put(path, text, mtime):
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


OLD, NEW = 1_600_000_000, 1_700_000_000


def _sync_tree(a, b):
    (a / "f.txt").unlink()
    _put(a / "same.txt", "s", OLD)
    _put(b / "same.txt", "s", OLD)
    _put(a / "newer.txt", "new", NEW)
    _put(b / "newer.txt", "o", OLD)
    _put(a / "clash.txt", "aa", OLD)
    _put(b / "clash.txt", "bbb", OLD)
    _put(a / "only_a.txt", "a", OLD)
    _put(b / "sub" / "only_b.txt", "b", OLD)


def test_compare_dirs_selects_differences_in_both_panels(tmp_path, monkeypatch):
    win, a, b = _win(tmp_path, monkeypatch)
    _sync_tree(a, b)
    left, right = win.panels
    _wait(lambda: {"clash.txt", "only_a.txt"} <= _names(left) and "sub" in _names(right))
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: infos.append(args[2]))
    win.actions_by_name["compare_dirs"].trigger()
    assert sorted(p.name for p in left.selected_paths()) == ["clash.txt", "newer.txt", "only_a.txt"]
    assert sorted(p.name for p in right.selected_paths()) == ["clash.txt", "sub"]
    assert infos == []

    right.set_path(str(a))  # same folder on both sides: nothing differs
    _wait(lambda: "only_a.txt" in _names(right))
    win.compare_dirs()
    assert left.selected_paths() == [] and infos == ["두 폴더의 내용이 같습니다."]


def test_sync_dialog_plan_review_and_apply(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    from commander import sync
    from commander.sync_dialog import SyncDialog

    win, a, b = _win(tmp_path, monkeypatch)
    _sync_tree(a, b)
    dialogs = []

    def drive(self):
        dialogs.append(self)
        self.start_scan()
        _wait(lambda: self._scan is None and self.items)
        rows = {self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i)
                for i in range(self.tree.topLevelItemCount())}
        assert sorted(rows) == ["clash.txt", "newer.txt", "only_a.txt", "sub"]  # equal rows hidden
        assert rows["newer.txt"].text(2) == "→" and rows["sub"].text(2) == "←"
        assert rows["clash.txt"].text(2) == "≠ 충돌"
        assert rows["clash.txt"].data(0, Qt.CheckStateRole) is None
        assert self.summary.text().split() == ["→", "2", "←", "1", "삭제", "0", "충돌", "1", "같음", "1"]

        self.tree.clearSelection()
        rows["clash.txt"].setSelected(True)
        self.set_action(sync.TO_LEFT)  # resolve the conflict by hand
        rows["only_a.txt"].setCheckState(0, Qt.Unchecked)  # and leave one item out
        rows["sub"].setSelected(True)
        rows["clash.txt"].setSelected(False)
        self.set_action(sync.TO_RIGHT)  # impossible: the left side has no 'sub' -> unchanged
        assert rows["sub"].text(2) == "←"
        assert self.sync_btn.isEnabled()
        self.show_equal.setChecked(True)
        assert self.tree.topLevelItemCount() == 5
        self._confirm()
        return self.result()

    monkeypatch.setattr(SyncDialog, "exec", drive)
    win.actions_by_name["sync"].trigger()
    _idle(win)
    dlg = dialogs[0]
    # toggling 'show equal' rebuilt the rows; the manual action and the exclusion both persist
    assert {i.rel: i.action for i in dlg.plan} == {
        "clash.txt": sync.TO_LEFT, "newer.txt": sync.TO_RIGHT, "sub": sync.TO_LEFT}
    assert not (b / "only_a.txt").exists()
    assert (b / "newer.txt").read_text() == "new"
    assert (a / "clash.txt").read_text() == "bbb"
    assert (a / "sub" / "only_b.txt").read_text() == "b"


def test_sync_dialog_mirror_options_and_cancel(tmp_path, monkeypatch):
    from commander import sync
    from commander.sync_dialog import SyncDialog

    win, a, b = _win(tmp_path, monkeypatch)
    _sync_tree(a, b)
    dlg = SyncDialog(a, b, include_hidden=False, parent=win)
    assert not dlg.delete_extra.isEnabled()
    dlg.mode.setCurrentIndex(1)  # left -> right
    assert dlg.delete_extra.isEnabled()
    dlg.delete_extra.setChecked(True)
    dlg.start_scan()
    _wait(lambda: dlg._scan is None and dlg.items)
    plan = {i.rel: i.action for i in dlg.checked_items()}
    assert plan == {"clash.txt": sync.TO_RIGHT, "newer.txt": sync.TO_RIGHT,
                    "only_a.txt": sync.TO_RIGHT, "sub": sync.DEL_RIGHT}
    assert "삭제 1" in dlg.summary.text()
    dlg.mode.setCurrentIndex(0)
    assert not dlg.delete_extra.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    dlg._confirm()
    assert dlg.plan == [] and dlg.result() == 0
    dlg.start_scan()
    dlg.reject()  # closing during a scan stops the thread first
    assert dlg._scan is None

    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    win.panels[1].set_path(str(a))
    win.sync_dirs()  # a folder cannot be synchronized with itself
    assert len(errors) == 1 and not win.busy()


def test_diff_files_choices_and_dialog(tmp_path, monkeypatch):
    from commander.diff_dialog import DiffDialog

    win, a, b = _win(tmp_path, monkeypatch)
    (a / "f.txt").write_text("one\ntwo\nthree\nfour\n")
    (b / "f.txt").write_text("one\nTWO\nthree\nfour\nfive\n")
    (a / "bin.dat").write_bytes(b"\0\1")
    (b / "bin.dat").write_bytes(b"\0\2")
    (a / "copy.txt").write_text("one\ntwo\nthree\nfour\n")
    left = win.panels[0]
    _wait(lambda: {"bin.dat", "copy.txt"} <= _names(left))
    shown, infos, errors = [], [], []
    monkeypatch.setattr(DiffDialog, "exec", lambda self: shown.append(self))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: infos.append(args[2]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))

    _select_file(win)  # f.txt against the same name in the other panel
    win.actions_by_name["diff"].trigger()
    dlg = shown[-1]
    assert dlg.windowTitle() == "비교 - f.txt ↔ f.txt" and len(dlg.blocks) == 2
    left_text, right_text = (p.toPlainText().split("\n") for p in dlg.panes)
    assert left_text == ["1  one", "2  two", "3  three", "4  four", ""]
    assert right_text == ["1  one", "2  TWO", "3  three", "4  four", "5  five"]
    assert [len(p.extraSelections()) for p in dlg.panes] == [2, 2]
    assert dlg.summary.text() == "차이 1 / 2"
    dlg.go(1)
    assert dlg.summary.text() == "차이 2 / 2" and dlg.panes[0].textCursor().blockNumber() == 4
    dlg.go(1)
    assert dlg.summary.text() == "차이 1 / 2"

    left.select_names({"f.txt", "copy.txt"})  # two files in one panel, identical
    win.diff_files()
    assert infos == ["두 파일의 내용이 같습니다."] and len(shown) == 1
    left.select_names({"bin.dat"})
    win.diff_files()
    assert len(infos) == 2 and "다릅니다" in infos[-1] and len(shown) == 1
    left.select_names({"copy.txt"})  # no counterpart in the other panel
    win.diff_files()
    assert len(errors) == 1


def test_pack_selection_into_other_panel(tmp_path, monkeypatch):
    from commander import archive

    win, a, b = _win(tmp_path, monkeypatch)
    (a / "dir").mkdir()
    (a / "dir" / "in.txt").write_text("inside")
    left = win.panels[0]
    _wait(lambda: "dir" in _names(left))
    left.select_names({"f.txt", "dir"})
    prompts = []

    def get_text(*args):
        prompts.append(args[4])
        return ("bundle.tar.gz", True)

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    win.actions_by_name["pack"].trigger()
    _idle(win)
    assert prompts == ["a.zip"]  # several items: named after the folder
    assert {e.path for e in archive.read_entries(b / "bundle.tar.gz")} == {"dir", "dir/in.txt", "f.txt"}

    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("bundle.rar", True))
    win.pack()
    assert len(errors) == 1 and not win.busy()

    # The new archive can be browsed right away, and an existing name asks before overwriting
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("bundle.tar.gz", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    before = (b / "bundle.tar.gz").stat().st_mtime_ns
    win.pack()
    assert not win.busy() and (b / "bundle.tar.gz").stat().st_mtime_ns == before
    win.panels[1].set_path(str(b / "bundle.tar.gz"))
    assert win.panels[1].in_archive


def test_sync_failed_rescan_cannot_reuse_previous_plan(tmp_path, monkeypatch):
    from commander import sync
    from commander.sync_dialog import SyncDialog

    win, left, right = _win(tmp_path, monkeypatch)
    _sync_tree(left, right)
    dlg = SyncDialog(left, right, parent=win)
    dlg.start_scan()
    _wait(lambda: dlg._scan is None and dlg.items)
    assert dlg.sync_btn.isEnabled()

    def fail_scan(**_kwargs):
        raise PermissionError("scan denied")

    monkeypatch.setattr(sync, "scan", fail_scan)
    dlg.start_scan()
    dlg.show_equal.setChecked(True)
    assert not dlg.sync_btn.isEnabled()
    _wait(lambda: dlg._scan is None)
    assert "PermissionError: scan denied" in dlg.summary.text()
    assert not dlg.sync_btn.isEnabled()
    assert dlg.checked_items() == []
    assert dlg.plan == []
    dlg.close()
