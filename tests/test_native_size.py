import os
import subprocess
import sys
from pathlib import Path

import pytest

from commander import native_size, ops


@pytest.fixture
def helper(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("macOS native helper")
    binary = tmp_path / "size-helper"
    source = Path(__file__).resolve().parents[1] / "packaging" / "dir_size.c"
    subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror", str(source),
                    "-o", str(binary)], check=True)
    monkeypatch.setattr(native_size, "_helper_path", lambda: binary)
    return binary


def test_native_matches_logical_sizes(helper, tmp_path):
    root = tmp_path / "한글 폴더"
    (root / ".hidden" / "nested").mkdir(parents=True)
    file = root / ".hidden" / "nested" / "file"
    file.write_bytes(b"abc")
    os.link(file, root / "hardlink")
    (root / "symlink").symlink_to(file)
    (root / "loop").symlink_to(root, target_is_directory=True)
    with (root / "sparse").open("wb") as f:
        f.truncate(16 * 1024 * 1024)
    os.mkfifo(root / "fifo")
    expected = 16 * 1024 * 1024 + 6
    assert ops.dir_size(root) == (expected, True)
    assert native_size.calculate(root) == (expected, True)


def test_native_empty_and_failure(helper, tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert native_size.calculate(root) == (0, True)
    assert native_size.calculate(root / "missing") is None


def test_native_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(native_size, "_helper_path", lambda: None)
    assert native_size.calculate(tmp_path) is None


def test_native_cancellation_reaps_process(helper, tmp_path, monkeypatch):
    real_popen = subprocess.Popen
    processes = []

    def start(*args, **kwargs):
        p = real_popen(*args, **kwargs)
        processes.append(p)
        return p

    monkeypatch.setattr(native_size.subprocess, "Popen", start)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls > 1

    assert native_size.calculate(tmp_path, cancel=cancel) == (0, False)
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.parametrize("native_result", [None, (123, True), (7, False)])
def test_worker_uses_native_or_fallback(tmp_path, monkeypatch, native_result):
    from commander.workers import DirSizeWorker

    folder = tmp_path / "large"
    folder.mkdir()
    monkeypatch.setattr(ops, "get_cached_size", lambda _: None)
    scans, cached, emitted = [], [], []

    def scan(path, **kwargs):
        scans.append(kwargs.get("max_dirs"))
        return (1, False) if kwargs.get("max_dirs") else (42, True)

    monkeypatch.setattr(ops, "dir_size", scan)
    monkeypatch.setattr(ops, "set_cached_size", lambda p, s: cached.append((p, s)))
    monkeypatch.setattr(native_size, "calculate", lambda *a, **kw: native_result)
    worker = DirSizeWorker(tmp_path)
    worker.sized.connect(lambda p, s: emitted.append(s))
    try:
        worker.run()
    finally:
        worker._cleanup()
    if native_result is None:
        assert scans == [60, None] and cached == [(str(folder), 42)]
    elif native_result[1]:
        assert scans == [60] and cached == [(str(folder), 123)]
    else:
        assert scans == [60] and cached == []
