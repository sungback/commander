import os

import pytest

from commander import compare, sync
from commander.sync import CONFLICT, DEL_RIGHT, EQUAL, NONE, TO_LEFT, TO_RIGHT, Item

OLD, NEW = 1_600_000_000, 1_700_000_000


def _put(path, text, mtime=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


@pytest.fixture
def pair(tmp_path):
    left, right = tmp_path / "L", tmp_path / "R"
    _put(left / "same.txt", "same")
    _put(right / "same.txt", "same")
    _put(left / "newer_left.txt", "LLLL", NEW)
    _put(right / "newer_left.txt", "r")
    _put(left / "newer_right.txt", "l")
    _put(right / "newer_right.txt", "RRRR", NEW)
    _put(left / "clash.txt", "aa")
    _put(right / "clash.txt", "bbb")  # same time, other size
    _put(left / "only_left.txt", "L")
    _put(right / "only_right.txt", "R")
    _put(left / "sub" / "deep.txt", "deep", NEW)
    _put(right / "sub" / "deep.txt", "old")
    _put(left / "ldir" / "x" / "y.txt", "y")
    _put(left / ".hidden", "h")
    return left, right


def test_differing_names_marks_unique_and_newer(pair):
    left, right = pair
    l_mark, r_mark = compare.differing_names(left, right, include_hidden=False)
    assert l_mark == {"newer_left.txt", "clash.txt", "only_left.txt", "ldir"}
    assert r_mark == {"newer_right.txt", "clash.txt", "only_right.txt"}
    assert ".hidden" in compare.differing_names(left, right)[0]


def _plan(items):
    return {it.rel: it.action for it in items}


def test_scan_both_ways(pair):
    left, right = pair
    assert _plan(sync.scan(left, right, include_hidden=False)) == {
        "clash.txt": CONFLICT, "ldir": TO_RIGHT, "newer_left.txt": TO_RIGHT,
        "newer_right.txt": TO_LEFT, "only_left.txt": TO_RIGHT, "only_right.txt": TO_LEFT,
        "same.txt": EQUAL, "sub/deep.txt": TO_RIGHT}


def test_scan_one_way_and_delete_extra(pair):
    left, right = pair
    plan = _plan(sync.scan(left, right, mode="to_right", include_hidden=False))
    assert plan["newer_right.txt"] == TO_RIGHT and plan["clash.txt"] == TO_RIGHT
    assert plan["only_right.txt"] == NONE and plan["only_left.txt"] == TO_RIGHT
    plan = _plan(sync.scan(left, right, mode="to_right", delete_extra=True))
    assert plan["only_right.txt"] == DEL_RIGHT and plan[".hidden"] == TO_RIGHT
    plan = _plan(sync.scan(left, right, mode="to_left", include_hidden=False))
    assert plan["only_left.txt"] == NONE and plan["newer_left.txt"] == TO_LEFT
    with pytest.raises(ValueError):
        sync.scan(left, right, mode="sideways")


def test_scan_by_content_ignores_timestamps(pair):
    left, right = pair
    _put(left / "touched.txt", "identical", NEW)
    _put(right / "touched.txt", "identical", OLD)
    assert _plan(sync.scan(left, right))["touched.txt"] == TO_RIGHT
    plan = _plan(sync.scan(left, right, by_content=True))
    assert plan["touched.txt"] == EQUAL and plan["same.txt"] == EQUAL
    assert plan["clash.txt"] == CONFLICT and plan["newer_left.txt"] == TO_RIGHT


def test_apply_makes_folders_agree(pair):
    left, right = pair
    items = sync.scan(left, right)
    trashed = []
    sync.apply(items, left, right, trash=lambda paths: trashed.extend(paths))
    assert (right / "newer_left.txt").read_text() == "LLLL"
    assert (left / "newer_right.txt").read_text() == "RRRR"
    assert (right / "ldir" / "x" / "y.txt").read_text() == "y"
    assert (left / "only_right.txt").read_text() == "R" and (right / ".hidden").read_text() == "h"
    assert (right / "sub" / "deep.txt").read_text() == "deep"
    assert (left / "clash.txt").read_text() == "aa" and (right / "clash.txt").read_text() == "bbb"
    assert trashed == []
    again = sync.summarize(sync.scan(left, right))
    assert again[TO_RIGHT] == again[TO_LEFT] == 0 and again[CONFLICT] == 1


def test_apply_mirror_trashes_extras_and_reports_errors(pair):
    left, right = pair
    items = sync.scan(left, right, mode="to_right", delete_extra=True)
    trashed, errors = [], []
    items.append(Item("ghost.txt", None, None, TO_RIGHT))
    items.append(Item("../escape.txt", None, None, TO_RIGHT))
    sync.apply(items, left, right, trash=lambda paths: trashed.extend(paths),
               on_error=lambda p, e: errors.append((p.as_posix(), type(e).__name__)))
    assert trashed == [right / "only_right.txt"]
    assert errors == [("ghost.txt", "FileNotFoundError"), ("../escape.txt", "ValueError")]
    assert (right / "clash.txt").read_text() == "aa"
    assert not (right.parent / "escape.txt").exists()


def test_roots_must_be_separate_folders(pair):
    left, _right = pair
    for other in (left, left / "sub", left.parent):
        with pytest.raises(ValueError):
            sync.scan(left, other)
    with pytest.raises(NotADirectoryError):
        sync.scan(left, left / "same.txt")


def test_files_identical_and_read_lines(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"x" * 10)
    c.write_bytes(b"x" * 9 + b"y")
    assert compare.files_identical(a, b) and not compare.files_identical(a, c)
    (tmp_path / "k.txt").write_bytes("한글\n둘째".encode("cp949"))
    (tmp_path / "u16.txt").write_bytes("a\nb".encode("utf-16"))
    (tmp_path / "bin").write_bytes(b"\0\1\2")
    assert compare.read_lines(tmp_path / "k.txt") == ["한글", "둘째"]
    assert compare.read_lines(tmp_path / "u16.txt") == ["a", "b"]
    assert compare.read_lines(tmp_path / "bin") is None


def test_side_by_side_alignment():
    rows = compare.side_by_side(["a", "b", "c", "d"], ["a", "B", "c", "d", "e"])
    assert [(r.left, r.right, r.changed) for r in rows] == [
        ("a", "a", False), ("b", "B", True), ("c", "c", False), ("d", "d", False),
        (None, "e", True)]
    assert (rows[1].left_no, rows[1].right_no, rows[4].left_no, rows[4].right_no) == (2, 2, 0, 5)
    assert compare.change_blocks(rows) == [1, 4]
    assert compare.side_by_side([], []) == []
    rows = compare.side_by_side(["x", "y", "z"], ["z"])
    assert [(r.left, r.right) for r in rows] == [("x", None), ("y", None), ("z", "z")]
