import pytest

from commander.job_paths import conflicting_paths


def test_readers_can_overlap(tmp_path):
    assert conflicting_paths([tmp_path], [], [tmp_path / "file"], []) is None


def test_write_overlap_and_sibling_boundary(tmp_path):
    folder = tmp_path / "dir"
    file = folder / "item"
    sibling = tmp_path / "directory" / "item"
    assert conflicting_paths([], [folder], [file], []) == (folder, file)
    assert conflicting_paths([file], [], [], [folder]) == (file, folder)
    assert conflicting_paths([], [folder], [], [sibling]) is None


def test_future_output_under_symlink(tmp_path):
    folder = tmp_path / "real"
    folder.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(folder, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    output = link / "new"
    assert conflicting_paths([], [output], [folder / "new"], []) is not None


def test_existing_hardlinks_overlap(tmp_path):
    original = tmp_path / "a"
    original.write_text("data")
    alias = tmp_path / "b"
    try:
        alias.hardlink_to(original)
    except OSError:
        pytest.skip("hard links unavailable")
    assert conflicting_paths([original], [], [], [alias]) == (original, alias)
