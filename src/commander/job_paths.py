"""Conservative overlap checks for local paths used by background jobs."""

import os
from collections.abc import Iterable
from pathlib import Path


def _canonical(path: Path) -> str:
    # Resolve existing symlinks even when the leaf does not exist yet.  This also
    # makes a future output underneath a symlink comparable to its real location.
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _overlap(a: Path, b: Path) -> bool:
    left, right = _canonical(a), _canonical(b)
    try:
        common = os.path.commonpath((left, right))
    except ValueError:  # Different Windows drives.
        return False
    if common == left or common == right:
        return True
    # Distinct directory entries can name the same existing inode (hard links).
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def conflicting_paths(
    reads_a: Iterable[Path], writes_a: Iterable[Path],
    reads_b: Iterable[Path], writes_b: Iterable[Path],
) -> tuple[Path, Path] | None:
    """Return one overlapping pair when at least one job writes; else None.

    A path represents the whole subtree, even if it does not yet exist. Callers
    should identify actual output paths rather than a shared destination folder.
    """
    reads_a, writes_a = tuple(map(Path, reads_a)), tuple(map(Path, writes_a))
    reads_b, writes_b = tuple(map(Path, reads_b)), tuple(map(Path, writes_b))
    for left, right in ((writes_a, reads_b + writes_b), (reads_a, writes_b)):
        for a in left:
            for b in right:
                if _overlap(a, b):
                    return a, b
    return None
