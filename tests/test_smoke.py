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


def _names(panel):
    root = panel.view.rootIndex()
    return {panel.model.fileName(panel.model.index(r, 0, root))
            for r in range(panel.model.rowCount(root))}


def _wait(cond, timeout_ms=3000):
    """QFileSystemModel loads asynchronously; poll until cond() holds."""
    from PySide6.QtTest import QTest

    waited = 0
    while not cond() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert cond(), "timed out waiting for model"


def _idle(win):
    _wait(lambda: win._worker is None)


def _setup(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    app = QApplication.instance() or QApplication([])
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("x")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_: None)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("newdir", True))
    win = MainWindow()
    win.show()
    win.panels[0].set_path(str(a))
    win.panels[1].set_path(str(b))
    _wait(lambda: "f.txt" in _names(win.panels[0]))
    win.panels[0].view.setFocus()
    return app, win, a, b


def _select_file(win):
    p = win.panels[0]
    for r in range(p.model.rowCount(p.view.rootIndex())):
        idx = p.model.index(r, 0, p.view.rootIndex())
        if p.model.fileName(idx) == "f.txt":
            p.view.setCurrentIndex(idx)
            return
    raise AssertionError("f.txt not listed")


def test_f5_f6_f7_f8(tmp_path, monkeypatch):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0  # offscreen focus is unreliable
    _select_file(win)
    assert [p.name for p in win.panels[0].selected_paths()] == ["f.txt"]

    win.transfer(move=False)
    _idle(win)
    assert (b / "f.txt").exists() and (a / "f.txt").exists()

    win.make_dir()
    assert (a / "newdir").is_dir()
    _wait(lambda: "newdir" in _names(win.panels[0]))

    win.transfer(move=True)  # f.txt exists in b -> overwrite prompt auto-yes
    _idle(win)
    assert not (a / "f.txt").exists() and (b / "f.txt").exists()

    _wait(lambda: "f.txt" not in _names(win.panels[0]))
    win.panels[0].view.selectAll()
    win.delete()
    _idle(win)
    assert list(a.iterdir()) == []


def test_tabs_are_independent(tmp_path):
    app = QApplication.instance() or QApplication([])
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    panel = MainWindow().panels[0]
    panel.set_path(str(a))
    panel.new_tab()
    assert panel.tabs.count() == 2 and panel.current_path() == str(a)
    panel.set_path(str(b))
    assert panel.tabs.tabText(1) == "b"
    panel.tabs.setCurrentIndex(0)
    assert panel.current_path() == str(a)
    panel.close_tab(1)
    panel.close_tab(0)  # last tab is kept
    assert panel.tabs.count() == 1
    app.processEvents()


def test_keyboard_navigation(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "x.txt").write_text("x")
    panel = MainWindow().panels[0]
    panel.set_path(str(tmp_path))
    _wait(lambda: "sub" in _names(panel))

    def row(name):
        root = panel.view.rootIndex()
        return next(panel.model.index(r, 0, root) for r in range(panel.model.rowCount(root))
                    if panel.model.fileName(panel.model.index(r, 0, root)) == name)

    panel.view.setCurrentIndex(row("sub"))
    QTest.keyClick(panel.view, Qt.Key_Return)
    assert panel.current_path() == str(sub)
    QTest.keyClick(panel.view, Qt.Key_Backspace)
    assert panel.current_path() == str(tmp_path)

    _wait(lambda: "x.txt" in _names(panel))
    panel.view.setCurrentIndex(row("x.txt"))
    panel.view.clearSelection()  # setCurrentIndex selects the row itself
    QTest.keyClick(panel.view, Qt.Key_Space)
    assert [p.name for p in panel.selected_paths()] == ["x.txt"]
    panel.view.setCurrentIndex(row("x.txt"))
    QTest.keyClick(panel.view, Qt.Key_Space)
    assert panel.selected_paths() == []
    app.processEvents()
