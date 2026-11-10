import os
import posixpath
from fnmatch import fnmatchcase
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QItemSelectionModel,
    QModelIndex,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
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
from .workers import DirSizeWorker


def list_drives() -> list[str]:
    """Available root drives (e.g. ['C:\\', 'D:\\'] on Windows, ['/'] on POSIX)."""
    drives = [os.path.normpath(fi.absoluteFilePath()) for fi in QDir.drives()]
    return drives or [os.path.sep]


def name_matches(name: str, pattern: str) -> bool:
    """Case-insensitive match against ';'-separated masks; a mask without wildcards is a substring."""
    masks = [m.strip().lower() for m in pattern.split(";") if m.strip()]
    if not masks:
        return True
    low = name.lower()
    return any(fnmatchcase(low, m) if any(c in m for c in "*?[") else m in low for m in masks)


class _LocalModel(QFileSystemModel):
    """QFileSystemModel that also displays calculated directory sizes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dir_sizes: dict[str, int] = dict(ops.load_size_cache())

    def set_dir_size(self, path: str, size: int) -> None:
        key = ops.norm_key(path)
        if self.dir_sizes.get(key) == size:
            return
        self.dir_sizes[key] = size
        idx = self.index(path, 1)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and index.column() == 1 and self.isDir(index):
            if self.fileName(index) == "..":
                return ""
            key = ops.norm_key(self.filePath(index))
            if key in self.dir_sizes:
                return ops.format_size(self.dir_sizes[key])
            cached = ops.get_cached_size(key)
            if cached is not None:
                self.dir_sizes[key] = cached
                return ops.format_size(cached)
        return super().data(index, role)


class _SortProxy(QSortFilterProxyModel):
    """Keep '..' first and directories above files regardless of sort column/order."""

    def __init__(self, src: _LocalModel, parent=None, show_hidden: bool = False):
        super().__init__(parent)
        self._src = src
        self.show_hidden = show_hidden
        self.name_filter = ""
        self._root_src = QModelIndex()
        self.setSourceModel(src)
        self.setDynamicSortFilter(True)

    @property
    def dir_sizes(self) -> dict[str, int]:
        return self._src.dir_sizes

    def set_dir_size(self, path: str, size: int) -> None:
        self._src.set_dir_size(path, size)

    def _refresh_filter(self) -> None:
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange()
        else:
            self.invalidateFilter()

    def set_show_hidden(self, show: bool) -> None:
        if self.show_hidden == show:
            return
        self.show_hidden = show
        self._refresh_filter()

    def set_name_filter(self, pattern: str) -> None:
        if self.name_filter == pattern:
            return
        self.name_filter = pattern
        self._refresh_filter()

    def setRootPath(self, path: str) -> QModelIndex:
        self._root_src = self._src.setRootPath(path)
        if not self._root_src.isValid():
            self._root_src = self._src.index(path)
        self._refresh_filter()
        return self.mapFromSource(self._root_src)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._root_src.isValid() or source_parent != self._root_src:
            return True
        idx = self._src.index(source_row, 0, source_parent)
        name = self._src.fileName(idx)
        if name == "..":
            return True
        if not self.show_hidden and (name.startswith(".") or self._src.fileInfo(idx).isHidden()):
            return False
        return name_matches(name, self.name_filter)

    def index(self, row_or_path, column: int = 0, parent: QModelIndex | None = None) -> QModelIndex:
        if isinstance(row_or_path, str):
            return self.mapFromSource(self._src.index(row_or_path, column))
        return super().index(row_or_path, column, QModelIndex() if parent is None else parent)

    def flags(self, index: QModelIndex):
        f = super().flags(index)
        if self.fileName(index.siblingAtColumn(0)) == "..":
            f &= ~Qt.ItemIsDragEnabled  # never hand the parent folder to another app
        return f

    def filePath(self, index: QModelIndex) -> str:
        p = self._src.filePath(self.mapToSource(index))
        return os.path.normpath(p) if p else ""

    def fileName(self, index: QModelIndex) -> str:
        return self._src.fileName(self.mapToSource(index))

    def isDir(self, index: QModelIndex) -> bool:
        return self._src.isDir(self.mapToSource(index))

    def size(self, index: QModelIndex) -> int:
        return self._src.size(self.mapToSource(index))

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self._src
        l0 = left.siblingAtColumn(0)
        r0 = right.siblingAtColumn(0)
        l_name = src.fileName(l0)
        r_name = src.fileName(r0)
        asc = self.sortOrder() == Qt.AscendingOrder
        if l_name == "..":
            return asc
        if r_name == "..":
            return not asc
        l_dir = src.isDir(l0)
        r_dir = src.isDir(r0)
        if l_dir != r_dir:
            return l_dir if asc else r_dir
        col = left.column()
        if col == 1:
            l_sz = src.dir_sizes.get(ops.norm_key(src.filePath(l0)), -1) if l_dir else src.size(l0)
            r_sz = src.dir_sizes.get(ops.norm_key(src.filePath(r0)), -1) if r_dir else src.size(r0)
            if l_sz != r_sz:
                return l_sz < r_sz
        elif col in (2, 3):
            return super().lessThan(left, right)
        return l_name.lower() < r_name.lower()


_SELECT_KEYS = (Qt.Key_Plus, Qt.Key_Minus, Qt.Key_Asterisk)


class _FilterEdit(QLineEdit):
    """Quick filter box: Esc clears and closes, Enter/Down returns to the file list."""

    def __init__(self, tab: "_Tab"):
        super().__init__()
        self._tab = tab
        self.setPlaceholderText("Filter (text or *.ext;*.md) — Esc to clear")
        self.setClearButtonEnabled(True)
        self.hide()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self._tab.clear_filter()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self._tab.view.setFocus()
        else:
            super().keyPressEvent(e)


class _View(QTreeView):
    """Tree view with Total Commander style keys: Enter/Backspace/Space/Insert."""

    def __init__(self, tab: "_Tab"):
        super().__init__()
        self._tab = tab
        self.setDragDropMode(QTreeView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos) -> None:
        idx = self.indexAt(pos)
        if idx.isValid() and not self.selectionModel().isRowSelected(idx.row(), idx.parent()):
            self.setCurrentIndex(idx.siblingAtColumn(0))
        elif not idx.isValid():
            self.clearSelection()
        if self._tab.context_requested:
            self._tab.context_requested(self.viewport().mapToGlobal(pos))

    def _accepts(self, e) -> bool:
        """Dropped files are only taken by a local folder listing."""
        return e.mimeData().hasUrls() and not (self._tab.in_archive or self._tab.in_remote)

    def dragEnterEvent(self, e) -> None:
        if self._accepts(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:
        self.dragEnterEvent(e)

    def dropEvent(self, e) -> None:
        if not self._accepts(e):
            e.ignore()
            return
        tab = self._tab
        target = tab.current_path()
        idx = self.indexAt(e.position().toPoint()).siblingAtColumn(0)
        if idx.isValid() and tab.model.isDir(idx) and tab.model.fileName(idx) != "..":
            target = tab.model.filePath(idx)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths
                 if p.name not in ("", "..") and ops.norm_key(p.parent) != ops.norm_key(target)
                 and ops.norm_key(p) != ops.norm_key(target)]
        move = e.proposedAction() == Qt.MoveAction
        # Always report a copy: the drag source must never delete anything itself.
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if paths and tab.files_dropped:
            tab.files_dropped(paths, target, move)

    def keyPressEvent(self, e) -> None:
        key = e.key()
        cur = self.currentIndex()
        if key in (Qt.Key_Return, Qt.Key_Enter) and cur.isValid():
            self._tab._open(cur)
        elif key == Qt.Key_Backspace:
            self._tab.go_up()
        elif key == Qt.Key_Escape and self._tab.filter_text:
            self._tab.clear_filter()
        elif key in _SELECT_KEYS and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            if key == Qt.Key_Asterisk:
                self._tab.invert_selection()
            else:
                self._tab.prompt_select(select=key == Qt.Key_Plus)
        elif key in (Qt.Key_Space, Qt.Key_Insert) and cur.isValid():
            self.selectionModel().select(
                cur, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
            below = self.indexBelow(cur)
            if below.isValid():
                self.selectionModel().setCurrentIndex(below, QItemSelectionModel.NoUpdate)
        else:
            super().keyPressEvent(e)


class _Tab(QWidget):
    def __init__(self, path: str, show_hidden: bool = False):
        super().__init__()
        self._fs_model = _LocalModel(self)
        self._fs_model.setFilter(QDir.AllEntries | QDir.NoDot | QDir.Hidden)
        self.model = _SortProxy(self._fs_model, self, show_hidden=show_hidden)
        root_idx = self.model.setRootPath(path)

        self.drive_box = QComboBox()
        self.drive_box.setFocusPolicy(Qt.NoFocus)
        self.path_bar = QLineEdit(path)
        self.path_changed = None  # callback(path), set by FilePanel
        self.file_activated = None  # callback(Path): Enter / double-click on a local file
        self.context_requested = None  # callback(global QPoint): right-click in the list
        self.files_dropped = None  # callback(paths, target_dir, move): files dropped on the list
        self.path_bar.returnPressed.connect(lambda: self.set_path(self.path_bar.text()))
        self.drive_box.currentIndexChanged.connect(self._on_drive_selected)

        self.archive: Path | None = None  # set while browsing inside an archive
        self.inner = ""
        self._entries: list[archive.Entry] = []
        self._raw_rows: tuple[list[tuple[str, bool, int]], str, str, bool] | None = None
        self.arch_model = QStandardItemModel(self)  # also used for remote listings
        self.remote: FtpClient | SftpClient | None = None  # set while browsing an FTP server
        self.remote_path = "/"
        self.local_dir = path  # last local folder, kept while browsing a remote
        self._size_worker: DirSizeWorker | None = None
        self._pending_cur: str | None = None
        self._pending_sel: set[str] = set()
        self._history: list[str] = [path]
        self._hist_pos = 0
        self.filter_text = ""
        self.filter_bar = _FilterEdit(self)
        self.filter_bar.textChanged.connect(self.set_filter)
        self._sync_drive_box()

        self.view = _View(self)
        self.view.setModel(self.model)
        self.view.setRootIndex(root_idx if root_idx.isValid() else self.model.index(path))
        self.view.setRootIsDecorated(False)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)
        self.view.setSelectionMode(QTreeView.ExtendedSelection)
        self.view.setColumnWidth(0, 260)
        self.view.doubleClicked.connect(self._open)

        self.status_bar = QLabel()
        self._fs_model.directoryLoaded.connect(lambda _: self.update_status())
        self.model.rowsInserted.connect(self._on_rows_changed)
        self.model.rowsRemoved.connect(self._on_rows_changed)
        self._bind_selection()
        self._scan_dir_sizes()
        self.update_status()

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(4)
        top_bar.addWidget(self.drive_box)
        top_bar.addWidget(self.path_bar, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top_bar)
        layout.addWidget(self.view)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.status_bar)

    def _sync_drive_box(self) -> None:
        anchor = os.path.normpath(Path(self.local_dir).anchor or os.path.sep)
        drives = list_drives()
        if all(ops.norm_key(d) != ops.norm_key(anchor) for d in drives):
            drives.append(anchor)
        with QSignalBlocker(self.drive_box):
            cur_items = [self.drive_box.itemText(i) for i in range(self.drive_box.count())]
            if cur_items != drives:
                self.drive_box.clear()
                self.drive_box.addItems(drives)
            for i, d in enumerate(drives):
                if ops.norm_key(d) == ops.norm_key(anchor):
                    self.drive_box.setCurrentIndex(i)
                    break

    def _on_drive_selected(self, index: int) -> None:
        if index < 0:
            return
        drive = self.drive_box.itemText(index)
        if drive:
            self.set_path(drive)

    @property
    def in_archive(self) -> bool:
        return self.archive is not None

    @property
    def in_remote(self) -> bool:
        return self.remote is not None

    @property
    def show_hidden(self) -> bool:
        return self.model.show_hidden

    def set_show_hidden(self, show: bool) -> None:
        if self.model.show_hidden == show:
            return
        self.model.set_show_hidden(show)
        if (self.in_archive or self.in_remote) and self._raw_rows is not None:
            self._show_rows(*self._raw_rows)
        else:
            if show:
                self._scan_dir_sizes()
            self.update_status()

    def toggle_hidden(self) -> bool:
        self.set_show_hidden(not self.show_hidden)
        return self.show_hidden

    def show_filter(self) -> None:
        self.filter_bar.show()
        self.filter_bar.setFocus()
        self.filter_bar.selectAll()

    def set_filter(self, pattern: str) -> None:
        """Show only entries matching `pattern` ('..' always stays) in any of the three modes."""
        if pattern == self.filter_text:
            return
        self.filter_text = pattern
        if self.filter_bar.text() != pattern:
            with QSignalBlocker(self.filter_bar):
                self.filter_bar.setText(pattern)
        if pattern:
            self.filter_bar.show()
        self.model.set_name_filter(pattern)
        if (self.in_archive or self.in_remote) and self._raw_rows is not None:
            self._show_rows(*self._raw_rows)
        else:
            self.update_status()

    def clear_filter(self) -> None:
        self.set_filter("")
        self.filter_bar.hide()
        self.view.setFocus()

    def _reset_filter(self) -> None:
        """Drop the filter without re-rendering; used right before showing another folder."""
        self.filter_text = ""
        with QSignalBlocker(self.filter_bar):
            self.filter_bar.clear()
        self.filter_bar.hide()
        self.model.set_name_filter("")

    def _record_history(self, path: str) -> None:
        if self._history[self._hist_pos] == path:
            return
        del self._history[self._hist_pos + 1:]
        self._history.append(path)
        del self._history[:-50]
        self._hist_pos = len(self._history) - 1

    def go_history(self, step: int) -> bool:
        """Move back (-1) / forward (+1) through visited local folders, skipping vanished ones."""
        pos = self._hist_pos + step
        while 0 <= pos < len(self._history):
            if Path(self._history[pos]).is_dir():
                self._hist_pos = pos
                self.set_path(self._history[pos], record=False)
                return True
            pos += step
        return False

    def set_path(self, path: str, record: bool = True) -> None:
        p = Path(path)
        if archive.is_archive(p):
            try:
                self.open_archive(p)
            except (OSError, ValueError) as e:
                QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")
            return
        if not p.is_dir():
            return
        idx = self.model.setRootPath(path)
        if not idx.isValid():
            idx = self.model.index(path)
        if idx.isValid():
            self._pending_cur = None
            self._pending_sel.clear()
            self._leave_archive()
            self._reset_filter()
            self.view.setRootIndex(idx)
            self.local_dir = self.model.filePath(idx)
            if record:
                self._record_history(self.local_dir)
            self.path_bar.setText(self.local_dir)
            self._sync_drive_box()
            self._scan_dir_sizes()
            self.update_status()
            if self.path_changed:
                self.path_changed(self.local_dir)

    def open_archive(self, path: Path, inner: str = "") -> None:
        if self.archive != path:
            self._entries = archive.read_entries(path)  # raises before any state changes
            self._leave_archive()
            self.archive = path
        if self._raw_rows is None or inner != self.inner:
            self._reset_filter()
        self.inner = inner
        rows = [(e.path.rsplit("/", 1)[-1], e.is_dir, e.size)
                for e in archive.children(self._entries, inner)]
        self._show_rows(rows, f"{path}:/{inner}", str(path), show_dir_size=True)

    def open_remote(self, client: FtpClient | SftpClient, path: str = "/") -> None:
        entries = client.listdir(path)  # raises before any state changes
        if self.remote is not client:
            self._leave_archive()
            self.remote = client
        if self._raw_rows is None or path != self.remote_path:
            self._reset_filter()
        self.remote_path = path
        rows = [(e.name, e.is_dir, e.size) for e in entries]
        self._show_rows(rows, f"{client.scheme}://{client.host}:{client.port}{path}", f"{client.host}{path}")

    def _show_rows(self, rows: list[tuple[str, bool, int]], bar: str, title: str,
                   show_dir_size: bool = False) -> None:
        self._stop_size_worker()
        self._raw_rows = (rows, bar, title, show_dir_size)
        visible = [r for r in rows if (self.show_hidden or not r[0].startswith("."))
                   and name_matches(r[0], self.filter_text)]
        m = self.arch_model
        m.clear()
        m.setHorizontalHeaderLabels(["Name", "Size"])
        for name, is_dir, size in [("..", True, 0)] + visible:
            item = QStandardItem(name)
            item.setData(is_dir, Qt.UserRole)
            item.setDragEnabled(False)
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
        self._raw_rows = None
        if self.archive is None and self.view.model() is self.model:
            return
        self.archive = None
        hdr = self.view.header()
        col, order = hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()
        self.view.setModel(self.model)
        self._bind_selection()
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(col, order)
        self.view.setColumnWidth(0, 260)

    def _bind_selection(self) -> None:
        sel = self.view.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(lambda *_: self.update_status())

    def _on_rows_changed(self, parent: QModelIndex, first: int, last: int) -> None:
        self._apply_pending_selection()
        self.update_status()
        if parent == self.view.rootIndex():
            for r in range(first, last + 1):
                idx = self.model.index(r, 0, parent)
                if self.model.isDir(idx) and ops.norm_key(self.model.filePath(idx)) not in self.model.dir_sizes:
                    if self._size_worker is None or not self._size_worker.isRunning():
                        self._scan_dir_sizes()
                    break

    def _apply_pending_selection(self) -> None:
        if not self._pending_cur and not self._pending_sel:
            return
        m = self.view.model()
        root = self.view.rootIndex() if m is self.model else QModelIndex()
        sel_model = self.view.selectionModel()
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        for r in range(m.rowCount(root)):
            idx = m.index(r, 0, root)
            name = idx.data()
            if name in self._pending_sel:
                sel_model.select(idx, flags)
                self._pending_sel.discard(name)
            if self._pending_cur and name == self._pending_cur:
                sel_model.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
                self._pending_cur = None

    def to_state(self) -> dict:
        hdr = self.view.header()
        cur = self.view.currentIndex()
        cur_name = cur.siblingAtColumn(0).data() if cur.isValid() else ""
        if self.in_archive:
            sel_names = [s.rsplit("/", 1)[-1] for s in self.archive_selection()]
        else:
            sel_names = [p.name for p in self.selected_paths()]
        return {
            "path": self.local_dir,
            "archive": str(self.archive) if self.archive else "",
            "inner": self.inner if self.archive else "",
            "sort_col": hdr.sortIndicatorSection(),
            "sort_order": hdr.sortIndicatorOrder().value,
            "show_hidden": self.show_hidden,
            "current": cur_name or "",
            "selected": sel_names,
        }

    def restore_state(self, state: dict) -> None:
        self.set_show_hidden(bool(state.get("show_hidden", False)))
        path = state.get("path", "")
        if path and Path(path).is_dir():
            self.set_path(path)
        arc = state.get("archive", "")
        if arc and archive.is_archive(Path(arc)):
            try:
                self.open_archive(Path(arc), state.get("inner", ""))
            except (OSError, ValueError):
                pass
        col = int(state.get("sort_col", 0))
        order = Qt.DescendingOrder if state.get("sort_order", 0) == 1 else Qt.AscendingOrder
        if not self.in_archive and not self.in_remote:
            self.view.sortByColumn(col, order)
        else:
            self.view.header().setSortIndicator(col, order)
        self._pending_cur = state.get("current") or None
        self._pending_sel = set(state.get("selected") or [])
        self._apply_pending_selection()
        self.update_status()

    def _stop_size_worker(self) -> None:
        if self._size_worker is not None:
            w = self._size_worker
            self._size_worker = None
            w.cancel()
            try:
                w.sized.disconnect()
            except RuntimeError:
                pass

    def _scan_dir_sizes(self) -> None:
        if self.archive is not None or self.remote is not None:
            return
        self._stop_size_worker()
        worker = DirSizeWorker(Path(self.local_dir), include_hidden=self.show_hidden)
        worker.sized.connect(lambda p, s: (self.model.set_dir_size(p, s), self.update_status()))
        self._size_worker = worker
        worker.start()

    def _entry_size(self, row_idx: QModelIndex) -> int:
        if self.archive is not None or self.remote is not None:
            val = self.arch_model.itemFromIndex(row_idx.siblingAtColumn(1)).data(Qt.UserRole)
            return val or 0
        idx0 = row_idx.siblingAtColumn(0)
        if self.model.isDir(idx0):
            key = ops.norm_key(self.model.filePath(idx0))
            if key in self.model.dir_sizes:
                return self.model.dir_sizes[key]
            cached = ops.get_cached_size(key)
            if cached is not None:
                self.model.dir_sizes[key] = cached
                return cached
            return 0
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

    def _name_rows(self) -> list[QModelIndex]:
        """Column-0 indexes of every listed entry except '..' (works in all three modes)."""
        m = self.view.model()
        root = self.view.rootIndex() if m is self.model else QModelIndex()
        rows = (m.index(r, 0, root) for r in range(m.rowCount(root)))
        return [i for i in rows if i.data() != ".."]

    def select_pattern(self, pattern: str, select: bool = True) -> int:
        """Select (or deselect) entries matching the masks; returns how many matched."""
        flag = QItemSelectionModel.Select if select else QItemSelectionModel.Deselect
        hits = [i for i in self._name_rows() if name_matches(i.data(), pattern)]
        for i in hits:
            self.view.selectionModel().select(i, flag | QItemSelectionModel.Rows)
        return len(hits)

    def select_names(self, names) -> int:
        """Replace the selection with the entries whose names are in `names`."""
        self.view.clearSelection()
        hits = [i for i in self._name_rows() if i.data() in names]
        for i in hits:
            self.view.selectionModel().select(
                i, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        return len(hits)

    def invert_selection(self) -> None:
        for i in self._name_rows():
            self.view.selectionModel().select(
                i, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)

    def prompt_select(self, select: bool = True) -> None:
        title = "Select" if select else "Deselect"
        pattern, ok = QInputDialog.getText(
            self, title, "Mask (e.g. *.txt;*.md, or part of a name):", QLineEdit.Normal, "*")
        if ok and pattern.strip():
            self.select_pattern(pattern, select)

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
        if self.model.fileName(idx) == "..":
            self.go_up()
            return
        path = self.model.filePath(idx)
        if self.model.isDir(idx) or archive.is_archive(Path(path)):
            self.set_path(path)
        elif self.file_activated:
            self.file_activated(Path(path))

    def selection_labels(self) -> list[str]:
        """Copyable locations of the selection (or of the current folder when nothing is selected)."""
        if self.remote is not None:
            base = f"{self.remote.scheme}://{self.remote.host}:{self.remote.port}"
            return [base + p for p in self.remote_selection() or [self.remote_path]]
        if self.archive is not None:
            inner = self.archive_selection() or ([self.inner] if self.inner else [])
            return [f"{self.archive}:/{i}" for i in inner] or [str(self.archive)]
        return [str(p) for p in self.selected_paths()] or [self.current_path()]


class FilePanel(QWidget):
    """A tabbed file panel; view/model/path_bar and path APIs act on the current tab."""

    file_activated = Signal(object)  # Path of a local file opened with Enter / double-click
    context_requested = Signal(object)  # global QPoint of a right-click in the list
    files_dropped = Signal(object, str, bool)  # paths, target folder, move

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
    def drive_box(self) -> QComboBox:
        return self._tab.drive_box

    @property
    def path_bar(self) -> QLineEdit:
        return self._tab.path_bar

    @property
    def status_bar(self) -> QLabel:
        return self._tab.status_bar

    def new_tab(self, path: str | None = None) -> None:
        prev = self._tab if self.tabs.count() else None
        path = path or (prev.current_path() if prev else str(Path.home()))
        tab = _Tab(path, show_hidden=prev.show_hidden if prev else False)
        if prev is not None:
            hdr = prev.view.header()
            tab.view.sortByColumn(hdr.sortIndicatorSection(), hdr.sortIndicatorOrder())
        tab.path_changed = lambda p, t=tab: self.tabs.setTabText(self.tabs.indexOf(t), _title(p))
        tab.file_activated = self.file_activated.emit
        tab.context_requested = self.context_requested.emit
        tab.files_dropped = self.files_dropped.emit
        self.tabs.setCurrentIndex(self.tabs.addTab(tab, _title(path)))
        tab.view.setFocus()

    def to_state(self) -> dict:
        return {
            "active": self.tabs.currentIndex(),
            "tabs": [self.tabs.widget(i).to_state() for i in range(self.tabs.count())],
        }

    def restore_state(self, state: dict) -> None:
        tabs = state.get("tabs") or []
        if not tabs:
            return
        for i, ts in enumerate(tabs):
            if i == 0:
                self.tabs.widget(0).restore_state(ts)
            else:
                path = ts.get("path", "")
                self.new_tab(path if path and Path(path).is_dir() else None)
                self.tabs.widget(self.tabs.count() - 1).restore_state(ts)
        active = int(state.get("active", 0))
        if 0 <= active < self.tabs.count():
            self.tabs.setCurrentIndex(active)

    def close_tab(self, index: int | None = None) -> None:
        if self.tabs.count() <= 1:
            return  # keep at least one tab
        index = self.tabs.currentIndex() if index is None else index
        tab = self.tabs.widget(index)
        tab._stop_size_worker()
        if tab.remote is not None:
            tab.remote.close()
        tab.deleteLater()
        self.tabs.removeTab(index)

    def set_path(self, path: str) -> None:
        self._tab.set_path(path)

    def go_back(self) -> bool:
        return self._tab.go_history(-1)

    def go_forward(self) -> bool:
        return self._tab.go_history(1)

    @property
    def filter_text(self) -> str:
        return self._tab.filter_text

    def show_filter(self) -> None:
        self._tab.show_filter()

    def set_filter(self, pattern: str) -> None:
        self._tab.set_filter(pattern)

    def clear_filter(self) -> None:
        self._tab.clear_filter()

    def select_pattern(self, pattern: str, select: bool = True) -> int:
        return self._tab.select_pattern(pattern, select)

    def select_names(self, names) -> int:
        return self._tab.select_names(names)

    def invert_selection(self) -> None:
        self._tab.invert_selection()

    @property
    def show_hidden(self) -> bool:
        return self._tab.show_hidden

    def set_show_hidden(self, show: bool) -> None:
        self._tab.set_show_hidden(show)

    def toggle_hidden(self) -> bool:
        return self._tab.toggle_hidden()

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

    def selection_labels(self) -> list[str]:
        return self._tab.selection_labels()

    def selected_paths(self) -> list[Path]:
        return self._tab.selected_paths()


def _title(path: str) -> str:
    return Path(path).name or path
