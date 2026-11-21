"""Save, restore and remove named panel layouts."""

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import workspaces


class WorkspaceDialog(QDialog):
    def __init__(self, path: Path | None, snapshot: dict, parent=None):
        super().__init__(parent)
        self.path = path
        self.snapshot = snapshot
        self.selected_state: dict | None = None
        self.layouts: dict[str, dict] = {}
        self._valid = False
        self.setWindowTitle("작업 공간")
        self.resize(480, 360)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _: self._restore())
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.save_btn = QPushButton("현재 구성 저장")
        self.save_btn.clicked.connect(self._save)
        self.restore_btn = QPushButton("불러오기")
        self.restore_btn.clicked.connect(self._restore)
        self.delete_btn = QPushButton("삭제")
        self.delete_btn.clicked.connect(self._delete)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for button in (self.save_btn, self.restore_btn, self.delete_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("저장된 좌우 패널과 탭 구성"))
        layout.addWidget(self.list)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        if path is None:
            self.status.setText("작업 공간 저장 위치가 설정되지 않았습니다.")
        else:
            try:
                self.layouts = workspaces.load(path)
                self._valid = True
            except (OSError, workspaces.InvalidWorkspaceFile) as error:
                self.status.setText(f"작업 공간 파일을 읽을 수 없습니다: {error}")
        self._refresh()

    def _update_buttons(self) -> None:
        selected = self.list.currentItem() is not None
        self.save_btn.setEnabled(self._valid)
        self.restore_btn.setEnabled(self._valid and selected)
        self.delete_btn.setEnabled(self._valid and selected)

    def _refresh(self, selected: str = "") -> None:
        self.list.clear()
        self.list.addItems(sorted(self.layouts, key=str.casefold))
        for index in range(self.list.count()):
            if self.list.item(index).text() == selected:
                self.list.setCurrentRow(index)
                break
        self._update_buttons()

    def _persist(self, layouts: dict[str, dict]) -> bool:
        try:
            # Do not overwrite a file that became malformed while the dialog was open.
            if workspaces.load(self.path) != self.layouts:
                raise ValueError("작업 공간이 변경되었습니다. 창을 다시 열어 주세요")
            workspaces.save(self.path, layouts)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "작업 공간", f"저장하지 못했습니다: {error}")
            return False
        self.layouts = layouts
        return True

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "작업 공간 저장", "이름:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.layouts and QMessageBox.question(
            self, "작업 공간 저장", f"'{name}' 작업 공간을 덮어쓸까요?"
        ) != QMessageBox.Yes:
            return
        layouts = dict(self.layouts)
        layouts[name] = self.snapshot
        if self._persist(layouts):
            self._refresh(name)

    def _restore(self) -> None:
        item = self.list.currentItem()
        if item is not None and self._valid:
            self.selected_state = self.layouts[item.text()]
            self.accept()

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        if QMessageBox.question(self, "작업 공간 삭제", f"'{name}' 작업 공간을 삭제할까요?") != QMessageBox.Yes:
            return
        layouts = dict(self.layouts)
        del layouts[name]
        if self._persist(layouts):
            self._refresh()
