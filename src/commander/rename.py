"""UI-independent batch rename."""
import re
import uuid
from collections.abc import Iterable
from pathlib import Path


def plan(paths: Iterable[Path], find: str, replace: str, regex: bool = False) -> list[tuple[Path, Path]]:
    """Return (src, dst) for each path whose name changes. Raises ValueError on a bad plan.

    Rejects invalid names, duplicate targets and targets that collide with an
    existing file outside the batch.
    """
    if not find:
        raise ValueError("search text is empty")
    try:
        pat = re.compile(find if regex else re.escape(find))
        pairs = []
        for src in map(Path, paths):
            new = pat.sub(replace if regex else replace.replace("\\", "\\\\"), src.name)
            if new != src.name:
                pairs.append((src, src.with_name(new)))
    except re.error as e:
        raise ValueError(f"invalid pattern: {e}") from e
    srcs = {src for src, _ in pairs}
    seen = set()
    for src, dst in pairs:
        if not dst.name or "/" in dst.name or dst.name in {".", ".."}:
            raise ValueError(f"invalid name: {dst.name!r}")
        if dst in seen:
            raise ValueError(f"duplicate target: {dst.name}")
        seen.add(dst)
        if (dst.exists() or dst.is_symlink()) and dst not in srcs:
            raise FileExistsError(dst)
    return pairs


def apply(pairs: list[tuple[Path, Path]]) -> None:
    """Rename via temporary names so swaps and chains (a->b, b->c) work."""
    tmp = []
    for src, dst in pairs:
        t = src.with_name(f".{uuid.uuid4().hex}.tmp")
        src.rename(t)
        tmp.append((t, dst))
    for t, dst in tmp:
        t.rename(dst)
