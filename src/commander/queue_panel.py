"""Bottom queue panel: one row per running background job with progress, speed, pause, cancel."""
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import ops
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
        return -1, "Cancelling..."
    if st.scanning:
        return -1, "Preparing..."
    if st.total_bytes > 0:
        done = min(st.done_bytes, st.total_bytes)
        parts = [f"{ops.format_size(done)} / {ops.format_size(st.total_bytes)}"]
        permille = int(done * 1000 / st.total_bytes)
        if speed and not job.control.paused and not job.waiting:
            parts.append(f"{ops.format_size(int(speed))}/s")
            parts.append(f"{format_duration((st.total_bytes - done) / speed)} left")
    else:
        parts = [f"{st.done_items} / {st.total_items} item(s)"]
        permille = int(st.done_items * 1000 / st.total_items) if st.total_items else -1
    if job.waiting:
        parts.insert(0, "Waiting for your answer")
    elif job.control.paused:
        parts.insert(0, "Paused")
    if st.current:
        parts.append(st.current)
    return permille, " — ".join(parts)


class _Row(QWidget):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self._samples: deque[tuple[float, int]] = deque()
        self.title = QLabel(job.title)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.detail = QLabel()
        self.detail.setTextInteractionFlags(Qt.NoTextInteraction)
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Cancel")
        for b in (self.pause_btn, self.cancel_btn):
            b.setFocusPolicy(Qt.NoFocus)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(job.cancel)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self.title)
        text.addWidget(self.bar)
        text.addWidget(self.detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.addLayout(text, 1)
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
        permille, text = describe(self.job, self.speed())
        if permille < 0:
            self.bar.setRange(0, 0)  # busy indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(permille)
        self.detail.setText(text)
        self.pause_btn.setText("Resume" if self.job.control.paused else "Pause")
        self.pause_btn.setEnabled(not self.job.cancelled)
        self.cancel_btn.setEnabled(not self.job.cancelled)


class QueuePanel(QWidget):
    """Hidden while empty; rows are refreshed by polling the jobs' counters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._rows: dict[Job, _Row] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def rows(self) -> list[_Row]:
        return list(self._rows.values())

    def row(self, job: Job) -> _Row:
        return self._rows[job]

    def add(self, job: Job) -> None:
        row = _Row(job)
        self._rows[job] = row
        self._layout.addWidget(row)
        self.show()
        self._timer.start()

    def remove(self, job: Job) -> None:
        row = self._rows.pop(job, None)
        if row is not None:
            self._layout.removeWidget(row)
            row.deleteLater()
        if not self._rows:
            self._timer.stop()
            self.hide()

    def refresh(self) -> None:
        for row in self._rows.values():
            row.refresh()
