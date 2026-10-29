import io
import tarfile
import zipfile

import pytest

from commander import archive


def _zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("top.txt", "t")
        z.writestr("d/a.txt", "a")
        z.writestr("d/sub/b.txt", "b")  # no explicit dir entries
    return path


def _tar(path, mode):
    with tarfile.open(path, mode) as t:
        for name, data in [("top.txt", b"t"), ("d/a.txt", b"a"), ("d/sub/b.txt", b"b")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("d/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        t.addfile(link)
    return path


@pytest.fixture(params=["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"])
def arc(request, tmp_path):
    kind = request.param
    p = tmp_path / f"x.{kind}"
    return _zip(p) if kind == "zip" else _tar(p, "w:" + kind.split(".")[1] if "." in kind else "w")


def test_is_archive(tmp_path, arc):
    assert archive.is_archive(arc)
    plain = tmp_path / "p.txt"
    plain.write_text("x")
    assert not archive.is_archive(plain)
    assert not archive.is_archive(tmp_path / "missing.zip")


def test_children(arc):
    entries = archive.read_entries(arc)
    assert [(e.path, e.is_dir, e.size) for e in archive.children(entries)] == [
        ("d", True, 2), ("top.txt", False, 1)]
    assert [(e.path, e.is_dir, e.size) for e in archive.children(entries, "d")] == [
        ("d/sub", True, 1), ("d/a.txt", False, 1)]


def test_extract_file_and_dir(tmp_path, arc):
    dst = tmp_path / "out"
    dst.mkdir()
    seen = []
    archive.extract(arc, ["top.txt", "d"], dst, progress=seen.append)
    assert (dst / "top.txt").read_text() == "t"
    assert (dst / "d" / "a.txt").read_text() == "a"
    assert (dst / "d" / "sub" / "b.txt").read_text() == "b"
    assert not (dst / "d" / "link").exists()  # links skipped
    assert [p.name for p in seen] == ["top.txt", "d"]


def test_extract_nested_selection_lands_at_top_level(tmp_path, arc):
    dst = tmp_path / "out"
    dst.mkdir()
    archive.extract(arc, ["d/sub"], dst)
    assert (dst / "sub" / "b.txt").read_text() == "b"


def test_extract_conflict(tmp_path, arc):
    dst = tmp_path / "out"
    dst.mkdir()
    (dst / "top.txt").write_text("old")
    with pytest.raises(FileExistsError):
        archive.extract(arc, ["top.txt"], dst)
    assert (dst / "top.txt").read_text() == "old"
    archive.extract(arc, ["top.txt"], dst, overwrite=True)
    assert (dst / "top.txt").read_text() == "t"


def test_path_traversal_blocked_zip(tmp_path):
    p = tmp_path / "evil.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("d/../../evil2.txt", "x")
        z.writestr("/abs.txt", "x")
        z.writestr("..\\win.txt", "x")
        z.writestr("C:/drive.txt", "x")
    dst = tmp_path / "out"
    dst.mkdir()
    for sel in ["../evil.txt", "/abs.txt", "..\\win.txt", "C:/drive.txt"]:
        with pytest.raises(ValueError):
            archive.extract(p, [sel], dst)
    with pytest.raises(ValueError):
        archive.extract(p, ["d"], dst)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "evil2.txt").exists()
    assert not (tmp_path / "abs.txt").exists()
    # hostile names are never offered as children
    assert [e.path for e in archive.children(archive.read_entries(p))] == ["d"]


def test_path_traversal_blocked_tar(tmp_path):
    p = tmp_path / "evil.tar"
    with tarfile.open(p, "w") as t:
        info = tarfile.TarInfo("d/../../evil.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    dst = tmp_path / "out"
    dst.mkdir()
    with pytest.raises(ValueError):
        archive.extract(p, ["d"], dst)
    assert not (tmp_path / "evil.txt").exists()


def test_corrupt_archive(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        archive.read_entries(p)
