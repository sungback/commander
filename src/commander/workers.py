"""Background execution of ops functions with progress and cancel."""
import atexit
import shutil
import subprocess
import threading
import zipfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import ops, search, transfer, updater

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


class Job(QThread):
    """A queued background operation: progress is polled from `stats`, never signalled.

    `run_fn(job)` does the work on this thread and uses the job as its callbacks:
    `on_item` (per-item progress), `on_error` (collect and continue) and `ask` (conflict
    question answered by the UI thread through `answer`).
    """

    conflict = Signal(object, str, str)  # job, source path, destination path

    def __init__(self, title: str, run_fn: Callable[["Job"], None], total_items: int = 0,
                 parent=None):
        super().__init__(parent)
        self.title = title
        self.stats = transfer.Stats(total_items=total_items)
        self.control = transfer.Control()
        self.errors: list[str] = []
        self.waiting = False  # blocked on a conflict question
        self.result = None  # whatever run_fn returns, for the UI's completion callback
        self._run_fn = run_fn
        self._answered = threading.Event()
        self._choice = ""
        self._sticky = ""
        _size_workers.add(self)
        self.finished.connect(lambda: _size_workers.discard(self))

    @property
    def cancelled(self) -> bool:
        return self.control.cancelled

    def pause(self) -> None:
        self.control.pause()

    def resume(self) -> None:
        self.control.resume()

    def cancel(self) -> None:
        self.control.cancel()
        self._answered.set()

    def on_item(self, path: Path) -> None:
        self.stats.done_items += 1
        self.stats.current = Path(path).name
        self.control.checkpoint()

    def on_error(self, path: Path, e: Exception) -> None:
        self.errors.append(f"{Path(path).name}: {type(e).__name__}: {e}")

    def ask(self, src: Path, dst: Path) -> str:
        """Called on the worker thread; blocks until the UI answers (or the job is cancelled)."""
        if self._sticky:
            return self._sticky
        self._answered.clear()
        self.waiting = True
        self.conflict.emit(self, str(src), str(dst))
        self._answered.wait()
        self.waiting = False
        self.control.checkpoint()
        return self._choice

    def answer(self, choice: str | None, apply_all: bool = False) -> None:
        """UI-thread reply to `conflict`; None cancels the whole job."""
        if choice is None:
            self.cancel()
            return
        self._choice = choice
        if apply_all:
            self._sticky = choice
        self._answered.set()

    def run(self) -> None:
        try:
            self.result = self._run_fn(self)
        except (Cancelled, transfer.Cancelled):
            pass
        except (OSError, ValueError, shutil.Error) as e:
            self.errors.append(f"{type(e).__name__}: {e}")


class SearchWorker(QThread):
    found = Signal(str)

    LIMIT = 5000

    def __init__(self, root: Path, pattern: str, parent=None, text: str = ""):
        super().__init__(parent)
        self._root = root
        self._pattern = pattern
        self._text = text
        self._cancel = False
        self.truncated = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        count = 0
        for path in search.find(self._root, self._pattern, lambda: self._cancel, self.LIMIT,
                                text=self._text):
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
            with suppress(RuntimeError):  # the C++ thread object may already be gone
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
