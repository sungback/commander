import sys
from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter

from .panel import FilePanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Commander")
        self.resize(1100, 650)
        home = str(Path.home())
        self.panels = [FilePanel(home), FilePanel(home)]
        splitter = QSplitter()
        for p in self.panels:
            splitter.addWidget(p)
        self.setCentralWidget(splitter)
        QShortcut(QKeySequence("Tab"), self, activated=self.switch_panel)
        self.panels[0].view.setFocus()

    def active_index(self) -> int:
        return 1 if self.panels[1].view.hasFocus() else 0

    def switch_panel(self) -> None:
        self.panels[1 - self.active_index()].view.setFocus()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
