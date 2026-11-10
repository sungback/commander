"""UI-independent batch rename."""
import os
import re
import time
import uuid
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from . import ops


def _same_file(a: Path, b: Path) -> bool:
    with suppress(OSError):
        return os.path.samestat(os.lstat(a), os.lstat(b))
    return False


CASES = ("", "lower", "upper", "title")
_TOKEN = re.compile(r"\[(N|E|C|P|YMD|hms)\]")


def _validate(pairs: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Reject invalid names, duplicate targets and collisions with files outside the batch."""
    srcs = {src for src, _ in pairs}
    seen = set()
    for _src, dst in pairs:
        if not ops.is_valid_name(dst.name):
            raise ValueError(f"invalid name: {dst.name!r}")
        key = ops.norm_key(dst)
        if dst in seen or key in seen:
            raise ValueError(f"duplicate target: {dst.name}")
        seen.add(dst)
        seen.add(key)
        if (dst.exists() or dst.is_symlink()) and dst not in srcs and not any(_same_file(dst, s) for s in srcs):
            raise FileExistsError(dst)
    return pairs


def _substituter(find: str, replace: str, regex: bool):
    if not find:
        return lambda name: name
    try:
        pat = re.compile(find if regex else re.escape(find))
    except re.error as e:
        raise ValueError(f"invalid pattern: {e}") from e
    repl = replace if regex else replace.replace("\\", "\\\\")

    def sub(name: str) -> str:
        try:
            return pat.sub(repl, name)
        except (re.error, IndexError) as e:
            raise ValueError(f"invalid replacement: {e}") from e
    return sub


def plan(paths: Iterable[Path], find: str, replace: str, regex: bool = False) -> list[tuple[Path, Path]]:
    """Return (src, dst) for each path whose name changes. Raises ValueError on a bad plan.

    Rejects invalid names, duplicate targets and targets that collide with an
    existing file outside the batch.
    """
    if not find:
        raise ValueError("search text is empty")
    return plan_names(paths, find=find, replace=replace, regex=regex)


def _split(path: Path) -> tuple[str, str]:
    """(stem, extension without dot); folders and dot-files have no extension."""
    if path.is_dir() and not path.is_symlink():
        return path.name, ""
    return path.stem, path.suffix[1:]


def _expand(mask: str, path: Path, counter: str) -> str:
    stem, ext = _split(path)

    def token(m: re.Match) -> str:
        key = m.group(1)
        if key == "N":
            return stem
        if key == "E":
            return ext
        if key == "C":
            return counter
        if key == "P":
            return path.parent.name
        try:
            stamp = time.localtime(path.lstat().st_mtime)
        except OSError:
            return ""
        return time.strftime("%Y%m%d" if key == "YMD" else "%H%M%S", stamp)

    return _TOKEN.sub(token, mask)


def _recase(text: str, case: str) -> str:
    if case == "lower":
        return text.lower()
    if case == "upper":
        return text.upper()
    if case == "title":
        return text.title()
    return text


def plan_names(paths: Iterable[Path], name_mask: str = "[N]", ext_mask: str = "[E]",
               find: str = "", replace: str = "", regex: bool = False, case: str = "",
               start: int = 1, step: int = 1, digits: int = 1) -> list[tuple[Path, Path]]:
    """Multi-rename plan: masks, then find/replace on the whole name, then letter case.

    Mask tokens: [N] name, [E] extension, [C] counter (start/step, zero-padded to `digits`),
    [P] parent folder name, [YMD] / [hms] modification date / time. The case option changes
    the name part only (the extension is lower/upper-cased with it, never title-cased).
    Returns only the paths whose name actually changes; raises ValueError on a bad plan.
    """
    if case not in CASES:
        raise ValueError(f"unknown case option: {case}")
    sub = _substituter(find, replace, regex)
    pairs = []
    for n, src in enumerate(map(Path, paths)):
        counter = f"{start + n * step:0{max(1, digits)}d}"
        name = _expand(name_mask, src, counter)
        ext = _expand(ext_mask, src, counter)
        new = sub(f"{name}.{ext}" if ext else name)
        if case:
            head, dot, tail = new.rpartition(".") if ext else (new, "", "")
            if dot and head:
                new = _recase(head, case) + "." + _recase(tail, "lower" if case == "title" else case)
            else:
                new = _recase(new, case)
        if new != src.name:
            pairs.append((src, src.with_name(new)))
    return _validate(pairs)


def apply(pairs: list[tuple[Path, Path]]) -> None:
    """Rename via temporary names so swaps and chains (a->b, b->c) work."""
    tmp = []
    for src, dst in pairs:
        t = src.with_name(f".{uuid.uuid4().hex}.tmp")
        src.rename(t)
        tmp.append((t, dst))
    for t, dst in tmp:
        t.rename(dst)
