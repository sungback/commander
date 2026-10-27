import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest

from commander import ops
from commander.workers import Worker


def _run(worker):
    app = QCoreApplication.instance() or QCoreApplication([])
    worker.start()
    while not worker.isFinished():
        QTest.qWait(10)
    app.processEvents()


def test_progress_reports_each_item(tmp_path):
    files = [tmp_path / f"{i}.txt" for i in range(3)]
    for f in files:
        f.write_text("x")
    seen = []
    w = Worker(lambda progress: ops.delete(files, progress=progress))
    w.progress.connect(lambda n, name: seen.append((n, name)))
    _run(w)
    assert seen == [(1, "0.txt"), (2, "1.txt"), (3, "2.txt")]
    assert not any(f.exists() for f in files)


def test_cancel_stops_between_items(tmp_path):
    files = [tmp_path / f"{i}.txt" for i in range(3)]
    for f in files:
        f.write_text("x")
    w = Worker(lambda progress: ops.delete(files, progress=progress))
    w.progress.connect(lambda *_: w.cancel(), type=Qt.DirectConnection)
    _run(w)
    assert [f.exists() for f in files] == [False, True, True]


def test_error_is_reported(tmp_path):
    errors = []
    w = Worker(lambda progress: ops.delete([tmp_path / "missing"], progress=progress))
    w.failed.connect(errors.append)
    _run(w)
    assert errors and "FileNotFoundError" in errors[0]


def test_search_worker_streams_and_cancels(tmp_path):
    from commander.workers import SearchWorker

    for i in range(3):
        (tmp_path / f"{i}.txt").write_text("x")
    found = []
    w = SearchWorker(tmp_path, "*.txt")
    w.found.connect(found.append)
    _run(w)
    assert sorted(found) == [str(tmp_path / f"{i}.txt") for i in range(3)]
    assert not w.truncated

    found.clear()
    w = SearchWorker(tmp_path, "*.txt")
    w.found.connect(lambda p: (found.append(p), w.cancel()), type=Qt.DirectConnection)
    _run(w)
    assert len(found) == 1
