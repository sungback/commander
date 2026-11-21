"""Find duplicate local files without modifying them."""

import hashlib
import os
import stat
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import ops
from .transfer import Control

Signature = tuple[int, int, int, int, int]
_WINDOWS = os.name == "nt"


@dataclass
class ScanResult:
    groups: list[list[Path]]
    snapshots: dict[Path, tuple[Signature, bytes]]


class DuplicateScanCancelled(Exception):
    """The scan was cancelled; no partial result should be used."""


def _signature(st: os.stat_result) -> Signature:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _checkpoint(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise DuplicateScanCancelled


def _changed(path: Path) -> OSError:
    return OSError(f"file changed while scanning: {path}")


def _matches_opened(path_sig: Signature, opened_sig: Signature) -> bool:
    # CPython on Windows may report creation time in stat().st_ctime_ns but
    # change time in fstat().st_ctime_ns (cpython#157671). Both sources are
    # compared against themselves before and after the read below.
    return path_sig[:4] == opened_sig[:4] if _WINDOWS else path_sig == opened_sig


def _digest(path: Path, expected: Signature,
            cancelled: Callable[[], bool]) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or _signature(before) != expected:
        raise _changed(path)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        opened_signature = _signature(opened)
        if not stat.S_ISREG(opened.st_mode) or not _matches_opened(expected, opened_signature):
            raise _changed(path)
        digest = hashlib.sha256()
        while True:
            _checkpoint(cancelled)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if _signature(os.fstat(stream.fileno())) != opened_signature:
            raise _changed(path)
    after = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(after.st_mode) or _signature(after) != expected:
        raise _changed(path)
    return digest.digest()


def find_duplicates(
    root: Path, *, cancelled: Callable[[], bool] = lambda: False,
    on_error: Callable[[Path, Exception], None] | None = None,
    on_progress: Callable[[Path], None] | None = None,
) -> ScanResult:
    """Group regular files by size and SHA-256; skip links, errors and changed files.

    Scanning is read-only. The results are snapshots, not a guarantee that files will
    remain unchanged after this function returns. No partial results are returned
    on cancellation. Access errors are surfaced through ``on_error`` when supplied.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"not a local directory: {root}")

    def error(path: Path, exc: Exception) -> None:
        if on_error:
            on_error(path, exc)

    def walk_error(exc: OSError) -> None:
        error(Path(exc.filename) if exc.filename else root, exc)

    sizes: dict[int, list[tuple[Path, Signature]]] = defaultdict(list)
    for directory, dirs, names in os.walk(root, onerror=walk_error, followlinks=False):
        _checkpoint(cancelled)
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            _checkpoint(cancelled)
            path = Path(directory) / name
            if on_progress:
                on_progress(path)
            try:
                st = path.stat(follow_symlinks=False)
                if stat.S_ISREG(st.st_mode):
                    sizes[st.st_size].append((path, _signature(st)))
            except OSError as exc:
                error(path, exc)

    hashes: dict[tuple[int, bytes], list[tuple[Path, Signature]]] = defaultdict(list)
    for size, files in sizes.items():
        if len(files) < 2:
            continue
        for path, signature in files:
            _checkpoint(cancelled)
            if on_progress:
                on_progress(path)
            try:
                hashes[(size, _digest(path, signature, cancelled))].append((path, signature))
            except OSError as exc:
                error(path, exc)

    groups = []
    snapshots = {}
    for (_, digest), files in hashes.items():
        unchanged = []
        for path, signature in files:
            _checkpoint(cancelled)
            try:
                st = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode) or _signature(st) != signature:
                    raise _changed(path)
                unchanged.append(path)
            except OSError as exc:
                error(path, exc)
        if len(unchanged) > 1:
            groups.append(sorted(unchanged))
            signatures = dict(files)
            for path in unchanged:
                snapshots[path] = (signatures[path], digest)
    _checkpoint(cancelled)
    return ScanResult(sorted(groups, key=lambda group: str(group[0])), snapshots)


def trash_duplicates(
    result: ScanResult, selected: list[Path], *, control: Control,
    on_error: Callable[[Path, Exception], None],
    progress: Callable[[Path], None] | None = None,
) -> None:
    """Trash selected duplicates only after revalidating an unselected survivor.

    Paths and content can still be changed by another process between the final
    validation and the OS trash operation; the filesystem offers no atomic
    compare-and-trash operation. An entire group may not be selected.
    """
    chosen = set(map(Path, selected))

    def checkpoint() -> bool:
        control.checkpoint()
        return False

    def valid(path: Path) -> bool:
        signature, expected_hash = result.snapshots[path]
        try:
            if _digest(path, signature, checkpoint) != expected_hash:
                raise _changed(path)
            return True
        except OSError as exc:
            on_error(path, exc)
            return False

    known = set(result.snapshots)
    for path in chosen - known:
        on_error(path, ValueError("file is not in the duplicate scan results"))
    for group in result.groups:
        targets = [path for path in group if path in chosen]
        survivors = [path for path in group if path not in chosen]
        for path in targets:
            control.checkpoint()
            if not survivors:
                on_error(path, ValueError("at least one duplicate must be kept"))
                continue
            if not valid(path):
                continue
            if not any(valid(other) for other in survivors):
                on_error(path, ValueError("no unchanged duplicate remains"))
                continue
            ops.trash([path], progress=progress, on_error=on_error)
