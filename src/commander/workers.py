"""Background execution of ops functions with progress and cancel."""
import atexit
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import ops, search, updater

Task = Callable[[Callable[[Path], None]], None]


class Cancelled(Exception):
    pass


class Worker(QThread):
    progress = Signal(int, str)  # items done, last item name
    failed = Signal(str)

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self._task = task
        self._cancel = False
        self._done = 0

    def cancel(self) -> None:
        self._cancel = True

    def _on_item(self, path: Path) -> None:
        self._done += 1
        self.progress.emit(self._done, Path(path).name)
        if self._cancel:  # ops report after each item, so cancel lands between items
            raise Cancelled

    def run(self) -> None:
        try:
            self._task(self._on_item)
        except Cancelled:
            pass
        except (OSError, ValueError) as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class SearchWorker(QThread):
    found = Signal(str)

    LIMIT = 5000

    def __init__(self, root: Path, pattern: str, parent=None):
        super().__init__(parent)
        self._root = root
        self._pattern = pattern
        self._cancel = False
        self.truncated = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        count = 0
        for path in search.find(self._root, self._pattern, lambda: self._cancel, self.LIMIT):
            self.found.emit(str(path))
            count += 1
        self.truncated = count >= self.LIMIT


_size_workers: set["DirSizeWorker"] = set()


class DirSizeWorker(QThread):
    sized = Signal(str, object)  # path, int bytes (object avoids 32-bit C++ int overflow)

    def __init__(self, root: Path, include_hidden: bool = False, parent=None):
        super().__init__(parent)
        self._root = Path(root)
        self._include_hidden = include_hidden
        self._cancel = False
        _size_workers.add(self)
        self.finished.connect(self._cleanup)

    def _cleanup(self) -> None:
        _size_workers.discard(self)

    @classmethod
    def stop_all(cls) -> None:
        for w in list(_size_workers):
            w.cancel()
            w.wait(2000)
        _size_workers.clear()

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            entries = sorted(
                (e for e in self._root.iterdir()
                 if (self._include_hidden or not e.name.startswith("."))
                 and e.is_dir() and not e.is_symlink()),
                key=lambda p: (p.name.startswith("."), p.name.lower()),
            )
        except OSError:
            return
        deferred: list[Path] = []
        for e in entries:
            if self._cancel:
                return
            path = str(e)
            cached = ops.get_cached_size(path)
            if cached is not None:
                self.sized.emit(path, cached)
                continue
            size, done = ops.dir_size(e, cancel=lambda: self._cancel, max_dirs=60)
            if not self._cancel:
                if done:
                    ops.set_cached_size(path, size)
                self.sized.emit(path, size)
                if not done:
                    deferred.append(e)
        for e in deferred:
            if self._cancel:
                return
            path = str(e)
            size, done = ops.dir_size(
                e,
                cancel=lambda: self._cancel,
                on_progress=lambda s, p=path: self.sized.emit(p, s) if not self._cancel else None,
            )
            if not self._cancel and done:
                self.sized.emit(path, size)


class UpdateCheckWorker(QThread):
    checked = Signal(object)  # updater.ReleaseInfo
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        _size_workers.add(self)
        self.finished.connect(lambda: _size_workers.discard(self))

    def cancel(self) -> None:
        pass

    def run(self) -> None:
        try:
            info = updater.fetch_latest_release()
            self.checked.emit(info)
        except (OSError, ValueError, RuntimeError) as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class UpdateDownloadWorker(QThread):
    progress = Signal(object, object)  # downloaded_bytes, total_bytes
    staged = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, info: updater.ReleaseInfo, parent=None):
        super().__init__(parent)
        self._info = info
        self._cancel = False
        _size_workers.add(self)
        self.finished.connect(lambda: _size_workers.discard(self))

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = updater.download_and_stage(
                self._info,
                progress=lambda done, total: self.progress.emit(done, total),
                cancel=lambda: self._cancel,
            )
            self.staged.emit(path)
        except updater.UpdateCancelled:
            pass
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


atexit.register(DirSizeWorker.stop_all)
