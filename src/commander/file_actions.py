"""Local file associations and printing, without changing OS file associations."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QFileDialog, QInputDialog

from . import preview, shell
from .settings import Settings

_TEXT_LIMIT = 10 * 1024 * 1024


def choose_program(parent) -> str:
    """Pick an executable or, on macOS, an application bundle."""
    selected, _ = QFileDialog.getOpenFileName(parent, "연결할 프로그램 선택")
    return selected


def open_with(path: Path, program: str, platform: str = sys.platform) -> None:
    """Open a single local path without using a shell or changing system settings."""
    path, app = Path(path), Path(program)
    if not path.is_file():
        raise ValueError("파일 하나를 선택하세요")
    if not program or not app.exists():
        raise ValueError("선택한 프로그램을 찾을 수 없습니다")
    if platform == "darwin" and app.suffix.lower() == ".app" and app.is_dir():
        argv = ["open", "-a", str(app), str(path)]
    elif app.is_file():
        argv = [str(app), str(path)]
    else:
        raise ValueError("실행 파일 또는 macOS 앱을 선택하세요")
    shell.launch(argv)


def open_associated(path: Path, associations: dict[str, str]) -> bool:
    """Open using Commander's association, or return False if none exists."""
    program = associations.get(Path(path).suffix.lower())
    if not program:
        return False
    open_with(path, program)
    return True


def edit_association(parent, path: Path, settings: Settings) -> bool:
    """Edit the selected extension's app association. Return whether it changed."""
    extension = Path(path).suffix.lower()
    if not extension:
        raise ValueError("확장자가 있는 파일을 선택하세요")
    current = settings.associations.get(extension)
    options = ["프로그램 선택"]
    if current:
        options.append("연결 제거")
    choice, ok = QInputDialog.getItem(
        parent, "내부 파일 연결", f"{extension} 연결: {current or '없음'}", options, 0, False)
    if not ok:
        return False
    if choice == "연결 제거":
        del settings.associations[extension]
        return True
    program = choose_program(parent)
    if not program:
        return False
    settings.associations[extension] = program
    return True


def print_file(parent, path: Path) -> bool:
    """Print one image or text file; False means the print dialog was cancelled."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("파일 하나를 선택하세요")
    image = QImage(str(path))
    text = None
    if image.isNull():
        if path.stat().st_size > _TEXT_LIMIT:
            raise ValueError("10MB를 넘는 텍스트 파일은 인쇄할 수 없습니다")
        encoding = preview.sniff(path)
        if encoding is None:
            raise ValueError("지원하지 않는 인쇄 형식입니다")
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeError as exc:
            raise ValueError("텍스트 인코딩을 읽을 수 없습니다") from exc
    printer = QPrinter(QPrinter.HighResolution)
    if QPrintDialog(printer, parent).exec() != QPrintDialog.Accepted:
        return False
    if text is not None:
        document = QTextDocument()
        document.setPlainText(text)
        document.print_(printer)
    else:
        painter = QPainter(printer)
        if not painter.isActive():
            raise OSError("프린터를 열 수 없습니다")
        try:
            rect = painter.viewport()
            size = image.size().scaled(rect.size(), Qt.KeepAspectRatio)
            painter.setViewport(rect.x(), rect.y(), size.width(), size.height())
            painter.setWindow(image.rect())
            painter.drawImage(0, 0, image)
        finally:
            painter.end()
    return True
