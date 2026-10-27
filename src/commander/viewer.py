from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit, QVBoxLayout

MAX_BYTES = 1_000_000


def read_text(path: Path, strict: bool) -> str:
    """Read up to MAX_BYTES. strict (editing) refuses truncated or non-UTF-8 files."""
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if strict:
        if len(data) > MAX_BYTES:
            raise ValueError("File too large to edit")
        return data.decode("utf-8")  # UnicodeDecodeError is a ValueError
    return data[:MAX_BYTES].decode("utf-8", errors="replace")


class ViewerDialog(QDialog):
    def __init__(self, path: Path, edit: bool, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"{'Edit' if edit else 'View'} - {path.name}")
        self.resize(800, 600)
        self.editor = QPlainTextEdit(read_text(path, strict=edit))
        self.editor.setReadOnly(not edit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)
        if edit:
            QShortcut(QKeySequence.Save, self, activated=self.save)

    def save(self) -> None:
        try:
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")
            return
        self.editor.document().setModified(False)
