"""Compare folders and text files (UI-independent)."""
import difflib
from dataclasses import dataclass
from pathlib import Path

from .transfer import CHUNK, Control

MTIME_SLOP = 2.0  # seconds; FAT and zip store 2-second timestamps
MAX_DIFF_BYTES = 2_000_000


def same_stamp(a_size: int, a_mtime: float, b_size: int, b_mtime: float) -> bool:
    return a_size == b_size and abs(a_mtime - b_mtime) <= MTIME_SLOP


def files_identical(a: Path, b: Path, control: Control | None = None) -> bool:
    """Byte-for-byte comparison in chunks."""
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            if control:
                control.checkpoint()
            ca, cb = fa.read(CHUNK), fb.read(CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def _entries(folder: Path, include_hidden: bool) -> dict[str, Path]:
    return {p.name: p for p in folder.iterdir()
            if (include_hidden or not p.name.startswith(".")) and not p.is_symlink()}


def differing_names(left: Path, right: Path, include_hidden: bool = True) -> tuple[set[str], set[str]]:
    """Names to mark on each side: entries missing on the other side, and the newer of two files.

    Files with the same size and time are equal; same time but another size marks both.
    Folders present on both sides are not compared (use synchronize for a recursive view).
    """
    l_items, r_items = _entries(left, include_hidden), _entries(right, include_hidden)
    l_mark = set(l_items) - set(r_items)
    r_mark = set(r_items) - set(l_items)
    for name in set(l_items) & set(r_items):
        lp, rp = l_items[name], r_items[name]
        if lp.is_dir() and rp.is_dir():
            continue
        if lp.is_dir() != rp.is_dir():
            l_mark.add(name)
            r_mark.add(name)
            continue
        ls, rs = lp.stat(), rp.stat()
        if same_stamp(ls.st_size, ls.st_mtime, rs.st_size, rs.st_mtime):
            continue
        if ls.st_mtime - rs.st_mtime > MTIME_SLOP:
            l_mark.add(name)
        elif rs.st_mtime - ls.st_mtime > MTIME_SLOP:
            r_mark.add(name)
        else:
            l_mark.add(name)
            r_mark.add(name)
    return l_mark, r_mark


def read_lines(path: Path) -> list[str] | None:
    """Text lines of a file, or None when it is binary or too large to diff line by line."""
    if path.stat().st_size > MAX_DIFF_BYTES:
        return None
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif b"\0" in data:
        return None
    else:
        encodings = ("utf-8-sig", "cp949")
    for enc in encodings:
        try:
            return data.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return None


@dataclass(frozen=True)
class Row:
    """One aligned line pair; a side is None where the other side has an extra line."""

    left: str | None
    right: str | None
    changed: bool
    left_no: int = 0
    right_no: int = 0


def side_by_side(a: list[str], b: list[str]) -> list[Row]:
    rows: list[Row] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            rows.extend(Row(a[i1 + k], b[j1 + k], False, i1 + k + 1, j1 + k + 1)
                        for k in range(i2 - i1))
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            li, rj = i1 + k, j1 + k
            rows.append(Row(a[li] if li < i2 else None, b[rj] if rj < j2 else None, True,
                            li + 1 if li < i2 else 0, rj + 1 if rj < j2 else 0))
    return rows


def change_blocks(rows: list[Row]) -> list[int]:
    """Row index where each run of changed rows starts."""
    return [i for i, r in enumerate(rows) if r.changed and (i == 0 or not rows[i - 1].changed)]
