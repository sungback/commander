import os
import sys
import threading

import pytest

from commander import ops, transfer
from commander.transfer import Cancelled, Control, Stats


@pytest.fixture
def tree(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "d" / "deep").mkdir(parents=True)
    dst.mkdir()
    (src / "a.txt").write_text("aaa")
    (src / "d" / "b.txt").write_text("bb")
    (src / "d" / "deep" / "c.txt").write_text("c")
    return src, dst


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_copy_reports_bytes_and_keeps_metadata(tree):
    src, dst = tree
    os.utime(src / "a.txt", (1_000_000_000, 1_000_000_000))
    stats = Stats()
    transfer.transfer([src / "a.txt", src / "d"], dst, stats=stats)
    assert _files(dst) == ["a.txt", "d/b.txt", "d/deep/c.txt"]
    assert (stats.total_bytes, stats.total_items) == (6, 3)
    assert (stats.done_bytes, stats.done_items) == (6, 3) and not stats.scanning
    assert (dst / "a.txt").stat().st_mtime == 1_000_000_000
    assert _files(src) == ["a.txt", "d/b.txt", "d/deep/c.txt"]


def test_existing_folder_is_merged_with_per_file_decisions(tree):
    src, dst = tree
    (dst / "d" / "deep").mkdir(parents=True)
    (dst / "d" / "keep.txt").write_text("keep")
    (dst / "d" / "b.txt").write_text("old")
    (dst / "d" / "deep" / "c.txt").write_text("old")
    asked = []

    def decide(s, d):
        asked.append(d.name)
        return {"b.txt": "skip", "c.txt": "overwrite"}[d.name]

    stats = Stats()
    transfer.transfer([src / "d"], dst, on_conflict=decide, stats=stats)
    assert asked == ["b.txt", "c.txt"]  # the folders themselves merge without asking
    assert (dst / "d" / "keep.txt").read_text() == "keep"
    assert (dst / "d" / "b.txt").read_text() == "old"
    assert (dst / "d" / "deep" / "c.txt").read_text() == "c"
    assert stats.done_bytes == stats.total_bytes == 3

    transfer.transfer([src / "d"], dst, on_conflict=lambda s, d: "rename")
    assert (dst / "d" / "b (2).txt").read_text() == "bb"
    with pytest.raises(FileExistsError):
        transfer.transfer([src / "a.txt", src / "a.txt"], dst)


def test_move_merges_and_leaves_skipped_items_behind(tree):
    src, dst = tree
    (dst / "d").mkdir()
    (dst / "d" / "b.txt").write_text("old")
    transfer.transfer([src / "a.txt", src / "d"], dst, move=True, on_conflict=lambda s, d: "skip")
    assert not (src / "a.txt").exists() and (dst / "a.txt").read_text() == "aaa"
    assert (dst / "d" / "deep" / "c.txt").read_text() == "c"
    assert _files(src) == ["d/b.txt"] and (dst / "d" / "b.txt").read_text() == "old"


def test_move_across_volumes_falls_back_to_copy_and_delete(tree, monkeypatch):
    src, dst = tree

    def no_rename(*_):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(transfer.os, "rename", no_rename)
    monkeypatch.setattr(transfer._Run, "_rename", staticmethod(lambda s, d: False))
    stats = Stats()
    transfer.transfer([src / "a.txt", src / "d"], dst, move=True, stats=stats)
    assert _files(dst) == ["a.txt", "d/b.txt", "d/deep/c.txt"]
    assert list(src.iterdir()) == [] and stats.done_bytes == 6


def test_errors_are_reported_and_other_files_continue(tree):
    src, dst = tree
    errors = []
    stats = Stats()
    transfer.transfer([src / "missing", src / "d"], dst, stats=stats,
                      on_error=lambda p, e: errors.append((p.name, type(e).__name__)))
    assert errors == [("missing", "FileNotFoundError")]
    assert _files(dst) == ["d/b.txt", "d/deep/c.txt"]
    with pytest.raises(ValueError):
        transfer.transfer([src / "d"], src / "d" / "deep")
    with pytest.raises(ValueError):  # onto itself
        transfer.transfer([src / "a.txt"], src, on_conflict=lambda s, d: "overwrite")
    assert (src / "a.txt").read_text() == "aaa"


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks and FIFOs")
def test_links_are_copied_as_links_and_special_files_refused(tree):
    src, dst = tree
    (src / "d" / "ghost").symlink_to(src / "nowhere")  # dangling links are copied as links
    os.mkfifo(src / "d" / "pipe")  # special files are refused, never opened
    errors = []
    stats = Stats()
    transfer.transfer([src / "d"], dst, stats=stats,
                      on_error=lambda p, e: errors.append((p.name, type(e).__name__)))
    assert errors == [("pipe", "OSError")]
    assert (dst / "d" / "ghost").is_symlink() and _files(dst) == ["d/b.txt", "d/deep/c.txt"]
    assert stats.done_items == stats.total_items == 4


def test_overwrite_replaces_across_types(tree):
    src, dst = tree
    (dst / "a.txt").mkdir()
    (dst / "a.txt" / "inner").write_text("x")
    (dst / "d").write_text("i am a file")
    transfer.transfer([src / "a.txt", src / "d"], dst, on_conflict=lambda s, d: "overwrite")
    assert (dst / "a.txt").read_text() == "aaa" and (dst / "d" / "b.txt").read_text() == "bb"


def test_cancel_mid_file_leaves_no_partial_file(tree, monkeypatch):
    src, dst = tree
    monkeypatch.setattr(transfer, "CHUNK", 4)
    (src / "big.bin").write_bytes(b"0123456789abcdef")
    (dst / "big.bin").write_text("original")
    control = Control()
    stats = Stats()

    real_checkpoint = control.checkpoint

    def checkpoint():
        if stats.done_bytes >= 4:
            control.cancel()
        real_checkpoint()

    control.checkpoint = checkpoint
    with pytest.raises(Cancelled):
        transfer.transfer([src / "big.bin"], dst, on_conflict=lambda s, d: "overwrite",
                          stats=stats, control=control)
    assert (dst / "big.bin").read_text() == "original"  # untouched until the copy completes
    assert [p.name for p in dst.iterdir()] == ["big.bin"]


def test_pause_blocks_until_resumed(tree):
    src, dst = tree
    control = Control()
    control.pause()
    assert control.paused
    t = threading.Thread(target=transfer.transfer, args=([src / "a.txt"], dst),
                         kwargs={"control": control})
    t.start()
    t.join(0.2)
    assert t.is_alive() and not (dst / "a.txt").exists()
    control.resume()
    t.join(5)
    assert not t.is_alive() and (dst / "a.txt").read_text() == "aaa"
    control.pause()
    control.cancel()  # cancelling wakes a paused transfer
    with pytest.raises(Cancelled):
        control.checkpoint()


def test_size_cache_invalidated(tree):
    src, dst = tree
    ops.set_cached_size(dst, 123)
    transfer.transfer([src / "a.txt"], dst)
    assert ops.get_cached_size(dst) is None
