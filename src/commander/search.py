"""UI-independent recursive file search by name pattern."""
import fnmatch
import os
from collections.abc import Callable, Iterator
from pathlib import Path


def normalize(pattern: str) -> str:
    """A pattern without glob characters matches as a substring."""
    pattern = pattern.strip()
    return pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"


def find(root: Path, pattern: str, cancelled: Callable[[], bool] = lambda: False,
         limit: int | None = None) -> Iterator[Path]:
    """Yield files and folders under root whose name matches the glob (case-insensitive).

    Symlinked folders are not followed; unreadable folders are skipped.
    Stops early when cancelled() is true or after `limit` matches.
    """
    pat = normalize(pattern).lower()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            if cancelled():
                return
            if fnmatch.fnmatchcase(name.lower(), pat):
                yield Path(dirpath) / name
                count += 1
                if limit is not None and count >= limit:
                    return
        if cancelled():
            return
