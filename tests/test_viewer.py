import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from commander.viewer import MAX_BYTES, ViewerDialog, read_text


def test_read_text_modes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("héllo")
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
    dlg.editor.setPlainText("new")
    dlg.save()
    assert f.read_text() == "new"
    assert ViewerDialog(f, edit=False).editor.isReadOnly()
