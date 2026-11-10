"""Offscreen UI tests for background jobs and the queue panel."""
import threading
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from test_smoke import _idle, _names, _setup, _wait

from commander.app import MainWindow
from commander.queue_panel import describe, format_duration
from commander.workers import Job


def _win(tmp_path, monkeypatch):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    return win, a, b


def _gated(gate, items=("a", "b")):
    """Job body that reports one item, then idles (honouring pause/cancel) until `gate` is set."""
    def run(job):
        job.on_item(Path(items[0]))
        while not gate.wait(0.01):
            job.control.checkpoint()
        for name in items[1:]:
            job.on_item(Path(name))
    return run


def test_queue_row_progress_pause_resume(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    assert win.queue.isHidden() and not win.busy()
    gate = threading.Event()
    job = win.start_job("Demo job", _gated(gate), total_items=2)
    assert not win.queue.isHidden() and win.busy()
    row = win.queue.row(job)
    _wait(lambda: job.stats.done_items == 1)
    win.queue.refresh()
    assert row.title.text() == "Demo job" and row.detail.text() == "1 / 2개 항목 — a"
    assert (row.bar.maximum(), row.bar.value()) == (1000, 500)

    row.pause_btn.click()
    assert job.control.paused and row.pause_btn.text() == "계속"
    assert row.detail.text().startswith("일시정지 — ")
    gate.set()
    QApplication.processEvents()
    _wait(lambda: job.stats.done_items == 2)  # the next item is reported, then it blocks
    assert job.isRunning()
    row.pause_btn.click()
    _idle(win)
    assert win.queue.isHidden() and win.jobs == [] and win.queue.rows() == []


def test_jobs_run_concurrently_and_cancel_independently(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    gates = [threading.Event(), threading.Event()]
    jobs = [win.start_job(f"job {i}", _gated(g), total_items=2) for i, g in enumerate(gates)]
    _wait(lambda: all(j.stats.done_items == 1 for j in jobs))  # both are running at once
    assert len(win.queue.rows()) == 2

    win.queue.row(jobs[0]).cancel_btn.click()
    _wait(lambda: win.jobs == [jobs[1]])
    assert jobs[0].stats.done_items == 1 and len(win.queue.rows()) == 1
    gates[1].set()
    _idle(win)
    assert jobs[1].stats.done_items == 2


def test_describe_bytes_speed_and_eta():
    QApplication.instance() or QApplication([])
    job = Job("x", lambda j: None)
    job.stats.scanning = True
    assert describe(job) == (-1, "준비 중...")
    job.stats.scanning = False
    job.stats.total_bytes, job.stats.done_bytes, job.stats.current = 4 << 20, 1 << 20, "big.iso"
    assert describe(job, speed=float(1 << 20)) == (
        250, "1.0 MB / 4.0 MB — 1.0 MB/s — 0:03 남음 — big.iso")
    job.waiting = True
    assert describe(job, speed=1.0)[1].startswith("응답 대기 중 — 1.0 MB / 4.0 MB — big")
    job.waiting = False
    job.stats.done_bytes = 9 << 20  # never shown above 100%
    assert describe(job)[0] == 1000
    job.cancel()
    assert describe(job) == (-1, "취소하는 중...")
    assert [format_duration(s) for s in (0, 59.9, 61, 3725)] == ["0:00", "0:59", "1:01", "1:02:05"]


def test_close_asks_before_cancelling_running_jobs(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    gate = threading.Event()
    job = win.start_job("long", _gated(gate), total_items=2)
    _wait(lambda: job.stats.done_items == 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    assert win.close() is False and job.isRunning() and not job.cancelled
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    assert win.close() is True
    assert job.cancelled and job.isFinished()
    QApplication.processEvents()


def test_f5_merges_into_existing_folder(tmp_path, monkeypatch):
    win, a, b = _win(tmp_path, monkeypatch)
    (a / "dir").mkdir()
    (a / "dir" / "x.txt").write_text("new")
    (a / "dir" / "only_a.txt").write_text("a")
    (b / "dir").mkdir()
    (b / "dir" / "x.txt").write_text("old")
    (b / "dir" / "only_b.txt").write_text("b")
    p = win.panels[0]
    _wait(lambda: "dir" in _names(p))
    asked = []

    def ask(self, name, remaining, allow_rename, detail=""):
        asked.append(Path(name).name)
        return ("overwrite", False)

    monkeypatch.setattr(MainWindow, "_ask_conflict", ask)
    p.select_pattern("dir")
    assert [x.name for x in p.selected_paths()] == ["dir"]
    win.transfer(move=False)
    _idle(win)
    assert asked == ["x.txt"]  # the folder merges silently; only the clashing file is asked
    assert sorted(x.name for x in (b / "dir").iterdir()) == ["only_a.txt", "only_b.txt", "x.txt"]
    assert (b / "dir" / "x.txt").read_text() == "new"


def test_conflict_questions_from_several_jobs_never_nest(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    depth = {"now": 0, "max": 0, "calls": 0}

    def ask(self, name, remaining, allow_rename, detail=""):
        depth["now"] += 1
        depth["calls"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        for _ in range(20):  # a real dialog spins the event loop while it is open
            QApplication.processEvents()
        depth["now"] -= 1
        return ("skip", False)

    monkeypatch.setattr(MainWindow, "_ask_conflict", ask)
    answers = []
    for i in range(3):
        win.start_job(f"asker {i}", lambda job: answers.append(job.ask(Path("s"), Path("d"))))
    _idle(win)
    assert answers == ["skip"] * 3 and depth == {"now": 0, "max": 1, "calls": 3}


def test_job_sticky_answer_and_cancel_while_waiting():
    QApplication.instance() or QApplication([])
    got = []

    def run(job):
        got.append(job.ask(Path("s1"), Path("d1")))
        got.append(job.ask(Path("s2"), Path("d2")))  # sticky: not asked again
        job.on_error(Path("bad.txt"), OSError("boom"))

    job = Job("sticky", run)
    asked = []
    job.conflict.connect(lambda j, s, d: (asked.append(d), j.answer("rename", apply_all=True)))
    job.start()
    _wait(job.isFinished)
    QApplication.processEvents()
    assert got == ["rename", "rename"] and asked == ["d1"]
    assert job.errors == ["bad.txt: OSError: boom"]

    reached = []
    waiter = Job("waits", lambda j: (j.ask(Path("s"), Path("d")), reached.append(1)))
    waiter.start()
    _wait(lambda: waiter.waiting)
    waiter.answer(None)  # cancelling the question cancels the job
    _wait(waiter.isFinished)
    assert reached == [] and waiter.cancelled and waiter.errors == []
