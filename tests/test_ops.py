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
    link.symlink_to(src / "d")
    ops.delete([link])
    assert not link.is_symlink() and (src / "d" / "b.txt").exists()


def test_make_dir(tmp_path):
    p = ops.make_dir(tmp_path, "new")
    assert p.is_dir()
    for bad in ["", "a/b", ".."]:
        with pytest.raises(ValueError):
            ops.make_dir(tmp_path, bad)
    with pytest.raises(FileExistsError):
        ops.make_dir(tmp_path, "new")


def test_dir_size_and_format(tree):
    src, _ = tree
    (src / "link").symlink_to(src / "d")  # symlinks must not be double-counted
    assert ops.dir_size(src) == (2, True)
    assert ops.format_size(0) == "0 B"
    assert ops.format_size(512) == "512 B"
    assert ops.format_size(1536) == "1.5 KB"
    assert ops.format_size(1024 * 1024) == "1.0 MB"
