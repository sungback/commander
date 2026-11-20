import pytest

from commander import descriptions


def test_description_rejects_nonregular_file(tmp_path):
    (tmp_path / "descript.ion").mkdir()
    with pytest.raises(ValueError):
        descriptions.read_description(tmp_path, "file.txt")


def test_edits_preserve_other_lines_encoding_and_newlines(tmp_path):
    path = tmp_path / "descript.ion"
    path.write_bytes('"한글 파일.txt" 이전 설명\r\nother.txt 다른 설명\r\n'.encode("cp949"))
    assert descriptions.read_description(tmp_path, "한글 파일.txt") == "이전 설명"
    descriptions.write_description(tmp_path, "한글 파일.txt", "새 설명")
    assert path.read_bytes() == '"한글 파일.txt" 새 설명\r\nother.txt 다른 설명\r\n'.encode("cp949")


def test_add_remove_and_preserve_bom(tmp_path):
    path = tmp_path / "descript.ion"
    path.write_bytes(b"\xef\xbb\xbfexisting text")
    descriptions.write_description(tmp_path, "new name.txt", "hello")
    assert path.read_bytes() == b'\xef\xbb\xbfexisting text\n"new name.txt" hello\n'
    descriptions.write_description(tmp_path, "new name.txt", "")
    assert path.read_bytes() == b"\xef\xbb\xbfexisting text\n"


def test_duplicate_and_unencodable_leave_original(tmp_path):
    path = tmp_path / "descript.ion"
    original = "a 이전\na 또다른\n".encode("cp949")
    path.write_bytes(original)
    with pytest.raises(ValueError):
        descriptions.write_description(tmp_path, "a", "변경")
    with pytest.raises(ValueError):
        descriptions.write_description(tmp_path, "b", "😀")
    assert path.read_bytes() == original


def test_symlink_is_never_replaced(tmp_path):
    target = tmp_path / "target"
    target.write_text("a hi\n")
    (tmp_path / "descript.ion").symlink_to(target)
    with pytest.raises(ValueError):
        descriptions.write_description(tmp_path, "a", "new")
    assert target.read_text() == "a hi\n"
