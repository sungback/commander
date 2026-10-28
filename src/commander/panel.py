import posixpath
from pathlib import Path

from PySide6.QtCore import QDir, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFileSystemModel,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from . import archive, ops
from .remote import FtpClient, SftpClient


class _LocalModel(QFileSystemModel):
    """QFileSystemModel that also displays calculated directory sizes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dir_sizes: dict[str, int] = {}

    def set_dir_size(self, path: str, size: int) -> None:
        self.dir_sizes[path] = size
        idx = self.index(path, 1)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and index.column() == 1 and self.isDir(index):
            path = self.filePath(index)
            if path in self.dir_sizes:
                return ops.format_size(self.dir_sizes[path])
        return super().data(index, role)


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
            if key == Qt.Key_Space:
                self._tab._calc_dir_size(cur)
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
        self.model = _LocalModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDot)
        self.model.setRootPath(path)

        self.path_bar = QLineEdit(path)
        self.path_changed = None  # callback(path), set by FilePanel
        self.path_bar.returnPressed.connect(lambda: self.set_path(self.path_bar.text()))

        self.archive: Path | None = None  # set while browsing inside an archive
        self.inner = ""
        self._entries: list[archive.Entry] = []
        self.arch_model = QStandardItemModel(self)  # also used for remote listings
        self.remote: FtpClient | SftpClient | None = None  # set while browsing an FTP server
        self.remote_path = "/"
        self.local_dir = path  # last local folder, kept while browsing a remote

        self.view = _View(self)
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(path))
        self.view.setRootIsDecorated(False)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, self.view.header().sortIndicatorOrder())
        self.view.setSelectionMode(QTreeView.ExtendedSelection)
        self.view.setColumnWidth(0, 260)
        self.view.doubleClicked.connect(self._open)

        self.status_bar = QLabel()
        self.model.directoryLoaded.connect(lambda _: self.update_status())
        self.model.rowsInserted.connect(lambda *_: self.update_status())
        self.model.rowsRemoved.connect(lambda *_: self.update_status())
        self._bind_selection()
        self.update_status()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_bar)
        layout.addWidget(self.view)
        layout.addWidget(self.status_bar)

    @property
    def in_archive(self) -> bool:
        return self.archive is not None

    @property
    def in_remote(self) -> bool:
        return self.remote is not None

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
            self.local_dir = self.model.filePath(idx)
            self.model.setRootPath(self.local_dir)
            self.path_bar.setText(self.local_dir)
            self.update_status()
            if self.path_changed:
                self.path_changed(self.model.filePath(idx))

    def open_archive(self, path: Path, inner: str = "") -> None:
        if self.archive != path:
            self._entries = archive.read_entries(path)  # raises before any state changes
            self._leave_archive()
            self.archive = path
        self.inner = inner
        rows = [(e.path.rsplit("/", 1)[-1], e.is_dir, e.size)
                for e in archive.children(self._entries, inner)]
        self._show_rows(rows, f"{path}:/{inner}", str(path), show_dir_size=True)

    def open_remote(self, client: FtpClient | SftpClient, path: str = "/") -> None:
        entries = client.listdir(path)  # raises before any state changes
        if self.remote is not client:
            self._leave_archive()
            self.remote = client
        self.remote_path = path
        rows = [(e.name, e.is_dir, e.size) for e in entries]
        self._show_rows(rows, f"{client.scheme}://{client.host}:{client.port}{path}", f"{client.host}{path}")

    def _show_rows(self, rows: list[tuple[str, bool, int]], bar: str, title: str,
                   show_dir_size: bool = False) -> None:
        m = self.arch_model
        m.clear()
        m.setHorizontalHeaderLabels(["Name", "Size"])
        for name, is_dir, size in [("..", True, 0)] + rows:
            item = QStandardItem(name)
            item.setData(is_dir, Qt.UserRole)
            has_size = (not is_dir or show_dir_size) and name != ".."
            size_item = QStandardItem(ops.format_size(size) if has_size else "")
            size_item.setData(size if has_size else None, Qt.UserRole)
            m.appendRow([item, size_item])
        self.view.setSortingEnabled(False)  # entries are pre-sorted, ".." first
        self.view.setModel(m)
        self._bind_selection()
        self.path_bar.setText(bar)
        self.update_status()
        if self.path_changed:
            self.path_changed(title)

    def _leave_archive(self) -> None:
        """Leave archive or remote browsing and return to the local file model."""
        if self.remote is not None:
            self.remote.close()
            self.remote = None
        if self.archive is None and self.view.model() is self.model:
            return
        self.archive = None
        self.view.setModel(self.model)
        self._bind_selection()
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, self.view.header().sortIndicatorOrder())
        self.view.setColumnWidth(0, 260)

    def _bind_selection(self) -> None:
        sel = self.view.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(lambda *_: self.update_status())

    def _calc_dir_size(self, idx: QModelIndex) -> None:
        if self.archive is not None or self.remote is not None:
            return
        idx0 = idx.siblingAtColumn(0)
        if self.model.isDir(idx0) and self.model.fileName(idx0) != "..":
            path = self.model.filePath(idx0)
            self.model.set_dir_size(path, ops.dir_size(Path(path)))
            self.update_status()

    def _entry_size(self, row_idx: QModelIndex) -> int:
        if self.archive is not None or self.remote is not None:
            val = self.arch_model.itemFromIndex(row_idx.siblingAtColumn(1)).data(Qt.UserRole)
            return val or 0
        idx0 = row_idx.siblingAtColumn(0)
        if self.model.isDir(idx0):
            return self.model.dir_sizes.get(self.model.filePath(idx0), 0)
        return self.model.size(idx0)

    def update_status(self) -> None:
        m = self.view.model()
        root = self.view.rootIndex() if m is self.model else QModelIndex()
        total_items = 0
        total_bytes = 0
        for r in range(m.rowCount(root)):
            idx = m.index(r, 0, root)
            if idx.data() == "..":
                continue
            total_items += 1
            total_bytes += self._entry_size(idx)
        sel_rows = [i for i in self.view.selectionModel().selectedRows(0) if i.data() != ".."]
        sel_bytes = sum(self._entry_size(i) for i in sel_rows)
        self.status_bar.setText(
            f"{len(sel_rows)} / {total_items} selected "
            f"({ops.format_size(sel_bytes)} / {ops.format_size(total_bytes)})"
        )

    def go_up(self) -> None:
        if self.remote is not None:
            if self.remote_path != "/":
                self.open_remote(self.remote, posixpath.dirname(self.remote_path.rstrip("/")) or "/")
        elif self.archive is None:
            self.set_path(str(Path(self.current_path()).parent))
        elif self.inner:
            self.open_archive(self.archive, self.inner.rpartition("/")[0])
        else:
            self.set_path(str(self.archive.parent))

    def current_path(self) -> str:
        """Current folder; inside an archive its folder, while on a remote the last local folder."""
        if self.remote is not None:
            return self.local_dir
        if self.archive is not None:
            return str(self.archive.parent)
        return self.model.filePath(self.view.rootIndex())

    def selected_paths(self) -> list[Path]:
        if self.archive is not None or self.remote is not None:
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

    def remote_selection(self) -> list[str]:
        """Selected full remote paths (empty outside remote browsing)."""
        if self.remote is None:
            return []
        items = (self.arch_model.itemFromIndex(i) for i in self.view.selectionModel().selectedRows(0))
        return [posixpath.join(self.remote_path, it.text()) for it in items if it.text() != ".."]

    def _open(self, idx: QModelIndex) -> None:
        if self.remote is not None:
            item = self.arch_model.itemFromIndex(idx.siblingAtColumn(0))
            try:
                if item.text() == "..":
                    self.go_up()
                elif item.data(Qt.UserRole):
                    self.open_remote(self.remote, posixpath.join(self.remote_path, item.text()))
            except OSError as e:
                QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")
            return
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

    @property
    def status_bar(self) -> QLabel:
        return self._tab.status_bar

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
        tab = self.tabs.widget(index)
        if tab.remote is not None:
            tab.remote.close()
        tab.deleteLater()
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

    @property
    def in_remote(self) -> bool:
        return self._tab.in_remote

    @property
    def remote(self) -> FtpClient | SftpClient | None:
        return self._tab.remote

    @property
    def remote_path(self) -> str:
        return self._tab.remote_path

    def remote_selection(self) -> list[str]:
        return self._tab.remote_selection()

    def connect_remote(self, client: FtpClient | SftpClient, path: str = "/") -> None:
        self._tab.open_remote(client, path)

    def current_path(self) -> str:
        return self._tab.current_path()

    def selected_paths(self) -> list[Path]:
        return self._tab.selected_paths()


def _title(path: str) -> str:
    return Path(path).name or path
