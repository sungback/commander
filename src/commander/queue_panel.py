"""Bottom queue panel: one row per running background job with progress, speed, pause, cancel."""
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import job_paths, ops
from .workers import Job

_WINDOW_S = 3.0  # speed is averaged over this many seconds


def format_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def describe(job: Job, speed: float | None = None) -> tuple[int, str]:
    """(progress in permille, or -1 when unknown; status text) for a job."""
    st = job.stats
    if job.cancelled:
        return -1, "취소하는 중..."
    if st.scanning:
        return -1, "준비 중..."
    if st.total_bytes > 0:
        done = min(st.done_bytes, st.total_bytes)
        parts = [f"{ops.format_size(done)} / {ops.format_size(st.total_bytes)}"]
        permille = int(done * 1000 / st.total_bytes)
        if speed and not job.control.paused and not job.waiting:
            parts.append(f"{ops.format_size(int(speed))}/s")
            parts.append(f"{format_duration((st.total_bytes - done) / speed)} 남음")
    else:
        parts = [f"{st.done_items} / {st.total_items}개 항목"]
        permille = int(st.done_items * 1000 / st.total_items) if st.total_items else -1
    if job.waiting:
        parts.insert(0, "응답 대기 중")
    elif job.control.paused:
        parts.insert(0, "일시정지")
    if st.current:
        parts.append(st.current)
    return permille, " — ".join(parts)


class _Row(QWidget):
    def __init__(self, job: Job, queue):
        super().__init__()
        self.job = job
        self.queue = queue
        self._samples: deque[tuple[float, int]] = deque()
        self.title = QLabel(job.title)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.detail = QLabel()
        self.detail.setTextInteractionFlags(Qt.NoTextInteraction)
        self.pause_btn = QPushButton("일시정지")
        self.cancel_btn = QPushButton("취소")
        self.up_btn = QPushButton("↑")
        self.down_btn = QPushButton("↓")
        for b in (self.pause_btn, self.cancel_btn, self.up_btn, self.down_btn):
            b.setFocusPolicy(Qt.NoFocus)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(lambda: queue.cancel(job))
        self.up_btn.clicked.connect(lambda: queue.move(job, -1))
        self.down_btn.clicked.connect(lambda: queue.move(job, 1))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self.title)
        text.addWidget(self.bar)
        text.addWidget(self.detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.addLayout(text, 1)
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.cancel_btn)
        self.refresh()

    def toggle_pause(self) -> None:
        if self.job.control.paused:
            self.job.resume()
        else:
            self.job.pause()
        self._samples.clear()
        self.refresh()

    def speed(self) -> float | None:
        now = time.monotonic()
        self._samples.append((now, self.job.stats.done_bytes))
        while len(self._samples) > 2 and now - self._samples[0][0] > _WINDOW_S:
            self._samples.popleft()
        t0, b0 = self._samples[0]
        if now - t0 < 0.5 or self.job.stats.done_bytes <= b0:
            return None
        return (self.job.stats.done_bytes - b0) / (now - t0)

    def refresh(self) -> None:
        if self.job in self.queue._pending:
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.detail.setText("경로 충돌로 대기 중" if self.queue.blocked(self.job) else "대기 중")
            self.pause_btn.setEnabled(False)
            index = self.queue._pending.index(self.job)
            self.up_btn.setEnabled(index > 0)
            self.down_btn.setEnabled(index < len(self.queue._pending) - 1)
            return
        permille, text = describe(self.job, self.speed())
        if permille < 0:
            self.bar.setRange(0, 0)  # busy indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(permille)
        self.detail.setText(text)
        self.pause_btn.setText("계속" if self.job.control.paused else "일시정지")
        self.pause_btn.setEnabled(not self.job.cancelled)
        self.cancel_btn.setEnabled(not self.job.cancelled)
        self.up_btn.setEnabled(False)
        self.down_btn.setEnabled(False)


class QueuePanel(QWidget):
    """Hidden while empty; rows are refreshed by polling the jobs' counters."""

    cancelled_pending = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("동시 실행 수"))
        self.limit = QSpinBox()
        self.limit.setRange(1, 16)
        self.limit.setValue(1)
        self.limit.valueChanged.connect(self.set_max_concurrent)
        controls.addWidget(self.limit)
        controls.addStretch()
        self._layout.addLayout(controls)
        self._rows: dict[Job, _Row] = {}
        self._pending: list[Job] = []
        self._active: set[Job] = set()
        self._max_concurrent = 1
        self._stopping = False
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def rows(self) -> list[_Row]:
        return list(self._rows.values())

    def row(self, job: Job) -> _Row:
        return self._rows[job]

    def add(self, job: Job) -> None:
        row = _Row(job, self)
        self._rows[job] = row
        self._pending.append(job)
        self._layout.addWidget(row)
        self.show()
        self._timer.start()
        self._schedule()

    def _schedule(self) -> None:
        while not self._stopping and len(self._active) < self._max_concurrent:
            job = next((j for j in self._pending if not self.blocked(j)), None)
            if job is None:
                break
            self._pending.remove(job)
            self._active.add(job)
            job.start()
        self.refresh()

    @staticmethod
    def _conflicts(a: Job, b: Job) -> bool:
        reads_a, writes_a = getattr(a, "reads", None), getattr(a, "writes", None)
        reads_b, writes_b = getattr(b, "reads", None), getattr(b, "writes", None)
        if any(paths is None for paths in (reads_a, writes_a, reads_b, writes_b)):
            return True
        return job_paths.conflicting_paths(reads_a, writes_a, reads_b, writes_b) is not None

    def blocked(self, job: Job) -> bool:
        # Earlier conflicting jobs keep their ordering even when another slot is free.
        earlier = self._pending[:self._pending.index(job)]
        return any(self._conflicts(job, other) for other in (*self._active, *earlier))

    def set_max_concurrent(self, count: int) -> None:
        if not 1 <= count <= self.limit.maximum():
            raise ValueError("concurrency is outside the supported range")
        self._max_concurrent = count
        if self.limit.value() != count:
            self.limit.setValue(count)
        self._schedule()

    def move(self, job: Job, delta: int) -> None:
        if job not in self._pending or delta not in (-1, 1):
            return
        index = self._pending.index(job)
        other = index + delta
        if not 0 <= other < len(self._pending):
            return
        neighbor = self._rows[self._pending[other]]
        self._pending[index], self._pending[other] = self._pending[other], self._pending[index]
        self._layout.removeWidget(self._rows[job])
        self._layout.insertWidget(self._layout.indexOf(neighbor) + (1 if delta > 0 else 0),
                                  self._rows[job])
        self._schedule()

    def cancel(self, job: Job) -> None:
        job.cancel()
        if job in self._pending:
            self._pending.remove(job)
            job.discard_pending()
            self.cancelled_pending.emit(job)
        self.refresh()

    def cancel_all(self) -> None:
        self._stopping = True
        for job in list(self._rows):
            self.cancel(job)

    def remove(self, job: Job) -> None:
        self._active.discard(job)
        if job in self._pending:
            self._pending.remove(job)
        row = self._rows.pop(job, None)
        if row is not None:
            self._layout.removeWidget(row)
            row.deleteLater()
        if not self._rows:
            self._timer.stop()
            self.hide()
        self._schedule()

    def refresh(self) -> None:
        for row in self._rows.values():
            row.refresh()
