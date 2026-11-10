"""UI-independent file operations."""
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path

Progress = Callable[[Path], None]

CACHE_FILE = Path.home() / ".config" / "commander" / "dir_sizes.json"
_cache_lock = threading.Lock()
_size_cache: dict[str, int] = {}
_cache_dirty = False
_CACHE_MAX = 5000
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BAD_CHARS = set('<>:"/\\|?*')


def norm_key(path: str | Path) -> str:
    """Canonical path key independent of slash direction ('/' vs '\\')."""
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def is_valid_name(name: str) -> bool:
    """Validate a single file/folder name across POSIX and Windows rules."""
    if not name or name in {".", ".."} or name.endswith((" ", ".")):
        return False
    if any(c in _BAD_CHARS or ord(c) < 32 for c in name):
        return False
    return name.split(".", 1)[0].upper() not in _WIN_RESERVED


def load_size_cache(path: Path | None = None) -> dict[str, int]:
    target = path or CACHE_FILE
    if not target.is_file():
        return _size_cache
    with _cache_lock, suppress(OSError, ValueError, TypeError):
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _size_cache.update({norm_key(str(k)): int(v) for k, v in data.items() if isinstance(v, int)})
    return _size_cache


def save_size_cache(path: Path | None = None) -> None:
    global _cache_dirty
    target = path or CACHE_FILE
    with _cache_lock:
        if not _cache_dirty and path is None:
            return
        if len(_size_cache) > _CACHE_MAX:
            for k in list(_size_cache)[:-_CACHE_MAX]:
                _size_cache.pop(k, None)
        payload = dict(_size_cache)
        _cache_dirty = False
    with suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")


def get_cached_size(path: str | Path) -> int | None:
    return _size_cache.get(norm_key(path))


def set_cached_size(path: str | Path, size: int) -> None:
    global _cache_dirty
    key = norm_key(path)
    with _cache_lock:
        if _size_cache.get(key) != size:
            _size_cache[key] = size
            _cache_dirty = True


def invalidate_size_cache(path: Path) -> None:
    """Invalidate `path`, all its ancestors, and any cached descendants."""
    global _cache_dirty
    p = Path(path).resolve()
    key = norm_key(p)
    prefix = key if key.endswith(os.sep) else key + os.sep
    ancestors = {key, *(norm_key(a) for a in p.parents)}
    with _cache_lock:
        drop = [k for k in _size_cache if k in ancestors or k.startswith(prefix)]
        for k in drop:
            _size_cache.pop(k, None)
        if drop:
            _cache_dirty = True


OnConflict = Callable[[Path, Path], str]  # (src, dst) -> "overwrite" | "skip" | "rename"
OnError = Callable[[Path, Exception], None]


def unique_name(dst: Path) -> Path:
    """First free sibling of `dst`: 'name (2).ext', 'name (3).ext', ..."""
    stem, suffix = (dst.name, "") if dst.is_dir() and not dst.is_symlink() else (dst.stem, dst.suffix)
    n = 2
    while True:
        cand = dst.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists() and not cand.is_symlink():
            return cand
        n += 1


def _target(src: Path, dst_dir: Path, overwrite: bool,
            on_conflict: OnConflict | None = None) -> Path | None:
    """Destination for `src`, or None when the conflict policy says to skip it."""
    dst = dst_dir / src.name
    if dst.exists() or dst.is_symlink():
        choice = on_conflict(src, dst) if on_conflict else ("overwrite" if overwrite else "")
        if choice == "skip":
            return None
        if choice == "rename":
            return unique_name(dst)
        if choice != "overwrite":
            raise FileExistsError(dst)
        if src.exists() and dst.exists() and os.path.samefile(src, dst):
            raise ValueError(f"source and destination are the same: {src}")
        remove(dst)
    return dst


def _check(src: Path, dst_dir: Path) -> None:
    if not dst_dir.is_dir():
        raise NotADirectoryError(dst_dir)
    if src.is_dir() and not src.is_symlink() and dst_dir.resolve().is_relative_to(src.resolve()):
        raise ValueError(f"cannot place {src} inside itself")


