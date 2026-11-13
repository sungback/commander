"""Folder synchronization: build a reviewable plan, then apply it (UI-independent)."""
import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import ops
from .compare import MTIME_SLOP, files_identical, same_stamp
from .transfer import Control, Stats, _Run, measure

TO_RIGHT, TO_LEFT, EQUAL, CONFLICT, DEL_LEFT, DEL_RIGHT, NONE = ">", "<", "=", "!", "x<", "x>", "-"
MODES = ("both", "to_right", "to_left")


@dataclass
class Info:
    is_dir: bool
    size: int
    mtime: float


@dataclass
class Item:
    rel: str  # POSIX-style path relative to both roots
    left: Info | None
    right: Info | None
    action: str


def check_roots(left: Path, right: Path) -> None:
    left, right = Path(left).resolve(), Path(right).resolve()
    if not left.is_dir() or not right.is_dir():
        raise NotADirectoryError(left if not left.is_dir() else right)
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError("cannot synchronize a folder with itself or with a folder inside it")


def _listing(folder: Path, include_hidden: bool) -> dict[str, Info]:
    out: dict[str, Info] = {}
    with os.scandir(folder) as it:
        for e in it:
            if not include_hidden and e.name.startswith("."):
                continue
            st = e.stat(follow_symlinks=False)
            is_dir = stat.S_ISDIR(st.st_mode)
            if not is_dir and not stat.S_ISREG(st.st_mode):
                continue  # symlinks, sockets, FIFOs, devices
            out[e.name] = Info(is_dir, 0 if is_dir else st.st_size, st.st_mtime)
    return out


def _one_sided(mode: str, on_left: bool, delete_extra: bool) -> str:
    if mode == "both":
        return TO_RIGHT if on_left else TO_LEFT
    source_is_left = mode == "to_right"
    if on_left == source_is_left:
        return TO_RIGHT if on_left else TO_LEFT
    if not delete_extra:
        return NONE
    return DEL_LEFT if on_left else DEL_RIGHT


def scan(left: Path, right: Path, mode: str = "both", delete_extra: bool = False,
         by_content: bool = False, include_hidden: bool = True,
         control: Control | None = None) -> list[Item]:
    """Plan to make `right` and `left` agree.

    mode "both" copies missing files each way and lets the newer file win; "to_right" /
    "to_left" make the target match the source (optionally deleting what only the target has).
    A folder that exists on one side only is a single item. Symlinks are ignored.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    left, right = Path(left), Path(right)
    check_roots(left, right)
    items: list[Item] = []
    stack = [""]
    while stack:
        if control:
            control.checkpoint()
        rel_dir = stack.pop()
        l_items = _listing(left / rel_dir, include_hidden)
        r_items = _listing(right / rel_dir, include_hidden)
        subdirs = []
        for name in sorted(set(l_items) | set(r_items), key=str.lower):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            li, ri = l_items.get(name), r_items.get(name)
            if li is None or ri is None:
                items.append(Item(rel, li, ri, _one_sided(mode, li is not None, delete_extra)))
            elif li.is_dir and ri.is_dir:
                subdirs.append(rel)
            elif li.is_dir != ri.is_dir:
                items.append(Item(rel, li, ri, CONFLICT))
            else:
                items.append(Item(rel, li, ri, _file_action(left / rel, right / rel, li, ri,
                                                            mode, by_content, control)))
        stack.extend(reversed(subdirs))
    return items


def _file_action(lp: Path, rp: Path, li: Info, ri: Info, mode: str, by_content: bool,
                 control: Control | None) -> str:
    if by_content:
        try:
            equal = li.size == ri.size and files_identical(lp, rp, control)
        except OSError:
            return CONFLICT
    else:
        equal = same_stamp(li.size, li.mtime, ri.size, ri.mtime)
    if equal:
        return EQUAL
    if mode == "to_right":
        return TO_RIGHT
    if mode == "to_left":
        return TO_LEFT
    if li.mtime - ri.mtime > MTIME_SLOP:
        return TO_RIGHT
    if ri.mtime - li.mtime > MTIME_SLOP:
        return TO_LEFT
    return CONFLICT  # same time, different content: the user has to pick a side


def summarize(items: Iterable[Item]) -> dict[str, int]:
    counts = dict.fromkeys((TO_RIGHT, TO_LEFT, EQUAL, CONFLICT, DEL_LEFT, DEL_RIGHT, NONE), 0)
    for it in items:
        counts[it.action] += 1
    return counts


def apply(items: Iterable[Item], left: Path, right: Path, stats: Stats | None = None,
          control: Control | None = None, on_error: ops.OnError | None = None,
          trash: Callable[..., None] = ops.trash) -> None:
    """Carry out the copy/delete actions of `items` (others are ignored). Deletions go to the trash."""
    left, right = Path(left), Path(right)
    check_roots(left, right)
    stats = stats or Stats()
    control = control or Control()
    todo = [it for it in items if it.action in (TO_RIGHT, TO_LEFT, DEL_LEFT, DEL_RIGHT)]
    sources = [(left if it.action == TO_RIGHT else right) / it.rel
               for it in todo if it.action in (TO_RIGHT, TO_LEFT)]
    stats.scanning = True
    stats.total_bytes, stats.total_items = measure(sources, control)
    stats.scanning = False
    run = _Run(False, lambda s, d: "overwrite", on_error, stats, control)
    for it in todo:
        control.checkpoint()
        stats.current = it.rel
        try:
            if any(part in ("", ".", "..") for part in it.rel.split("/")):
                raise ValueError(f"unsafe relative path: {it.rel}")
            if it.action in (DEL_LEFT, DEL_RIGHT):
                trash([(left if it.action == DEL_LEFT else right) / it.rel])
                continue
            src_root, dst_root = (left, right) if it.action == TO_RIGHT else (right, left)
            dst = dst_root / it.rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            run.place(src_root / it.rel, dst)
        except (OSError, ValueError) as e:
            if on_error is None:
                raise
            on_error(Path(it.rel), e)
    ops.invalidate_size_cache(left)
    ops.invalidate_size_cache(right)
