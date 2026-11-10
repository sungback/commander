from commander import search


def _tree(tmp_path):
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    for rel in ["a.txt", "B.TXT", "c.py", "sub/d.txt", "sub/deep/e.txt", "sub/readme"]:
        (tmp_path / rel).write_text("x")
    return tmp_path


def _rel(root, paths):
    return sorted(p.relative_to(root).as_posix() for p in paths)


def test_glob_is_recursive_and_case_insensitive(tmp_path):
    root = _tree(tmp_path)
    assert _rel(root, search.find(root, "*.txt")) == [
        "B.TXT", "a.txt", "sub/d.txt", "sub/deep/e.txt"]


def test_plain_text_matches_substring_and_folders(tmp_path):
    root = _tree(tmp_path)
    assert _rel(root, search.find(root, "dee")) == ["sub/deep"]
    assert _rel(root, search.find(root, "READ")) == ["sub/readme"]
    assert _rel(root, search.find(root, "?.py")) == ["c.py"]


def test_no_match_and_missing_root(tmp_path):
    root = _tree(tmp_path)
    assert list(search.find(root, "*.zzz")) == []
    assert list(search.find(root / "nope", "*")) == []


def test_limit_and_cancel(tmp_path):
    root = _tree(tmp_path)
    assert len(list(search.find(root, "*.txt", limit=2))) == 2
    assert list(search.find(root, "*", cancelled=lambda: True)) == []
    gen = search.find(root, "*", cancelled=lambda: flag)
    flag = False
    next(gen)
    flag = True
    assert list(gen) == []


def test_symlink_dirs_not_followed(tmp_path):
    root = _tree(tmp_path)
    (root / "loop").symlink_to(root, target_is_directory=True)
    found = _rel(root, search.find(root, "*.txt"))
    assert found == ["B.TXT", "a.txt", "sub/d.txt", "sub/deep/e.txt"]


def test_contains_text_across_chunks_and_encodings(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_CHUNK", 8)
    (tmp_path / "split.txt").write_text("0123456Needle in a haystack")  # straddles a chunk edge
    (tmp_path / "utf16.txt").write_bytes("한글 needle".encode("utf-16"))
    (tmp_path / "korean.txt").write_text("안녕하세요 반갑습니다", encoding="utf-8")
    (tmp_path / "none.txt").write_text("nothing here")
    (tmp_path / "needle-dir").mkdir()
    assert search.contains(tmp_path / "split.txt", "NEEDLE")
    assert search.contains(tmp_path / "utf16.txt", "needle")
    assert search.contains(tmp_path / "korean.txt", "반갑")
    assert not search.contains(tmp_path / "none.txt", "needle")
    assert not search.contains(tmp_path / "missing.txt", "needle")
    assert _rel(tmp_path, search.find(tmp_path, "*", text="needle")) == ["split.txt", "utf16.txt"]
    assert _rel(tmp_path, search.find(tmp_path, "utf*", text="needle")) == ["utf16.txt"]
    assert _rel(tmp_path, search.find(tmp_path, "", text="needle", limit=1)) in (
        ["split.txt"], ["utf16.txt"])
