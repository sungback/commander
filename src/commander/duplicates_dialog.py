"""Review duplicate files before selecting any for the trash."""

import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import duplicates, ops


class _ScanThread(QThread):
    progress = Signal(str)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.cancelled = False
        self.result_data: duplicates.ScanResult | None = None
        self.errors: list[str] = []
        self.error_count = 0
        self._last_progress = 0.0

    def cancel(self) -> None:
        self.cancelled = True

    def _error(self, path: Path, error: Exception) -> None:
        self.error_count += 1
        if len(self.errors) < 5:
            self.errors.append(f"{path}: {error}")

    def _progress(self, path: Path) -> None:
        now = time.monotonic()
        if now - self._last_progress >= 0.2:
            self._last_progress = now
            self.progress.emit(str(path))

    def run(self) -> None:
        try:
            self.result_data = duplicates.find_duplicates(
                self.root, cancelled=lambda: self.cancelled,
                on_error=self._error, on_progress=self._progress)
        except duplicates.DuplicateScanCancelled:
            pass
        except (OSError, ValueError) as error:
            self._error(self.root, error)


class DuplicatesDialog(QDialog):
    """Scan a local tree and return explicitly selected paths and their groups."""

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.groups: list[list[Path]] = []
        self.result_data: duplicates.ScanResult | None = None
        self.selected_paths: list[Path] = []
        self.selected_groups: list[tuple[list[Path], list[Path]]] = []
        self.setWindowTitle("중복 파일 찾기")
        self.resize(820, 520)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["경로", "크기"])
        self.tree.setColumnWidth(0, 650)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._update_selection)
        self.status = QLabel("검색 중...")
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.PlainText)
        self.errors.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trash_btn = QPushButton("선택 항목 휴지통으로")
        self.trash_btn.setEnabled(False)
        self.trash_btn.clicked.connect(self._select_trash)
        self.stop_btn = QPushButton("중지")
        self.stop_btn.clicked.connect(self._stop)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.trash_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(close_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"검색 폴더: {self.root}\n각 그룹에서 보관할 파일을 하나 이상 남겨두세요."))
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.errors)
        layout.addLayout(buttons)

        self.worker = _ScanThread(self.root, self)
        self.worker.progress.connect(lambda path: self.status.setText(f"검색 중: {path}"))
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _stop(self) -> None:
        self.worker.cancel()
        self.stop_btn.setEnabled(False)
        self.status.setText("중지 중...")

    def _done(self) -> None:
        self.stop_btn.setEnabled(False)
        if self.worker.error_count:
            errors = self.worker.errors
            count = self.worker.error_count
            more = f"\n외 {count - 5}개" if count > 5 else ""
            self.errors.setText(f"검색 중 오류 {count}개:\n" + "\n".join(errors) + more)
        if self.worker.cancelled:
            self.status.setText("검색이 중지되었습니다.")
            return
        if self.worker.result_data is None:
            self.status.setText("검색을 완료하지 못했습니다.")
            return
        self.result_data = self.worker.result_data
        self.groups = self.result_data.groups
        for index, group in enumerate(self.groups, 1):
            try:
                size = ops.format_size(group[0].stat().st_size)
            except OSError:
                size = "확인 불가"
            parent = QTreeWidgetItem([f"그룹 {index} ({len(group)}개)", size])
            parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(parent)
            for path in group:
                row = QTreeWidgetItem([str(path), size])
                row.setData(0, Qt.UserRole, str(path))
                parent.addChild(row)
            parent.setExpanded(True)
        self._update_selection()

    def _update_selection(self) -> None:
        selected = self.tree.selectedItems()
        all_selected = any(
            group.childCount() and all(group.child(i).isSelected() for i in range(group.childCount()))
            for group in (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        )
        self.trash_btn.setEnabled(bool(selected) and not all_selected and self.worker.isFinished())
        if all_selected:
            self.status.setText("각 그룹에 보관할 파일을 하나 이상 남겨두세요.")
        elif self.worker.isFinished():
            self.status.setText(f"중복 {len(self.groups)}그룹 — {len(selected)}개 선택")

    def _select_trash(self) -> None:
        self._update_selection()
        if not self.trash_btn.isEnabled():
            return
        self.selected_paths = [Path(item.data(0, Qt.UserRole)) for item in self.tree.selectedItems()]
        selected = set(self.selected_paths)
        self.selected_groups = [(group, [path for path in group if path in selected])
                                for group in self.groups if any(path in selected for path in group)]
        self.accept()

    def done(self, result: int) -> None:
        self.worker.cancel()
        self.worker.wait()
        super().done(result)
