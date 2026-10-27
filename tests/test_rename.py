import pytest

from commander import rename


def _files(tmp_path, *names):
    for n in names:
        (tmp_path / n).write_text(n)
    return [tmp_path / n for n in names]


def test_plan_skips_unchanged_and_applies(tmp_path):
    a, b, c = _files(tmp_path, "img1.txt", "img2.txt", "other.md")
    pairs = rename.plan([a, b, c], "img", "pic")
    assert pairs == [(a, tmp_path / "pic1.txt"), (b, tmp_path / "pic2.txt")]
    rename.apply(pairs)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.md", "pic1.txt", "pic2.txt"]
    assert (tmp_path / "pic1.txt").read_text() == "img1.txt"


def test_literal_replace_is_not_regex(tmp_path):
    (a,) = _files(tmp_path, "a.b.txt")
    assert rename.plan([a], ".", "_") == [(a, tmp_path / "a_b_txt")]


def test_regex_groups(tmp_path):
    (a,) = _files(tmp_path, "file12.txt")
    assert rename.plan([a], r"file(\d+)", r"n\1", regex=True) == [(a, tmp_path / "n12.txt")]


def test_swap_works(tmp_path):
    a, b = _files(tmp_path, "a", "b")
    pairs = [(a, b), (b, a)]
    rename.apply(pairs)
    assert a.read_text() == "b" and b.read_text() == "a"


def test_collision_with_outside_file_rejected(tmp_path):
    a, _ = _files(tmp_path, "x1", "x2")
    with pytest.raises(FileExistsError):
        rename.plan([a], "1", "2")


def test_duplicate_target_rejected(tmp_path):
    a, b = _files(tmp_path, "a1", "a2")
    with pytest.raises(ValueError, match="duplicate"):
        rename.plan([a, b], r"\d", "", regex=True)


def test_invalid_inputs(tmp_path):
    (a,) = _files(tmp_path, "a")
    with pytest.raises(ValueError):
        rename.plan([a], "", "x")
    with pytest.raises(ValueError):
        rename.plan([a], "(", "x", regex=True)
    with pytest.raises(ValueError):
        rename.plan([a], "a", "x/y")
    with pytest.raises(ValueError):
        rename.plan([a], "a", "")
