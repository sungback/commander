"""Local copy/move engine with byte progress, pause/cancel and folder merging (UI-independent)."""
import os
import shutil
import stat
import threading
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import ops

CHUNK = 1 << 20
PART_SUFFIX = ".commander-part"


class Cancelled(Exception):
    pass


class Control:
    """Pause / resume / cancel switch shared between a running transfer and its owner."""

    def __init__(self):
        self._cancel = False
        self._run = threading.Event()
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def pause(self) -> None:
        if not self._cancel:
            self._run.clear()

    def resume(self) -> None:
        self._run.set()

    def cancel(self) -> None:
        self._cancel = True
        self._run.set()  # wake a paused transfer so it can stop

    def checkpoint(self) -> None:
        """Block while paused; raise Cancelled once cancelled."""
        self._run.wait()
        if self._cancel:
            raise Cancelled


@dataclass
class Stats:
    """Progress counters, written by the worker thread and polled by the UI."""

    total_bytes: int = 0
    total_items: int = 0
    done_bytes: int = 0
    done_items: int = 0
    current: str = ""
    scanning: bool = False


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def measure(paths: Iterable[Path], control: Control | None = None) -> tuple[int, int]:
    """(bytes, files) under `paths`, without following symlinks. Unreadable entries count as 0."""
    total = files = 0
    stack = [os.fspath(p) for p in paths]
    while stack:
        if control:
            control.checkpoint()
        cur = stack.pop()
        try:
            st = os.lstat(cur)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            with suppress(OSError), os.scandir(cur) as it:
                stack.extend(e.path for e in it)
        else:
            files += 1
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total, files


def transfer(srcs: Iterable[Path], dst_dir: Path, *, move: bool = False,
             on_conflict: ops.OnConflict | None = None, on_error: ops.OnError | None = None,
             stats: Stats | None = None, control: Control | None = None) -> None:
    """Copy (or move) `srcs` into `dst_dir`.

    An existing destination folder is merged into rather than replaced; `on_conflict(src, dst)`
    is asked per conflicting file and answers "overwrite", "skip" or "rename" (anything else, or
    no callback, raises FileExistsError). With `on_error`, a failing file is reported and the
    rest continue. Files are written to a temporary sibling and renamed into place, so a
    cancelled or failed copy never leaves a truncated file under the real name.
    """
    srcs = [Path(s) for s in srcs]
    dst_dir = Path(dst_dir)
    stats = stats or Stats()
    control = control or Control()
    stats.scanning = True
    stats.total_bytes, stats.total_items = measure(srcs, control)
    stats.scanning = False
    run = _Run(move, on_conflict, on_error, stats, control)
    for src in srcs:
        control.checkpoint()
        try:
            ops._check(src, dst_dir)
            run.place(src, dst_dir / src.name)
        except (OSError, ValueError, shutil.Error) as e:
            if on_error is None:
                raise
            on_error(src, e)
        finally:
            ops.invalidate_size_cache(dst_dir / src.name)
            if move:
                ops.invalidate_size_cache(src)


class _Run:
    def __init__(self, move: bool, on_conflict, on_error, stats: Stats, control: Control):
        self.move = move
        self.on_conflict = on_conflict
        self.on_error = on_error
        self.stats = stats
        self.control = control

    def _account(self, path: Path) -> None:
        """Count a skipped / renamed-in-place / failed subtree as processed."""
        size, files = measure([path])
        self.stats.done_bytes += size
        self.stats.done_items += files

    def _resolve(self, src: Path, dst: Path) -> Path | None:
        """Final destination for `src` when `dst` already exists; None means skip."""
        choice = self.on_conflict(src, dst) if self.on_conflict else ""
        if choice == "skip":
            return None
        if choice == "rename":
            return ops.unique_name(dst)
        if choice != "overwrite":
            raise FileExistsError(dst)
        if src.exists() and dst.exists() and os.path.samefile(src, dst):
            raise ValueError(f"source and destination are the same: {src}")
        if _is_real_dir(dst) or _is_real_dir(src):
            ops.remove(dst)  # type change (file <-> folder): the old item has to go first
        return dst

    def place(self, src: Path, dst: Path) -> None:
        self.control.checkpoint()
        self.stats.current = src.name
        src_is_dir = _is_real_dir(src)
        exists = dst.exists() or dst.is_symlink()
        if exists and not (src_is_dir and _is_real_dir(dst)):
            before = (self.stats.done_bytes, self.stats.done_items)
            try:
                target = self._resolve(src, dst)
            except BaseException:
                self.stats.done_bytes, self.stats.done_items = before
                self._account(src)
                raise
            if target is None:
                self._account(src)
                return
            dst = target
            exists = False
        if src_is_dir:
            self._place_dir(src, dst, merge=exists)
        else:
            self._place_file(src, dst)

    def _place_dir(self, src: Path, dst: Path, merge: bool) -> None:
        if merge and os.path.samefile(src, dst):
            raise ValueError(f"source and destination are the same: {src}")
        if self.move and not merge:
            size, files = measure([src])
            try:
                os.rename(src, dst)  # same volume: instant
            except OSError:
                pass
            else:
                self.stats.done_bytes += size
                self.stats.done_items += files
                return
        if not merge:
            dst.mkdir()
        with os.scandir(src) as it:
            children = sorted(e.name for e in it)
        for name in children:
            try:
                self.place(src / name, dst / name)
            except (OSError, ValueError, shutil.Error) as e:
                if self.on_error is None:
                    raise
                self.on_error(src / name, e)
        if not merge:
            with suppress(OSError):
                shutil.copystat(src, dst, follow_symlinks=False)
        if self.move:
            with suppress(OSError):
                src.rmdir()  # stays when skipped or failed items are left inside

    def _place_file(self, src: Path, dst: Path) -> None:
        st = src.lstat()
        size = st.st_size if stat.S_ISREG(st.st_mode) else 0
        start = self.stats.done_bytes
        try:
            if not (self.move and self._rename(src, dst)):
                self._copy_file(src, dst, st)
                if self.move:
                    src.unlink()
        finally:
            self.stats.done_bytes = start + size
            self.stats.done_items += 1

    @staticmethod
    def _rename(src: Path, dst: Path) -> bool:
        try:
            os.replace(src, dst)
        except OSError:
            return False  # other volume (or not permitted): fall back to copy + delete
        return True

    def _copy_file(self, src: Path, dst: Path, st: os.stat_result) -> None:
        if stat.S_ISLNK(st.st_mode):
            link = os.readlink(src)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            os.symlink(link, dst)
            return
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"unsupported file type: {src}")
        part = dst.with_name(dst.name + PART_SUFFIX)
        try:
            with open(src, "rb") as fin, open(part, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    fout.write(chunk)
                    self.stats.done_bytes += len(chunk)
                    self.control.checkpoint()
            shutil.copystat(src, part, follow_symlinks=False)
            os.replace(part, dst)
        except BaseException:
            with suppress(OSError):
                part.unlink()
            raise
