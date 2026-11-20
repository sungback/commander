import json

import pytest

from commander.file_parts import join_file, read_manifest, split_file
from commander.transfer import Cancelled, Control, Stats


@pytest.mark.parametrize("name", ["C:escape", "CON", "bad?.txt", "../escape"])
def test_manifest_rejects_unsafe_cross_platform_names(tmp_path, name):
    manifest = tmp_path / "parts.json"
    manifest.write_text(json.dumps({"format": "commander-parts-v1", "name": name,
                                    "size": 0, "parts": []}))
    with pytest.raises(ValueError):
        read_manifest(manifest)


@pytest.mark.parametrize("payload", [b"", b"a", b"0123456789" * 100])
def test_round_trip_and_progress(tmp_path, payload):
    src_dir, parts_dir, out_dir = (tmp_path / n for n in ("src", "parts", "out"))
    for directory in (src_dir, parts_dir, out_dir):
        directory.mkdir()
    src = src_dir / "한글.dat"
    src.write_bytes(payload)
    stats = Stats()
    manifest = split_file(src, parts_dir, 23, stats=stats)
    assert stats.done_bytes == len(payload)
    assert stats.done_items == stats.total_items
    result = join_file(manifest, out_dir)
    assert result.read_bytes() == payload


def test_split_collision_preserves_existing_and_cleans_owned_parts(tmp_path):
    src = tmp_path / "file"
    src.write_bytes(b"0123456789")
    collision = tmp_path / "file.000002.part"
    collision.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        split_file(src, tmp_path, 5)
    assert collision.read_bytes() == b"keep"
    assert not (tmp_path / "file.000001.part").exists()
    assert src.read_bytes() == b"0123456789"


def test_join_rejects_damage_missing_reordered_and_existing_output(tmp_path):
    src = tmp_path / "file"
    src.write_bytes(b"abcdefgh")
    parts = tmp_path / "parts"
    parts.mkdir()
    manifest = split_file(src, parts, 4)
    out = tmp_path / "out"
    out.mkdir()
    data = json.loads(manifest.read_text())
    data["parts"].reverse()
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        join_file(manifest, out)
    data["parts"].reverse()
    manifest.write_text(json.dumps(data))
    part = parts / data["parts"][0]["name"]
    part.write_bytes(b"xxxx")
    with pytest.raises(ValueError):
        join_file(manifest, out)
    assert not (out / "file").exists()
    part.unlink()
    with pytest.raises(ValueError):
        join_file(manifest, out)
    part.write_bytes(b"abcd")
    (out / "file").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        join_file(manifest, out)
    assert (out / "file").read_bytes() == b"keep"


def test_cancel_leaves_no_output(tmp_path):
    src = tmp_path / "file"
    src.write_bytes(b"payload")
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):
        split_file(src, tmp_path, 2, control=control)
    assert list(tmp_path.iterdir()) == [src]
