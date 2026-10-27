from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex
from PySide6.QtWidgets import QFileSystemModel, QLineEdit, QTreeView, QVBoxLayout, QWidget


class FilePanel(QWidget):
    def __init__(self, path: str):
        super().__init__()
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDot)
        self.model.setRootPath(path)

        self.path_bar = QLineEdit(path)
        self.path_bar.returnPressed.connect(lambda: self.set_path(self.path_bar.text()))

        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(path))
        self.view.setRootIsDecorated(False)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, self.view.header().sortIndicatorOrder())
        self.view.setSelectionMode(QTreeView.ExtendedSelection)
        self.view.setColumnWidth(0, 260)
        self.view.doubleClicked.connect(self._open)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_bar)
        layout.addWidget(self.view)

    def set_path(self, path: str) -> None:
        idx = self.model.index(path)
        if self.model.isDir(idx):
            self.view.setRootIndex(idx)
            self.path_bar.setText(self.model.filePath(idx))

    def current_path(self) -> str:
        return self.model.filePath(self.view.rootIndex())

    def selected_paths(self) -> list[Path]:
        rows = self.view.selectionModel().selectedRows(0)
        return [Path(self.model.filePath(i)) for i in rows if self.model.fileName(i) != ".."]

    def _open(self, idx: QModelIndex) -> None:
        if self.model.isDir(idx):
            self.set_path(self.model.filePath(idx))
