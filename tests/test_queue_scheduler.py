"""Scheduling behavior independent of the main window."""
import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from test_smoke import _wait

from commander.queue_panel import QueuePanel
from commander.workers import Job


def test_queue_limit_reorder_cancel_and_completion(tmp_path):
    QApplication.instance() or QApplication([])
    queue = QueuePanel()
    gate = threading.Event()
    started = []

    def make(name):
        def run(job):
            started.append(name)
            while not gate.wait(.01) and not job.cancelled:
                pass
        job = Job(name, run)
        job.finished.connect(lambda: queue.remove(job))
        return job

    queue.cancelled_pending.connect(queue.remove)
    first, second, third = (make(name) for name in ("first", "second", "third"))
    for job in (first, second, third):
        queue.add(job)
    _wait(lambda: started == ["first"])
    assert queue.row(second).detail.text() == "경로 충돌로 대기 중"
    queue.row(third).up_btn.click()
    assert queue._pending == [third, second]
    queue.row(second).cancel_btn.click()
    assert second.cancelled and second not in queue._rows
    gate.set()
    _wait(lambda: started == ["first", "third"] and not queue.rows())


def test_disjoint_jobs_run_concurrently_and_conflicts_wait(tmp_path):
    QApplication.instance() or QApplication([])
    queue = QueuePanel()
    queue.set_max_concurrent(2)
    gate = threading.Event()
    started = []

    def make(name, reads, writes):
        def run(job):
            started.append(name)
            while not gate.wait(.01) and not job.cancelled:
                pass
        job = Job(name, run)
        job.reads, job.writes = reads, writes
        job.finished.connect(lambda: queue.remove(job))
        return job

    root = Path(tmp_path)
    first = make("first", (), (root / "a",))
    conflict = make("conflict", (root / "a" / "child",), ())
    independent = make("independent", (), (root / "b",))
    for job in (first, conflict, independent):
        queue.add(job)
    _wait(lambda: len(started) == 2)
    assert set(started) == {"first", "independent"}
    assert queue.row(conflict).detail.text() == "경로 충돌로 대기 중"
    gate.set()
    _wait(lambda: len(started) == 3 and not queue.rows())


def test_limit_matches_control_and_rejects_out_of_range():
    QApplication.instance() or QApplication([])
    queue = QueuePanel()
    queue.limit.setValue(3)
    assert queue._max_concurrent == 3
    queue.set_max_concurrent(16)
    assert queue.limit.value() == 16
    with pytest.raises(ValueError):
        queue.set_max_concurrent(17)
