from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit, QVBoxLayout

from .preview_widget import PreviewWidget

MAX_BYTES = 1_000_000


def read_text(path: Path, strict: bool) -> str:
    """Read up to MAX_BYTES. strict (editing) refuses truncated or non-UTF-8 files."""
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if strict:
        if len(data) > MAX_BYTES:
            raise ValueError("파일이 너무 커서 편집할 수 없습니다 (1MB 초과)")
        return data.decode("utf-8")  # UnicodeDecodeError is a ValueError
    return data[:MAX_BYTES].decode("utf-8", errors="replace")


class ViewerDialog(QDialog):
    """F3: paged viewer for files of any size (text, hex, image). F4: small UTF-8 text editor."""

    def __init__(self, path: Path, edit: bool, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"{'편집' if edit else '보기'} - {path.name}")
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview: PreviewWidget | None = None
        if edit:
            self.editor = QPlainTextEdit(read_text(path, strict=True))
            layout.addWidget(self.editor)
            QShortcut(QKeySequence.Save, self, activated=self.save)
        else:
            self.preview = PreviewWidget()
            self.preview.show_path(path)
            self.editor = self.preview.text
            layout.addWidget(self.preview)
            # F3 toggles hex inside the viewer (H would collide with text search-as-you-type)
            QShortcut(QKeySequence("F3"), self, activated=self.preview.toggle_hex)
            self.setWindowTitle(f"보기 - {path.name}  (F3: hex 전환)")

    def save(self) -> None:
        try:
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "오류", f"{type(e).__name__}: {e}")
            return
        self.editor.document().setModified(False)

    def done(self, result: int) -> None:
        if self.preview is not None:
            self.preview.show_message("")  # closes the open file
        super().done(result)
