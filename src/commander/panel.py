from pathlib import Path

from PySide6.QtCore import QDir, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFileSystemModel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from . import archive


class _View(QTreeView):
    """Tree view with Total Commander style keys: Enter/Backspace/Space/Insert."""

    def __init__(self, tab: "_Tab"):
        super().__init__()
        self._tab = tab

    def keyPressEvent(self, e) -> None:
        key = e.key()
        cur = self.currentIndex()
        if key in (Qt.Key_Return, Qt.Key_Enter) and cur.isValid():
            self._tab._open(cur)
        elif key == Qt.Key_Backspace:
            self._tab.go_up()
        elif key in (Qt.Key_Space, Qt.Key_Insert) and cur.isValid():
            self.selectionModel().select(
                cur, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
            below = self.indexBelow(cur)
            if below.isValid():
                self.selectionModel().setCurrentIndex(below, QItemSelectionModel.NoUpdate)
        else:
            super().keyPressEvent(e)


class _Tab(QWidget):
    def __init__(self, path: str):
        super().__init__()
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDot)
        self.model.setRootPath(path)

        self.path_bar = QLineEdit(path)
        self.path_changed = None  # callback(path), set by FilePanel
        self.path_bar.returnPressed.connect(lambda: self.set_path(self.path_bar.text()))

        self.archive: Path | None = None  # set while browsing inside an archive
        self.inner = ""
        self._entries: list[archive.Entry] = []
        self.arch_model = QStandardItemModel(self)

        self.view = _View(self)
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

    @property
    def in_archive(self) -> bool:
        return self.archive is not None

    def set_path(self, path: str) -> None:
        if archive.is_archive(Path(path)):
            try:
                self.open_archive(Path(path))
            except (OSError, ValueError) as e:
                QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")
            return
        idx = self.model.index(path)
        if self.model.isDir(idx):
            self._leave_archive()
            self.view.setRootIndex(idx)
            self.path_bar.setText(self.model.filePath(idx))
            if self.path_changed:
                self.path_changed(self.model.filePath(idx))

    def open_archive(self, path: Path, inner: str = "") -> None:
        if self.archive != path:
            self._entries = archive.read_entries(path)  # raises before any state changes
            self.archive = path
        self.inner = inner
        m = self.arch_model
        m.clear()
        m.setHorizontalHeaderLabels(["Name", "Size"])
        rows = [("..", True, 0)] + [(e.path.rsplit("/", 1)[-1], e.is_dir, e.size)
                                    for e in archive.children(self._entries, inner)]
        for name, is_dir, size in rows:
            item = QStandardItem(name)
            item.setData(is_dir, Qt.UserRole)
            m.appendRow([item, QStandardItem("" if is_dir else str(size))])
        self.view.setSortingEnabled(False)  # entries are pre-sorted, ".." first
        self.view.setModel(m)
        self.path_bar.setText(f"{path}:/{inner}")
        if self.path_changed:
            self.path_changed(str(path))

    def _leave_archive(self) -> None:
        if self.archive is None:
            return
        self.archive = None
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, self.view.header().sortIndicatorOrder())
        self.view.setColumnWidth(0, 260)

    def go_up(self) -> None:
        if self.archive is None:
            self.set_path(str(Path(self.current_path()).parent))
        elif self.inner:
            self.open_archive(self.archive, self.inner.rpartition("/")[0])
        else:
            self.set_path(str(self.archive.parent))

    def current_path(self) -> str:
        """Current folder; while inside an archive, the folder containing it."""
        if self.archive is not None:
            return str(self.archive.parent)
        return self.model.filePath(self.view.rootIndex())

    def selected_paths(self) -> list[Path]:
        if self.archive is not None:
            return []
        rows = self.view.selectionModel().selectedRows(0)
        return [Path(self.model.filePath(i)) for i in rows if self.model.fileName(i) != ".."]

    def archive_selection(self) -> list[str]:
        """Selected entry paths inside the archive (empty outside archives)."""
        if self.archive is None:
            return []
        prefix = self.inner + "/" if self.inner else ""
        items = (self.arch_model.itemFromIndex(i) for i in self.view.selectionModel().selectedRows(0))
        return [prefix + it.text() for it in items if it.text() != ".."]

    def _open(self, idx: QModelIndex) -> None:
        if self.archive is not None:
            item = self.arch_model.itemFromIndex(idx.siblingAtColumn(0))
            if item.text() == "..":
                self.go_up()
            elif item.data(Qt.UserRole):
                self.open_archive(self.archive, (self.inner + "/" if self.inner else "") + item.text())
            return
        path = self.model.filePath(idx)
        if self.model.isDir(idx) or archive.is_archive(Path(path)):
            self.set_path(path)


class FilePanel(QWidget):
    """A tabbed file panel; view/model/path_bar and path APIs act on the current tab."""

    def __init__(self, path: str):
        super().__init__()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.new_tab(path)

    @property
    def _tab(self) -> _Tab:
        return self.tabs.currentWidget()

    @property
    def view(self) -> QTreeView:
        return self._tab.view

    @property
    def model(self) -> QFileSystemModel:
        return self._tab.model

    @property
    def path_bar(self) -> QLineEdit:
        return self._tab.path_bar

    def new_tab(self, path: str | None = None) -> None:
        path = path or self.current_path()
        tab = _Tab(path)
        tab.path_changed = lambda p, t=tab: self.tabs.setTabText(self.tabs.indexOf(t), _title(p))
        self.tabs.setCurrentIndex(self.tabs.addTab(tab, _title(path)))
        tab.view.setFocus()

    def close_tab(self, index: int | None = None) -> None:
        if self.tabs.count() <= 1:
            return  # keep at least one tab
        index = self.tabs.currentIndex() if index is None else index
        self.tabs.widget(index).deleteLater()
        self.tabs.removeTab(index)

    def set_path(self, path: str) -> None:
        self._tab.set_path(path)

    @property
    def in_archive(self) -> bool:
        return self._tab.in_archive

    @property
    def archive(self) -> Path | None:
        return self._tab.archive

    def archive_selection(self) -> list[str]:
        return self._tab.archive_selection()

    def current_path(self) -> str:
        return self._tab.current_path()

    def selected_paths(self) -> list[Path]:
        return self._tab.selected_paths()


def _title(path: str) -> str:
    return Path(path).name or path
