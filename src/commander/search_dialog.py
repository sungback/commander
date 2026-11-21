from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

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
        self.action: str | None = None
        self.selected_paths: list[Path] = []

        what = f"{pattern} ('{text}' 포함)" if text else pattern
        self.setWindowTitle(f"검색: {what} — {root}")
        self.resize(700, 450)

        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.itemActivated.connect(self._choose)
        self.results.itemSelectionChanged.connect(self._update_btn_states)

        self.feed_btn = QPushButton("패널로 내보내기")
        self.feed_btn.setEnabled(False)
        self.feed_btn.clicked.connect(self._feed)

        self.goto_btn = QPushButton("폴더로 이동")
        self.goto_btn.setEnabled(False)
        self.goto_btn.clicked.connect(self._goto_selected)

        self.copy_btn = QPushButton("복사")
        self.copy_btn.clicked.connect(lambda: self._operate("copy"))
        self.move_btn = QPushButton("이동")
        self.move_btn.clicked.connect(lambda: self._operate("move"))
        self.trash_btn = QPushButton("휴지통")
        self.trash_btn.clicked.connect(lambda: self._operate("trash"))
        for button in (self.copy_btn, self.move_btn, self.trash_btn):
            button.setEnabled(False)

        self.stop = QPushButton("중지")
        self.stop.clicked.connect(self._stop)

        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.reject)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.feed_btn)
        btn_layout.addWidget(self.goto_btn)
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.move_btn)
        btn_layout.addWidget(self.trash_btn)
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
        count = len(self.results.selectedItems())
        self.goto_btn.setEnabled(count == 1)
        for button in (self.copy_btn, self.move_btn, self.trash_btn):
            button.setEnabled(count > 0)

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
        items = self.results.selectedItems()
        if len(items) == 1:
            self._choose(items[0])

    def _operate(self, action: str) -> None:
        selected = self.results.selectedItems()
        if not selected:
            return
        self.action = action
        self.selected_paths = [Path(item.text()) for item in selected]
        self.accept()

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
