import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
)

from . import ops
from .panel import FilePanel
from .workers import Worker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Commander")
        self.resize(1100, 650)
        self._worker: Worker | None = None
        home = str(Path.home())
        self.panels = [FilePanel(home), FilePanel(home)]
        splitter = QSplitter()
        for p in self.panels:
            splitter.addWidget(p)
        self.setCentralWidget(splitter)
        QShortcut(QKeySequence("Tab"), self, activated=self.switch_panel)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.transfer(move=False))
        QShortcut(QKeySequence("F6"), self, activated=lambda: self.transfer(move=True))
        QShortcut(QKeySequence("F7"), self, activated=self.make_dir)
        QShortcut(QKeySequence("F8"), self, activated=self.delete)
        self.panels[0].view.setFocus()

    def active_index(self) -> int:
        return 1 if self.panels[1].view.hasFocus() else 0

    def switch_panel(self) -> None:
        self.panels[1 - self.active_index()].view.setFocus()

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def _error(self, e: Exception) -> None:
        QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

    def transfer(self, move: bool) -> None:
        src_panel = self.panels[self.active_index()]
        dst_panel = self.panels[1 - self.active_index()]
        srcs = src_panel.selected_paths()
        if not srcs:
            return
        dst = Path(dst_panel.current_path())
        verb = "Move" if move else "Copy"
        if not self._ask(verb, f"{verb} {len(srcs)} item(s) to\n{dst}?"):
            return
        conflicts = [s.name for s in srcs if (dst / s.name).exists()]
        overwrite = False
        if conflicts:
            names = ", ".join(conflicts[:5]) + (" ..." if len(conflicts) > 5 else "")
            if not self._ask("Overwrite", f"Already exists:\n{names}\n\nOverwrite?"):
                return
            overwrite = True
        fn = ops.move if move else ops.copy
        self._run_task(verb, len(srcs),
                       lambda progress: fn(srcs, dst, overwrite=overwrite, progress=progress))

    def _run_task(self, title: str, total: int, task) -> None:
        if self._worker is not None:
            return  # one background task at a time
        dialog = QProgressDialog(title, "Cancel", 0, total, self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(500)
        worker = Worker(task, self)
        worker.progress.connect(lambda n, name: (dialog.setValue(n), dialog.setLabelText(name)))
        worker.failed.connect(lambda msg: QMessageBox.critical(self, "Error", msg))
        dialog.canceled.connect(worker.cancel)

        def finished():
            dialog.reset()
            dialog.deleteLater()
            worker.deleteLater()
            self._worker = None

        worker.finished.connect(finished)
        self._worker = worker
        worker.start()

    def make_dir(self) -> None:
        panel = self.panels[self.active_index()]
        name, ok = QInputDialog.getText(self, "New folder", "Name:")
        if not ok or not name:
            return
        try:
            ops.make_dir(Path(panel.current_path()), name)
        except (OSError, ValueError) as e:
            self._error(e)

    def delete(self) -> None:
        panel = self.panels[self.active_index()]
        paths = panel.selected_paths()
        if not paths:
            return
        if not self._ask("Delete", f"Permanently delete {len(paths)} item(s)?"):
            return
        self._run_task("Delete", len(paths), lambda progress: ops.delete(paths, progress=progress))


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
