"""Local file properties and explicit, symlink-safe attribute changes."""
import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sizes:
    logical: int
    allocated: int | None
    files: int
    directories: int


def _walk(paths: Iterable[Path], recursive: bool, on_error: Callable | None = None):
    """Walk without following links, deduplicating overlapping selections."""
    seen: set[str] = set()
    stack = [Path(p) for p in paths]
    while stack:
        path = stack.pop()
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        try:
            info = path.lstat()
            yield path, info
            if recursive and stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
        except OSError as exc:
            if on_error is None:
                raise
            on_error(path, exc)


def calculate_sizes(paths: Iterable[Path], *, control=None, on_error=None) -> Sizes:
    """Sum logical file bytes and allocated disk blocks; never follow symlinks.

    An unavailable allocated count is returned as None. Hard-linked names are
    counted individually, consistent with the existing directory size function.
    """
    logical = allocated = files = directories = 0
    allocation_known = True
    for path, info in _walk(paths, True, on_error):
        if control is not None:
            control.checkpoint()
        if stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_ISDIR(info.st_mode):
            directories += 1
        elif stat.S_ISREG(info.st_mode):
            files += 1
            logical += info.st_size
        else:
            continue
        blocks = getattr(info, 'st_blocks', None)
        if blocks is None:
            allocation_known = False
        else:
            allocated += blocks * 512
    return Sizes(logical, allocated if allocation_known else None, files, directories)


def change_attributes(paths: Iterable[Path], *, readonly: bool | None = None,
                      modified: float | None = None, recursive: bool = False,
                      control=None, on_error=None) -> int:
    """Apply only requested fields; report individual failures and skip links.

    On POSIX read-only clears write bits; allowing writes adds the owner bit only.
    Windows chmod supports its read-only flag only. Symlinks are never modified.
    """
    if readonly is None and modified is None:
        return 0
    changed = 0
    for path, info in _walk(paths, recursive, on_error):
        if control is not None:
            control.checkpoint()
        try:
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('심볼릭 링크의 속성은 변경하지 않습니다')
            # Recheck before modification; never intentionally follow a link.
            if path.is_symlink():
                raise ValueError('심볼릭 링크의 속성은 변경하지 않습니다')
            if readonly is not None:
                mode = info.st_mode & ~0o222 if readonly else info.st_mode | stat.S_IWUSR
                os.chmod(path, mode, follow_symlinks=False)
            if modified is not None:
                os.utime(path, (info.st_atime, modified), follow_symlinks=False)
            changed += 1
        except (OSError, ValueError, NotImplementedError) as exc:
            if on_error is None:
                raise
            on_error(path, exc)
    return changed
