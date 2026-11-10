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
    for bad in ["x/y", "x\\y", "", "CON", "a:b"]:
        with pytest.raises(ValueError):
            rename.plan([a], "a", bad)


def test_case_only_rename_works(tmp_path):
    (a,) = _files(tmp_path, "hello.txt")
    pairs = rename.plan([a], "hello", "HELLO")
    assert pairs == [(a, tmp_path / "HELLO.txt")]
    rename.apply(pairs)
    assert [p.name for p in tmp_path.iterdir()] == ["HELLO.txt"]


def test_masks_counter_and_case(tmp_path):
    import os

    from commander import rename

    names = ["b report.TXT", "a photo.JPG", "notes"]
    paths = []
    for n in names:
        p = tmp_path / n
        p.write_text("x")
        os.utime(p, (1_700_000_000, 1_700_000_000))
        paths.append(p)
    (tmp_path / "folder.v2").mkdir()
    paths.append(tmp_path / "folder.v2")

    def new(**opts):
        return [d.name for _s, d in rename.plan_names(paths, **opts)]

    assert new(name_mask="img_[C]", start=5, step=5, digits=3) == [
        "img_005.TXT", "img_010.JPG", "img_015", "img_020"]  # folders keep their dots in [N]
    assert new(name_mask="[P]-[N]", ext_mask="bak")[:2] == [
        f"{tmp_path.name}-b report.bak", f"{tmp_path.name}-a photo.bak"]
    stamp = __import__("time").strftime("%Y%m%d", __import__("time").localtime(1_700_000_000))
    assert new(name_mask="[YMD]_[N]")[2] == f"{stamp}_notes"
    assert new(case="lower") == ["b report.txt", "a photo.jpg"]  # unchanged names are left out
    assert new(case="upper") == ["B REPORT.TXT", "A PHOTO.JPG", "NOTES", "FOLDER.V2"]
    assert new(case="title")[:3] == ["B Report.txt", "A Photo.jpg", "Notes"]
    assert new(find=" ", replace="_", case="lower")[:2] == ["b_report.txt", "a_photo.jpg"]
    assert new(find=r"^(\w) (\w+)", replace=r"\2-\1", regex=True)[:2] == ["report-b.TXT", "photo-a.JPG"]
    assert new(name_mask="[X][N]")[0] == "[X]b report.TXT"  # unknown tokens stay literal
    assert new() == []

    import pytest

    with pytest.raises(ValueError):
        new(name_mask="same")  # every file would get the same name
    with pytest.raises(ValueError):
        new(find="(", regex=True)
    with pytest.raises(ValueError):
        new(find="a", replace=r"\9", regex=True)
    with pytest.raises(ValueError):
        new(case="sideways")
    with pytest.raises(ValueError):
        new(name_mask="a/[N]")


def test_rename_dialog_preview_and_apply(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    from commander import rename
    from commander.rename_dialog import RenameDialog

    QApplication.instance() or QApplication([])
    paths = [tmp_path / n for n in ("one.md", "two.md")]
    for p in paths:
        p.write_text(p.name)
    dlg = RenameDialog(paths)
    ok = dlg.buttons.button(QDialogButtonBox.Ok)
    assert not ok.isEnabled() and dlg.status.text() == "0개 항목의 이름이 바뀝니다."
    dlg.name_mask.setText("doc[C]")
    dlg.digits.setValue(2)
    rows = [(dlg.preview.topLevelItem(i).text(0), dlg.preview.topLevelItem(i).text(1)) for i in range(2)]
    assert rows == [("one.md", "doc01.md"), ("two.md", "doc02.md")] and ok.isEnabled()
    dlg.name_mask.setText("same")
    assert not ok.isEnabled() and "duplicate" in dlg.status.text()
    dlg.name_mask.setText("[N]")
    dlg.find.setText("one")
    dlg.replace.setText("two")  # one.md -> two.md, but two.md is in the batch and stays
    assert not ok.isEnabled()
    dlg.replace.setText("uno")
    assert [d.name for _s, d in dlg.pairs] == ["uno.md"]
    rename.apply(dlg.pairs)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["two.md", "uno.md"]
