from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QPushButton, QVBoxLayout

from .workers import SearchWorker


class SearchDialog(QDialog):
    """Streams search results; activating a result sets `chosen` and closes.

    Provides 'Feed to Listbox' (패널로 내보내기) to display the matched files
    in the active panel as a flat list for bulk operations.
    """

    def __init__(self, root: Path, pattern: str, parent=None, text: str = ""):
        super().__init__(parent)
        self.root = root
        self.pattern = pattern
        self.text = text
        self.chosen: Path | None = None
        self.feed_to_panel: bool = False
        self.found_paths: list[Path] = []

        what = f"{pattern} ('{text}' 포함)" if text else pattern
        self.setWindowTitle(f"검색: {what} — {root}")
        self.resize(700, 450)

        self.results = QListWidget()
        self.results.itemActivated.connect(self._choose)
        self.results.itemSelectionChanged.connect(self._update_btn_states)

        self.feed_btn = QPushButton("패널로 내보내기")
        self.feed_btn.setEnabled(False)
        self.feed_btn.clicked.connect(self._feed)

        self.goto_btn = QPushButton("폴더로 이동")
        self.goto_btn.setEnabled(False)
        self.goto_btn.clicked.connect(self._goto_selected)

        self.stop = QPushButton("중지")
        self.stop.clicked.connect(self._stop)

        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.reject)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.feed_btn)
        btn_layout.addWidget(self.goto_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.stop)
        btn_layout.addWidget(self.close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.results)
        layout.addLayout(btn_layout)

        self.worker = SearchWorker(root, pattern, self, text=text)
        self.worker.found.connect(self._on_found)
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _on_found(self, path_str: str) -> None:
        self.results.addItem(path_str)
        self.feed_btn.setEnabled(True)

    def _update_btn_states(self) -> None:
        has_sel = self.results.currentItem() is not None
        self.goto_btn.setEnabled(has_sel)

    def _stop(self) -> None:
        self.worker.cancel()

    def _done(self) -> None:
        self.stop.setEnabled(False)
        n = self.results.count()
        suffix = f" (처음 {n}개만 표시)" if self.worker.truncated else ""
        self.setWindowTitle(f"{self.windowTitle()} - {n}개 찾음{suffix}")

    def _choose(self, item) -> None:
        self.chosen = Path(item.text())
        self.accept()

    def _goto_selected(self) -> None:
        item = self.results.currentItem()
        if item:
            self._choose(item)

    def _feed(self) -> None:
        self.worker.cancel()
        self.worker.wait()
        self.feed_to_panel = True
        self.found_paths = [Path(self.results.item(i).text()) for i in range(self.results.count())]
        self.accept()

    def done(self, result: int) -> None:
        self.worker.cancel()
        self.worker.wait()  # never destroy a running thread
        super().done(result)
