"""Background execution of ops functions with progress and cancel."""
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

Task = Callable[[Callable[[Path], None]], None]


class Cancelled(Exception):
    pass


class Worker(QThread):
    progress = Signal(int, str)  # items done, last item name
    failed = Signal(str)

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self._task = task
        self._cancel = False
        self._done = 0

    def cancel(self) -> None:
        self._cancel = True

    def _on_item(self, path: Path) -> None:
        self._done += 1
        self.progress.emit(self._done, Path(path).name)
        if self._cancel:  # ops report after each item, so cancel lands between items
            raise Cancelled

    def run(self) -> None:
        try:
            self._task(self._on_item)
        except Cancelled:
            pass
        except (OSError, ValueError) as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
