import pytest

from commander import ops


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "d").mkdir(parents=True)
    dst.mkdir()
    (src / "a.txt").write_text("a")
    (src / "d" / "b.txt").write_text("b")
    return src, dst


def test_copy_file_and_dir(tree):
    src, dst = tree
    seen = []
    ops.copy([src / "a.txt", src / "d"], dst, progress=seen.append)
    assert (dst / "a.txt").read_text() == "a"
    assert (dst / "d" / "b.txt").read_text() == "b"
    assert (src / "a.txt").exists()
    assert len(seen) == 2


def test_copy_conflict_and_overwrite(tree):
    src, dst = tree
    (dst / "a.txt").write_text("old")
    with pytest.raises(FileExistsError):
        ops.copy([src / "a.txt"], dst)
    assert (dst / "a.txt").read_text() == "old"
    ops.copy([src / "a.txt"], dst, overwrite=True)
    assert (dst / "a.txt").read_text() == "a"


def test_move(tree):
    src, dst = tree
    ops.move([src / "a.txt", src / "d"], dst)
    assert not (src / "a.txt").exists() and not (src / "d").exists()
    assert (dst / "d" / "b.txt").exists()


def test_dir_into_itself_rejected(tree):
    src, _ = tree
    with pytest.raises(ValueError):
        ops.copy([src], src / "d")
    with pytest.raises(ValueError):
        ops.move([src], src / "d")
    assert (src / "d" / "b.txt").exists()


def test_delete(tree):
    src, _ = tree
    ops.delete([src / "a.txt", src / "d"])
    assert list(src.iterdir()) == []


def test_delete_symlink_keeps_target(tree):
    src, _ = tree
    link = src / "link"
    link.symlink_to(src / "d", target_is_directory=True)
    ops.delete([link])
    assert not link.is_symlink() and (src / "d" / "b.txt").exists()


def test_make_dir(tmp_path):
    p = ops.make_dir(tmp_path, "new")
    assert p.is_dir()
    for bad in ["", "a/b", "a\\b", "..", "CON", "nul.txt", "bad:name", "trail.", "trail "]:
        with pytest.raises(ValueError):
            ops.make_dir(tmp_path, bad)
    with pytest.raises(FileExistsError):
        ops.make_dir(tmp_path, "new")


def test_dir_size_and_format(tree, tmp_path):
    src, _ = tree
    (src / "link").symlink_to(src / "d", target_is_directory=True)  # symlinks must not be double-counted
    assert ops.dir_size(src) == (2, True)
    # Bottom-up subtree caching populates both root and child directory sizes
    assert ops.get_cached_size(str(src)) == 2
    assert ops.get_cached_size(src.as_posix()) == 2
    assert ops.get_cached_size(str(src / "d")) == 1
    cache_file = tmp_path / "sizes.json"
    ops.save_size_cache(cache_file)
    assert cache_file.is_file()
    # Mutating a child invalidates both the child and its ancestor cache entries
    ops.remove(src / "d" / "b.txt")
    assert ops.get_cached_size(str(src)) is None
    assert ops.get_cached_size(str(src / "d")) is None
    assert ops.format_size(0) == "0 B"
    assert ops.format_size(512) == "512 B"
    assert ops.format_size(1536) == "1.5 KB"
    assert ops.format_size(1024 * 1024) == "1.0 MB"


def test_same_file_is_never_overwritten(tree):
    src, _dst = tree
    with pytest.raises(ValueError):
        ops.copy([src / "a.txt"], src, overwrite=True)
    assert (src / "a.txt").read_text() == "a"


def test_conflict_policy_skip_rename_overwrite(tree):
    src, dst = tree
    (dst / "a.txt").write_text("old")
    (dst / "d").mkdir()
    ops.copy([src / "a.txt"], dst, on_conflict=lambda s, d: "skip")
    assert (dst / "a.txt").read_text() == "old"
    ops.copy([src / "a.txt", src / "d"], dst, on_conflict=lambda s, d: "rename")
    assert (dst / "a.txt").read_text() == "old"
    assert (dst / "a (2).txt").read_text() == "a"
    assert (dst / "d (2)" / "b.txt").read_text() == "b"
    ops.copy([src / "a.txt"], dst, on_conflict=lambda s, d: "rename")
    assert (dst / "a (3).txt").exists()
    ops.move([src / "a.txt"], dst, on_conflict=lambda s, d: "overwrite")
    assert (dst / "a.txt").read_text() == "a" and not (src / "a.txt").exists()


def test_on_error_continues_with_remaining_items(tree):
    src, dst = tree
    errors, seen = [], []
    ops.copy([src / "missing.txt", src / "a.txt"], dst, progress=seen.append,
             on_error=lambda p, e: errors.append((p.name, type(e).__name__)))
    assert errors == [("missing.txt", "FileNotFoundError")]
    assert (dst / "a.txt").exists() and len(seen) == 2
    errors.clear()
    ops.delete([src / "missing.txt", src / "a.txt"], on_error=lambda p, e: errors.append(p.name))
    assert errors == ["missing.txt"] and not (src / "a.txt").exists()
    with pytest.raises(FileNotFoundError):
        ops.delete([src / "missing.txt"])


def test_trash_uses_trasher_and_reports_failures(tree):
    src, _dst = tree
    sent, errors = [], []

    def trasher(p):
        sent.append(p.name)
        return p.name != "d"

    ops.trash([src / "a.txt", src / "d", src / "nope"], trasher=trasher,
              on_error=lambda p, e: errors.append(p.name))
    assert sent == ["a.txt", "d"] and errors == ["d", "nope"]
    assert (src / "a.txt").exists()  # the fake trasher moves nothing
    with pytest.raises(OSError):
        ops.trash([src / "d"], trasher=lambda p: False)
