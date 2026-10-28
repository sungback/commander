#!/bin/sh
# 커밋 및 완료 선언 전 필수 검증 하네스:
# 1) ruff 린트  2) pytest 전체 테스트  3) 실제 홈 디렉터리 오프스크린 스모크 & 성능 검증
set -e
cd "$(dirname "$0")/.."

echo "[1/3] Running ruff check..."
.venv/bin/ruff check .

echo "[2/3] Running pytest..."
.venv/bin/pytest -q

echo "[3/3] Running real-environment headless smoke & performance check..."
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from commander.app import MainWindow

app = QApplication([])
win = MainWindow()
assert win.windowTitle() == "commander", f"unexpected title: {win.windowTitle()}"
assert not win.windowIcon().isNull(), "window icon is null"

t0 = time.monotonic()
while time.monotonic() - t0 < 0.45:
    app.processEvents()
    time.sleep(0.005)

p = win.panels[0]
root = p.view.rootIndex()
assert p.model.rowCount(root) > 0, "home directory rows did not load"
assert p.model.index(0, 0, root).data() == "..", "first row in ascending order must be '..'"

p.view.sortByColumn(0, Qt.DescendingOrder)
assert p.model.index(0, 0, root).data() == "..", "first row in descending order must be '..'"
p.view.sortByColumn(0, Qt.AscendingOrder)

assert len(p.model.dir_sizes) > 0, "background DirSizeWorker did not populate dir_sizes within 450ms"
print(f"OK: '..' pinned first, {len(p.model.dir_sizes)} folders sized in 450ms, status='{p.status_bar.text()}'")
EOF
