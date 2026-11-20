import base64

import pytest

from commander import file_encoding
from commander.transfer import Cancelled, Control


def test_mime_encoding_matches_independent_library(tmp_path):
    data = bytes(range(256)) * 1000
    source, encoded, decoded = (tmp_path / name for name in ("source", "encoded", "decoded"))
    source.write_bytes(data)
    file_encoding.convert(source, encoded)
    assert encoded.read_bytes() == base64.encodebytes(data)
    file_encoding.convert(encoded, decoded, decode=True)
    assert decoded.read_bytes() == data


@pytest.mark.parametrize("content", [b"abc", b"a===", b"YQ==YQ==", b"!@#$", b"YQ==x"])
def test_invalid_input_never_publishes_output(tmp_path, content):
    source, destination = tmp_path / "bad", tmp_path / "output"
    source.write_bytes(content)
    with pytest.raises(ValueError):
        file_encoding.convert(source, destination, decode=True)
    assert not destination.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad"]


def test_existing_destination_and_cancel_preserve_files(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    source.write_bytes(b"content")
    destination.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        file_encoding.convert(source, destination)
    assert destination.read_bytes() == b"original"
    destination.unlink()
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):
        file_encoding.convert(source, destination, control=control)
    assert not destination.exists()


def test_symlink_source_rejected(tmp_path):
    source, link = tmp_path / "source", tmp_path / "link"
    source.write_bytes(b"data")
    link.symlink_to(source)
    with pytest.raises(ValueError):
        file_encoding.convert(link, tmp_path / "output")
