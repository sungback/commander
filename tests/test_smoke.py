import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from commander.app import MainWindow


def test_window_has_two_panels(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.panels[0].set_path(str(tmp_path))
    assert len(win.panels) == 2
    assert win.panels[0].current_path() == str(tmp_path)
    app.processEvents()