def _each(paths: Iterable[Path], action: Callable[[Path], None],
          progress: Progress | None, on_error: OnError | None) -> None:
    """Run `action` per path. With `on_error`, failures are reported and the rest continue."""
    for p in map(Path, paths):
        try:
            action(p)
        except (OSError, ValueError, shutil.Error) as e:
            if on_error is None:
                raise
            on_error(p, e)
        if progress:
            progress(p)


def copy(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None, on_conflict: OnConflict | None = None,
         on_error: OnError | None = None) -> None:
    def one(src: Path) -> None:
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite, on_conflict)
        if dst is None:
            return
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        invalidate_size_cache(dst)

    _each(srcs, one, progress, on_error)


def move(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None, on_conflict: OnConflict | None = None,
         on_error: OnError | None = None) -> None:
    def one(src: Path) -> None:
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite, on_conflict)
        if dst is None:
            return
        shutil.move(src, dst)
        invalidate_size_cache(src)
        invalidate_size_cache(dst)

    _each(srcs, one, progress, on_error)


def remove(path: Path) -> None:
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    invalidate_size_cache(path)


def delete(paths: Iterable[Path], progress: Progress | None = None,
           on_error: OnError | None = None) -> None:
    _each(paths, remove, progress, on_error)


def _move_to_trash(path: Path) -> bool:
    from PySide6.QtCore import QFile  # QtCore only: no GUI dependency

    return QFile.moveToTrash(os.fspath(path))


def trash(paths: Iterable[Path], progress: Progress | None = None,
          on_error: OnError | None = None,
          trasher: Callable[[Path], bool] | None = None) -> None:
    """Move paths to the OS trash / recycle bin (recoverable, unlike `delete`)."""
    send = trasher or _move_to_trash

    def one(p: Path) -> None:
        if not p.exists() and not p.is_symlink():
            raise FileNotFoundError(p)
        if not send(p):
            raise OSError(f"could not move to trash: {p}")
        invalidate_size_cache(p)

    _each(paths, one, progress, on_error)


def make_dir(parent: Path, name: str) -> Path:
    if not is_valid_name(name):
        raise ValueError(f"invalid folder name: {name!r}")
    path = Path(parent) / name
    path.mkdir()
    return path


def dir_size(path: Path, cancel: Callable[[], bool] | None = None,
             on_progress: Callable[[int], None] | None = None,
             max_dirs: int | None = None,
             cache_max_depth: int = 3) -> tuple[int, bool]:
    """(total_bytes, complete) of regular files under `path` (does not follow symlinks).

    When a full scan (`max_dirs is None`) completes without cancellation, also caches
    exact subtree sizes for `path` and its descendants up to `cache_max_depth`.
    """
    root_str = os.fspath(path)
    total = 0
    dirs = 0
    last = time.monotonic()
    post_order: list[tuple[str, int, list[str], int]] = []
    stack: list[tuple[str, int]] = [(root_str, 0)]
    record_subtrees = max_dirs is None
    while stack:
        if cancel and cancel():
            return total, False
        if max_dirs is not None and dirs >= max_dirs:
            return total, False
        cur, depth = stack.pop()
        dirs += 1
        own_files = 0
        children: list[str] = []
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if cancel and cancel():
                        return total, False
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, depth + 1))
                            if record_subtrees and depth < cache_max_depth:
                                children.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            sz = e.stat(follow_symlinks=False).st_size
                            total += sz
                            own_files += sz
                            if on_progress and time.monotonic() - last >= 0.4:
                                last = time.monotonic()
                                on_progress(total)
                    except OSError:
                        continue
        except OSError:
            continue
        if record_subtrees and depth <= cache_max_depth:
            post_order.append((cur, own_files, children, depth))

    if record_subtrees:
        sub_totals: dict[str, int] = {}
        for cur, own_files, children, depth in reversed(post_order):
            if depth == cache_max_depth:
                continue
            if all(c in sub_totals for c in children):
                s = own_files + sum(sub_totals[c] for c in children)
                sub_totals[cur] = s
                set_cached_size(cur, s)
        set_cached_size(root_str, total)
    return total, True


def format_size(n: int) -> str:
    """Human-readable byte size (e.g. '512 B', '1.5 KB', '3.2 MB')."""
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{int(val)} B" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{n} B"
