from pathlib import Path

from PySide6.QtWidgets import QDialog, QListWidget, QPushButton, QVBoxLayout

from .workers import SearchWorker


class SearchDialog(QDialog):
    """Streams search results; activating a result sets `chosen` and closes."""

    def __init__(self, root: Path, pattern: str, parent=None):
        super().__init__(parent)
        self.chosen: Path | None = None
        self.setWindowTitle(f"Search: {pattern} in {root}")
        self.resize(700, 450)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._choose)
        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(self._stop)
        layout = QVBoxLayout(self)
        layout.addWidget(self.results)
        layout.addWidget(self.stop)

        self.worker = SearchWorker(root, pattern, self)
        self.worker.found.connect(self.results.addItem)
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _stop(self) -> None:
        self.worker.cancel()

    def _done(self) -> None:
        self.stop.setEnabled(False)
        n = self.results.count()
        suffix = f" (limited to first {n})" if self.worker.truncated else ""
        self.setWindowTitle(f"{self.windowTitle()} - {n} found{suffix}")

    def _choose(self, item) -> None:
        self.chosen = Path(item.text())
        self.accept()

    def done(self, result: int) -> None:
        self.worker.cancel()
        self.worker.wait()  # never destroy a running thread
        super().done(result)
