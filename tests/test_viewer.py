import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from commander.viewer import MAX_BYTES, ViewerDialog, read_text


def test_read_text_modes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("héllo", encoding="utf-8")
    assert read_text(f, strict=True) == "héllo"
    b = tmp_path / "b.bin"
    b.write_bytes(b"\xff\xfe")
    assert read_text(b, strict=False)  # lossy view is fine
    with pytest.raises(ValueError):
        read_text(b, strict=True)
    big = tmp_path / "big"
    big.write_bytes(b"x" * (MAX_BYTES + 1))
    with pytest.raises(ValueError):
        read_text(big, strict=True)
    assert len(read_text(big, strict=False)) == MAX_BYTES


def test_edit_and_save(tmp_path):
    QApplication.instance() or QApplication([])
    f = tmp_path / "a.txt"
    f.write_text("old")
    dlg = ViewerDialog(f, edit=True)
    assert not dlg.editor.document().isModified()
    assert dlg.windowTitle() == "편집 - a.txt"
    dlg.editor.textCursor().insertText("new")
    assert dlg.editor.document().isModified()
    assert dlg.windowTitle() == "편집 - a.txt *"
    dlg.save()
    assert f.read_text() == "newold"
    assert not dlg.editor.document().isModified()
    assert dlg.windowTitle() == "편집 - a.txt"
    assert ViewerDialog(f, edit=False).editor.isReadOnly()


def test_edit_close_when_unmodified(tmp_path):
    QApplication.instance() or QApplication([])
    f = tmp_path / "a.txt"
    f.write_text("content")
    dlg = ViewerDialog(f, edit=True)
    # Closing an unmodified document does not show a message box
    assert dlg.close() is True


def test_edit_close_save_prompt(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    QApplication.instance() or QApplication([])
    f = tmp_path / "a.txt"
    f.write_text("old")

    def _choose_button(pattern):
        def _exec(box):
            for b in box.buttons():
                if pattern in b.text():
                    b.click()
                    return
        return _exec

    # 1. User clicks "저장(&S)"
    dlg = ViewerDialog(f, edit=True)
    dlg.editor.textCursor().insertText("saved: ")
    monkeypatch.setattr(QMessageBox, "exec", _choose_button("저장(&S)"))
    assert dlg.close() is True
    assert f.read_text() == "saved: old"

    # 2. User clicks "저장 안 함(&D)"
    dlg2 = ViewerDialog(f, edit=True)
    dlg2.editor.textCursor().insertText("discarded: ")
    monkeypatch.setattr(QMessageBox, "exec", _choose_button("저장 안 함"))
    assert dlg2.close() is True
    assert f.read_text() == "saved: old"

    # 3. User clicks "취소"
    dlg3 = ViewerDialog(f, edit=True)
    dlg3.editor.textCursor().insertText("cancelled: ")
    monkeypatch.setattr(QMessageBox, "exec", _choose_button("취소"))
    assert dlg3.close() is False
    assert f.read_text() == "saved: old"

    # 4. Reject / Esc key with Cancel keeps dialog open
    dlg3.reject()
    assert dlg3.editor.document().isModified()

    # 5. Save error keeps dialog open and does not close
    dlg4 = ViewerDialog(f, edit=True)
    dlg4.editor.textCursor().insertText("error content")
    monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **kw: None)
    monkeypatch.setattr(QMessageBox, "exec", _choose_button("저장(&S)"))
    assert dlg4.close() is False
    assert dlg4.editor.document().isModified()




def test_view_mode_close_without_prompt(tmp_path):
    QApplication.instance() or QApplication([])
    f = tmp_path / "a.txt"
    f.write_text("view only")
    dlg = ViewerDialog(f, edit=False)
    assert dlg.close() is True

