"""UI-independent file operations."""
import os
import shutil
import time
from collections.abc import Callable, Iterable
from pathlib import Path

Progress = Callable[[Path], None]


def _target(src: Path, dst_dir: Path, overwrite: bool) -> Path:
    dst = dst_dir / src.name
    if dst.exists() or dst.is_symlink():
        if not overwrite:
            raise FileExistsError(dst)
        remove(dst)
    return dst


def _check(src: Path, dst_dir: Path) -> None:
    if not dst_dir.is_dir():
        raise NotADirectoryError(dst_dir)
    if src.is_dir() and not src.is_symlink() and dst_dir.resolve().is_relative_to(src.resolve()):
        raise ValueError(f"cannot place {src} inside itself")


def copy(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None) -> None:
    for src in map(Path, srcs):
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        if progress:
            progress(src)


def move(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None) -> None:
    for src in map(Path, srcs):
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite)
        shutil.move(src, dst)
        if progress:
            progress(src)


def remove(path: Path) -> None:
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def delete(paths: Iterable[Path], progress: Progress | None = None) -> None:
    for p in map(Path, paths):
        remove(p)
        if progress:
            progress(p)


def make_dir(parent: Path, name: str) -> Path:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"invalid folder name: {name!r}")
    path = Path(parent) / name
    path.mkdir()
    return path


def dir_size(path: Path, cancel: Callable[[], bool] | None = None,
             on_progress: Callable[[int], None] | None = None,
             max_dirs: int | None = None) -> tuple[int, bool]:
    """(total_bytes, complete) of regular files under `path` (does not follow symlinks)."""
    total = 0
    dirs = 0
    last = time.monotonic()
    stack = [os.fspath(path)]
    while stack:
        if cancel and cancel():
            return total, False
        if max_dirs is not None and dirs >= max_dirs:
            return total, False
        cur = stack.pop()
        dirs += 1
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if cancel and cancel():
                        return total, False
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            if on_progress and time.monotonic() - last >= 0.15:
                                last = time.monotonic()
                                on_progress(total)
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def format_size(n: int) -> str:
    """Human-readable byte size (e.g. '512 B', '1.5 KB', '3.2 MB')."""
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{int(val)} B" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{n} B"
