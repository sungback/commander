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
        self.edit = edit
        self._closing_confirmed = False
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview: PreviewWidget | None = None
        if edit:
            self.editor = QPlainTextEdit(read_text(path, strict=True))
            self.editor.document().setModified(False)
            self.editor.document().modificationChanged.connect(self._update_title)
            layout.addWidget(self.editor)
            QShortcut(QKeySequence.Save, self, activated=self.save)
        else:
            self.preview = PreviewWidget()
            self.preview.show_path(path)
            self.editor = self.preview.text
            layout.addWidget(self.preview)
            # F3 toggles hex inside the viewer (H would collide with text search-as-you-type)
            QShortcut(QKeySequence("F3"), self, activated=self.preview.toggle_hex)
        self._update_title()

    def _update_title(self, *_) -> None:
        if self.edit:
            mod = " *" if self.editor.document().isModified() else ""
            self.setWindowTitle(f"편집 - {self.path.name}{mod}")
        else:
            self.setWindowTitle(f"보기 - {self.path.name}  (F3: hex 전환)")

    def save(self) -> bool:
        try:
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "오류", f"{type(e).__name__}: {e}")
            return False
        self.editor.document().setModified(False)
        self._update_title()
        return True

    def _confirm_close(self) -> bool:
        """Confirm discarding unsaved changes; returns True if safe to close, False to abort."""
        if not self.edit or not self.editor.document().isModified() or self._closing_confirmed:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("저장 확인")
        box.setText(f"'{self.path.name}'의 변경 내용을 저장할까요?")
        save_btn = box.addButton("저장(&S)", QMessageBox.AcceptRole)
        discard_btn = box.addButton("저장 안 함(&D)", QMessageBox.DestructiveRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_btn:
            if self.save():
                self._closing_confirmed = True
                return True
            return False
        if clicked == discard_btn:
            self._closing_confirmed = True
            return True
        return False

    def closeEvent(self, event) -> None:
        if self._confirm_close():
            event.accept()
            super().closeEvent(event)
        else:
            event.ignore()

    def done(self, result: int) -> None:
        if not self._confirm_close():
            return
        if self.preview is not None:
            self.preview.show_message("")  # closes the open file
        super().done(result)
