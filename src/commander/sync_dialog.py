"""Synchronize two folders: compare, review the plan, then hand the checked items back."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import ops, sync
from .transfer import Cancelled, Control

_LABELS = {sync.TO_RIGHT: "→", sync.TO_LEFT: "←", sync.EQUAL: "=", sync.CONFLICT: "≠ 충돌",
           sync.DEL_LEFT: "✕ 왼쪽 삭제", sync.DEL_RIGHT: "삭제 오른쪽 ✕", sync.NONE: "–"}
_ACTIVE = (sync.TO_RIGHT, sync.TO_LEFT, sync.DEL_LEFT, sync.DEL_RIGHT)
_MODES = (("양방향 (최신 파일 우선)", "both"), ("왼쪽 → 오른쪽", "to_right"),
          ("오른쪽 → 왼쪽", "to_left"))


def _info(info: sync.Info | None) -> str:
    if info is None:
        return ""
    when = datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d %H:%M")
    return f"<폴더>  {when}" if info.is_dir else f"{ops.format_size(info.size)}  {when}"


class _ScanThread(QThread):
    def __init__(self, parent, **kwargs):
        super().__init__(parent)
        self.kwargs = kwargs
        self.control = Control()
        self.items: list[sync.Item] | None = None
        self.error = ""

    def run(self) -> None:
        try:
            self.items = sync.scan(control=self.control, **self.kwargs)
        except Cancelled:
            pass
        except (OSError, ValueError) as e:
            self.error = f"{type(e).__name__}: {e}"


class SyncDialog(QDialog):
    """After `exec()` returns Accepted, `plan` holds the items to apply."""

    def __init__(self, left: Path, right: Path, include_hidden: bool = False, parent=None):
        super().__init__(parent)
        self.left, self.right = Path(left), Path(right)
        self.items: list[sync.Item] = []
        self.plan: list[sync.Item] = []
        self._unchecked: set[str] = set()  # rows the user excluded; survives re-filtering
        self._scan: _ScanThread | None = None
        self.setWindowTitle("폴더 동기화")
        self.resize(980, 600)

        self.mode = QComboBox()
        for label, value in _MODES:
            self.mode.addItem(label, value)
        self.delete_extra = QCheckBox("대상에만 있는 항목 삭제 (휴지통)")
        self.by_content = QCheckBox("내용으로 비교")
        self.hidden = QCheckBox("숨김 파일 포함")
        self.hidden.setChecked(include_hidden)
        self.show_equal = QCheckBox("같은 항목 표시")
        self.compare_btn = QPushButton("비교")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.show_equal.toggled.connect(lambda _: self._fill())
        self.compare_btn.clicked.connect(self.start_scan)
        self._mode_changed()

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setHeaderLabels(["경로", "왼쪽", "동작", "오른쪽"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 200)
        self.tree.setColumnWidth(2, 120)
        self.tree.itemChanged.connect(self._row_changed)

        self.to_right_btn = QPushButton("선택 항목 →")
        self.to_left_btn = QPushButton("선택 항목 ←")
        self.skip_btn = QPushButton("선택 항목 건너뛰기")
        self.to_right_btn.clicked.connect(lambda: self.set_action(sync.TO_RIGHT))
        self.to_left_btn.clicked.connect(lambda: self.set_action(sync.TO_LEFT))
        self.skip_btn.clicked.connect(lambda: self.set_action(sync.NONE))
        self.summary = QLabel("'비교'를 눌러 두 폴더를 비교하세요.")
        self.sync_btn = QPushButton("동기화")
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(self._confirm)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.reject)

        options = QHBoxLayout()
        for w in (self.mode, self.delete_extra, self.by_content, self.hidden, self.show_equal):
            options.addWidget(w)
        options.addStretch(1)
        options.addWidget(self.compare_btn)
        edit = QHBoxLayout()
        for w in (self.to_right_btn, self.to_left_btn, self.skip_btn):
            edit.addWidget(w)
        edit.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.sync_btn)
        bottom.addWidget(close_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"왼쪽: {self.left}\n오른쪽: {self.right}"))
        layout.addLayout(options)
        layout.addWidget(self.tree, 1)
        layout.addLayout(edit)
        layout.addLayout(bottom)

    def _mode_changed(self) -> None:
        one_way = self.mode.currentData() != "both"
        self.delete_extra.setEnabled(one_way)
        if not one_way:
            self.delete_extra.setChecked(False)

    def start_scan(self) -> None:
        if self._scan is not None:
            return
        self.compare_btn.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self.summary.setText("비교 중...")
        self._scan = _ScanThread(
            self, left=self.left, right=self.right, mode=self.mode.currentData(),
            delete_extra=self.delete_extra.isChecked(), by_content=self.by_content.isChecked(),
            include_hidden=self.hidden.isChecked())
        self._scan.finished.connect(self._scan_done)
        self._scan.start()

    def _scan_done(self) -> None:
        scan, self._scan = self._scan, None
        scan.deleteLater()
        self.compare_btn.setEnabled(True)
        if scan.error:
            self.summary.setText(scan.error)
            return
        if scan.items is None:
            return  # cancelled
        self.items = scan.items
        self._unchecked.clear()
        self._fill()

    def _fill(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for it in self.items:
            if it.action == sync.EQUAL and not self.show_equal.isChecked():
                continue
            row = QTreeWidgetItem([it.rel, _info(it.left), "", _info(it.right)])
            row.setData(0, Qt.UserRole, it)
            row.setTextAlignment(2, Qt.AlignCenter)
            self.tree.addTopLevelItem(row)
            self._show_action(row)
        self.tree.blockSignals(False)
        self._update_summary()

    def _row_changed(self, row: QTreeWidgetItem, _column: int) -> None:
        rel = row.data(0, Qt.UserRole).rel
        if row.checkState(0) == Qt.Unchecked:
            self._unchecked.add(rel)
        else:
            self._unchecked.discard(rel)
        self._update_summary()

    def _show_action(self, row: QTreeWidgetItem) -> None:
        it: sync.Item = row.data(0, Qt.UserRole)
        row.setText(2, _LABELS[it.action])
        if it.action in _ACTIVE:
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Unchecked if it.rel in self._unchecked else Qt.Checked)
        else:
            row.setFlags(row.flags() & ~Qt.ItemIsUserCheckable)
            row.setData(0, Qt.CheckStateRole, None)

    def set_action(self, action: str) -> None:
        """Override the action of the selected rows (a side that lacks the item cannot be a source)."""
        self.tree.blockSignals(True)
        for row in self.tree.selectedItems():
            it: sync.Item = row.data(0, Qt.UserRole)
            source = it.left if action == sync.TO_RIGHT else it.right if action == sync.TO_LEFT else True
            if source is None or it.action == sync.EQUAL:
                continue
            it.action = action
            self._show_action(row)
        self.tree.blockSignals(False)
        self._update_summary()

    def checked_items(self) -> list[sync.Item]:
        rows = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [r.data(0, Qt.UserRole) for r in rows
                if r.data(0, Qt.UserRole).action in _ACTIVE and r.checkState(0) == Qt.Checked]

    def _update_summary(self) -> None:
        chosen = sync.summarize(self.checked_items())
        total = sync.summarize(self.items)
        deletes = chosen[sync.DEL_LEFT] + chosen[sync.DEL_RIGHT]
        self.summary.setText(
            f"→ {chosen[sync.TO_RIGHT]}   ← {chosen[sync.TO_LEFT]}   삭제 {deletes}   "
            f"충돌 {total[sync.CONFLICT]}   같음 {total[sync.EQUAL]}")
        self.sync_btn.setEnabled(bool(self.checked_items()))

    def _confirm(self) -> None:
        plan = self.checked_items()
        c = sync.summarize(plan)
        deletes = c[sync.DEL_LEFT] + c[sync.DEL_RIGHT]
        text = (f"오른쪽으로 복사 {c[sync.TO_RIGHT]}개, 왼쪽으로 복사 {c[sync.TO_LEFT]}개"
                + (f", 휴지통으로 삭제 {deletes}개" if deletes else "")
                + "\n같은 이름의 기존 파일은 덮어씁니다. 진행할까요?")
        if QMessageBox.question(self, "폴더 동기화", text) != QMessageBox.Yes:
            return
        self.plan = plan
        self.accept()

    def done(self, result: int) -> None:
        if self._scan is not None:
            self._scan.finished.disconnect(self._scan_done)
            self._scan.control.cancel()
            self._scan.wait()  # never destroy a running thread
            self._scan = None
        super().done(result)
