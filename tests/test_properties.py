import os
import stat

import pytest

from commander import properties


def test_sizes_counts_files_and_does_not_follow_links(tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    (folder / 'a').write_bytes(b'abc')
    (folder / 'b').write_bytes(b'hello')
    try:
        (folder / 'link').symlink_to(folder, target_is_directory=True)
    except OSError:
        pass
    result = properties.calculate_sizes([folder, folder / 'a'])
    assert result.logical == 8
    assert result.files == 2
    assert result.directories == 1
    assert result.allocated is None or result.allocated >= 0


def test_change_only_selected_fields_and_reports_missing(tmp_path):
    path = tmp_path / 'file'
    path.write_text('hello')
    old_mode = stat.S_IMODE(path.stat().st_mode)
    failures = []
    count = properties.change_attributes([path, tmp_path / 'missing'], modified=1000000000,
                                         on_error=lambda p, e: failures.append((p, e)))
    assert count == 1
    assert path.stat().st_mtime == 1000000000
    assert stat.S_IMODE(path.stat().st_mode) == old_mode
    assert len(failures) == 1


def test_recursive_changes_and_skips_symlink(tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    file = folder / 'file'
    file.write_text('x')
    outside = tmp_path / 'outside'
    outside.write_text('y')
    try:
        (folder / 'link').symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    old_time = outside.stat().st_mtime
    errors = []
    properties.change_attributes([folder], modified=1000000000, recursive=True,
                                 on_error=lambda p, e: errors.append((p, e)))
    assert file.stat().st_mtime == 1000000000
    assert outside.stat().st_mtime == old_time
    assert len(errors) == 1


def test_calculate_can_cancel(tmp_path):
    class Control:
        def checkpoint(self):
            raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        properties.calculate_sizes([tmp_path], control=Control())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX mode semantics')
def test_readonly_sets_write_bits_only(tmp_path):
    file = tmp_path / 'file'
    file.write_text('x')
    file.chmod(0o754)
    properties.change_attributes([file], readonly=True)
    assert stat.S_IMODE(file.stat().st_mode) == 0o554
    properties.change_attributes([file], readonly=False)
    assert stat.S_IMODE(file.stat().st_mode) == 0o754
