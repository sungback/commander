"""Preview of one file: paged text, hex dump or image. Used by F3 and Quick View."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontDatabase, QImageReader, QPixmap, QTextCursor
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget

from . import ops, preview

_MAX_IMAGE_SIDE = 4096  # larger images are decoded scaled down


def image_suffixes() -> set[str]:
    return {"." + bytes(f).decode("ascii").lower() for f in QImageReader.supportedImageFormats()}


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        self.mode = "info"  # "text" | "hex" | "image" | "info"
        self._reader: preview.TextReader | preview.HexReader | None = None
        self._pixmap: QPixmap | None = None
        self._images = image_suffixes()
        self.header = QLabel()
        self.header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(1, 1)
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setWordWrap(True)
        self.stack = QStackedWidget()
        for w in (self.text, self.image, self.info):
            self.stack.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.stack, 1)
        self.show_message("")

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def show_message(self, text: str, title: str = "") -> None:
        self._close_reader()
        self.path, self.mode, self._pixmap = None, "info", None
        self.header.setText(title)
        self.info.setText(text)
        self.stack.setCurrentWidget(self.info)

    def show_path(self, path: Path, hex_mode: bool = False) -> None:
        """Preview a local file or folder; picks image / text / hex from its content."""
        path = Path(path)
        self._close_reader()
        self.path, self._pixmap = path, None
        try:
            if path.is_dir():
                self._show_folder(path)
            elif not hex_mode and path.suffix.lower() in self._images and self._show_image(path):
                pass
            else:
                encoding = None if hex_mode else preview.sniff(path)
                self._show_content(path, encoding)
        except OSError as e:
            self.show_message(f"{type(e).__name__}: {e}", path.name)
            self.path = path

    def toggle_hex(self) -> None:
        """Switch a file between its natural view (text / image) and the hex dump."""
        if self.path is not None and self.path.is_file():
            self.show_path(self.path, hex_mode=self.mode != "hex")

    def _title(self, path: Path, what: str) -> None:
        self.header.setText(f"{path.name} — {ops.format_size(path.stat().st_size)} — {what}")

    def _show_folder(self, path: Path) -> None:
        count = sum(1 for _ in path.iterdir())
        size = ops.get_cached_size(path)
        extra = f"\n{ops.format_size(size)}" if size is not None else ""
        self.mode = "info"
        self.header.setText(path.name)
        self.info.setText(f"폴더\n항목 {count}개{extra}")
        self.stack.setCurrentWidget(self.info)

    def _show_image(self, path: Path) -> bool:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if not reader.canRead() or not size.isValid():
            return False
        if max(size.width(), size.height()) > _MAX_IMAGE_SIDE:
            reader.setScaledSize(size.scaled(QSize(_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE), Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return False
        self._pixmap = QPixmap.fromImage(img)
        self.mode = "image"
        self._title(path, f"이미지 {size.width()}×{size.height()}")
        self.stack.setCurrentWidget(self.image)
        self._fit_image()
        return True

    def _fit_image(self) -> None:
        if self._pixmap is None:
            return
        area = self.stack.size()
        pm = self._pixmap
        if pm.width() > area.width() or pm.height() > area.height():
            pm = pm.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image.setPixmap(pm)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_image()

    def _show_content(self, path: Path, encoding: str | None) -> None:
        if encoding:
            self._reader = preview.TextReader(path, encoding)
            self.mode = "text"
            self.text.setFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
            self._title(path, f"텍스트 ({encoding})")
        else:
            self._reader = preview.HexReader(path)
            self.mode = "hex"
            self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            self._title(path, "hex")
        self.text.clear()
        self.stack.setCurrentWidget(self.text)
        self.load_more()

    def load_more(self) -> bool:
        """Append the next piece of the file; False once everything is shown."""
        reader = self._reader
        if reader is None or reader.eof:
            return False
        chunk = reader.read()
        if not chunk:
            return False
        bar = self.text.verticalScrollBar()
        pos = bar.value()
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.End)
        if self.mode == "hex" and not self.text.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText(chunk)
        bar.setValue(pos)
        return True

    def _on_scroll(self, value: int) -> None:
        bar = self.text.verticalScrollBar()
        if value >= bar.maximum() - bar.pageStep():
            self.load_more()
