"""UI-independent recursive file search by name pattern."""
import fnmatch
import os
from collections.abc import Callable, Iterator
from pathlib import Path


def normalize(pattern: str) -> str:
    """A pattern without glob characters matches as a substring."""
    pattern = pattern.strip()
    return pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"


_CHUNK = 1 << 20


def contains(path: Path, text: str, cancelled: Callable[[], bool] = lambda: False) -> bool:
    """True if the file holds `text` (ASCII-case-insensitive; UTF-8 or UTF-16 encoded).

    Reads in chunks, so large files never load into memory. Unreadable files do not match.
    """
    needles = {text.encode(enc).lower() for enc in ("utf-8", "utf-16-le", "utf-16-be")}
    keep = max(len(n) for n in needles) - 1
    tail = b""
    try:
        with open(path, "rb") as f:
            while chunk := f.read(_CHUNK):
                if cancelled():
                    return False
                data = tail + chunk.lower()
                if any(n in data for n in needles):
                    return True
                tail = data[-keep:] if keep else b""
    except OSError:
        return False
    return False


def find(root: Path, pattern: str, cancelled: Callable[[], bool] = lambda: False,
         limit: int | None = None, text: str = "") -> Iterator[Path]:
    """Yield files and folders under root whose name matches the glob (case-insensitive).

    With `text`, only regular files that also contain it are yielded (see `contains`).
    Symlinked folders are not followed; unreadable folders are skipped.
    Stops early when cancelled() is true or after `limit` matches.
    """
    pat = normalize(pattern or "*").lower()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (filenames if text else dirnames + filenames):
            if cancelled():
                return
            if not fnmatch.fnmatchcase(name.lower(), pat):
                continue
            path = Path(dirpath) / name
            if text and not (path.is_file() and contains(path, text, cancelled)):
                continue
            yield path
            count += 1
            if limit is not None and count >= limit:
                return
        if cancelled():
            return
