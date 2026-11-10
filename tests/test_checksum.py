import hashlib

import pytest

from commander import checksum
from commander.transfer import Cancelled, Control, Stats


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "a.bin").write_bytes(b"hello")
    (tmp_path / "d" / "b.txt").write_bytes(b"world")
    (tmp_path / "d" / "sub" / "c.txt").write_bytes(b"")
    return tmp_path


def test_hash_files_expands_folders_and_reports_progress(tree):
    stats = Stats()
    text = checksum.hash_files([tree / "a.bin", tree / "d"], tree, "sha256", stats)
    assert text.splitlines() == [
        f"{hashlib.sha256(b'hello').hexdigest()} *a.bin",
        f"{hashlib.sha256(b'world').hexdigest()} *d/b.txt",
        f"{hashlib.sha256(b'').hexdigest()} *d/sub/c.txt",
    ]
    assert (stats.total_bytes, stats.done_bytes, stats.done_items) == (10, 10, 3)
    assert checksum.file_hash(tree / "a.bin", "md5") == hashlib.md5(b"hello").hexdigest()
    assert checksum.hash_files([], tree) == ""


def test_parse_accepts_common_formats():
    md5 = "5d41402abc4b2a76b9719d911017c592"
    text = f"# comment\n\n{md5}  a.bin\n{md5.upper()} *d/b c.txt\nnot-a-hash  x\nzz{md5[2:]}  y\n"
    assert checksum.parse_sums(text) == [(md5, "a.bin"), (md5, "d/b c.txt")]


def test_verify_statuses_and_path_confinement(tree):
    outside = tree.parent / "outside.txt"
    outside.write_bytes(b"hello")
    good = hashlib.sha1(b"hello").hexdigest()
    sums = tree / "list.sha1"
    sums.write_text(
        f"{good} *a.bin\n{good} *d/b.txt\n{good} *gone.txt\n{good} *../outside.txt\n{good} */etc/hostname\n")
    stats = Stats()
    assert checksum.verify(sums, stats) == [
        ("a.bin", "OK"), ("d/b.txt", "FAILED"), ("gone.txt", "MISSING"),
        ("../outside.txt", "INVALID"), ("/etc/hostname", "INVALID")]
    assert stats.done_items == 5 and stats.done_bytes == 10
    # Without a known extension the algorithm follows the digest length
    plain = tree / "sums.txt"
    plain.write_text(f"{hashlib.md5(b'hello').hexdigest()}  a.bin\n")
    assert checksum.verify(plain) == [("a.bin", "OK")]
    plain.write_text("nothing useful\n")
    with pytest.raises(ValueError):
        checksum.verify(plain)


def test_roundtrip_and_cancel(tree):
    (tree / "all.sha256").write_text(checksum.hash_files([tree / "a.bin", tree / "d"], tree))
    assert {s for _, s in checksum.verify(tree / "all.sha256")} == {"OK"}
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):
        checksum.hash_files([tree / "a.bin"], tree, control=control)


def test_unreadable_file_is_reported(tree):
    errors = []
    real = checksum.file_hash

    def flaky(path, *a):
        if path.name == "b.txt":
            raise PermissionError(path)
        return real(path, *a)

    checksum.file_hash, keep = flaky, checksum.file_hash
    try:
        text = checksum.hash_files([tree / "d"], tree, on_error=lambda p, e: errors.append(p.name))
    finally:
        checksum.file_hash = keep
    assert errors == ["b.txt"] and text.count("\n") == 1
