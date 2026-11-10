"""Small shared dialogs."""
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)


class TextDialog(QDialog):
    """Read-only monospace text with Copy and (optionally) Save."""

    def __init__(self, title: str, text: str, parent=None, summary: str = "",
                 save_as: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 420)
        self._save_as = save_as
        self.view = QPlainTextEdit(text)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton("Copy", QDialogButtonBox.ActionRole).clicked.connect(self.copy)
        if save_as is not None:
            buttons.addButton("Save...", QDialogButtonBox.ActionRole).clicked.connect(self.save)
        layout = QVBoxLayout(self)
        if summary:
            layout.addWidget(QLabel(summary))
        layout.addWidget(self.view)
        layout.addWidget(buttons)

    def copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())

    def save(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "Save", str(self._save_as))
        if not name:
            return
        try:
            Path(name).write_text(self.view.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")
