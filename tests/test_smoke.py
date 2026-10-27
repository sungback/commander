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


def test_browse_and_extract_archive(tmp_path, monkeypatch):
    import zipfile

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    with zipfile.ZipFile(a / "z.zip", "w") as z:
        z.writestr("d/in.txt", "hi")
    p = win.panels[0]
    _wait(lambda: "z.zip" in _names(p))

    def rows():
        m = p.view.model()
        root = p.view.rootIndex()
        return [m.index(r, 0, root).data() for r in range(m.rowCount(root))]

    def select(name):
        m = p.view.model()
        p.view.setCurrentIndex(m.index(rows().index(name), 0, p.view.rootIndex()))

    select("z.zip")
    QTest.keyClick(p.view, Qt.Key_Return)
    assert p.in_archive and rows() == ["..", "d"]
    assert p.tabs.tabText(0) == "z.zip"
    select("d")
    QTest.keyClick(p.view, Qt.Key_Return)
    assert rows() == ["..", "in.txt"]

    select("in.txt")
    win.transfer(move=False)  # extract into panel 1 (b)
    _idle(win)
    assert (b / "in.txt").read_text() == "hi"

    win.transfer(move=True)  # read-only: no change, error dialog is stubbed
    win.delete()
    assert (a / "z.zip").exists()

    QTest.keyClick(p.view, Qt.Key_Backspace)
    assert rows() == ["..", "d"]
    QTest.keyClick(p.view, Qt.Key_Backspace)
    assert not p.in_archive and p.current_path() == str(a)
    _wait(lambda: "z.zip" in _names(p))


def test_search_dialog_navigates(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from commander.search_dialog import SearchDialog

    _app, win, _a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    (b / "deep").mkdir()
    (b / "deep" / "target.log").write_text("x")
    win.panels[0].set_path(str(tmp_path))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("target", True))

    def fake_exec(self):
        _wait(lambda: self.results.count() == 1 and self.worker.isFinished())
        assert self.results.item(0).text() == str(b / "deep" / "target.log")
        self._choose(self.results.item(0))

    monkeypatch.setattr(SearchDialog, "exec", fake_exec)
    win.search()
    assert win.panels[0].current_path() == str(b / "deep")


def test_ftp_browse_download_upload(tmp_path, monkeypatch):
    import threading

    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog

    _app, win, a, b = _setup(tmp_path, monkeypatch)
    root = tmp_path / "srv"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "r.txt").write_text("remote")
    auth = DummyAuthorizer()
    auth.add_user("u", "p", str(root), perm="elradfmwMT")
    srv = FTPServer(("127.0.0.1", 0), type("H", (FTPHandler,), {"authorizer": auth}))
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            srv.serve_forever(timeout=0.05, blocking=False)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        win.panels[0].set_path(str(a))
        win.panels[1].set_path(str(b))
        win.active_index = lambda: 1
        monkeypatch.setattr(QInputDialog, "getText",
                            lambda *_a, **_k: (f"ftp://u:p@127.0.0.1:{srv.address[1]}/", True))
        win.connect_ftp()
        p = win.panels[1]
        assert p.in_remote and p.remote_path == "/"

        def rows():
            m = p.view.model()
            return [m.item(r, 0).text() for r in range(m.rowCount())]

        assert rows() == ["..", "sub"]
        p._tab._open(p.view.model().index(1, 0))
        assert p.remote_path == "/sub" and rows() == ["..", "r.txt"]

        # download remote -> local panel 0
        win.active_index = lambda: 1
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        p.view.selectionModel().select(p.view.model().index(1, 0), flags)
        win.panels[0].set_path(str(a))
        win.transfer(move=False)
        _idle(win)
        assert (a / "r.txt").read_text() == "remote"

        # upload local f.txt -> remote /sub
        win.active_index = lambda: 0
        _wait(lambda: "f.txt" in _names(win.panels[0]))
        idx = win.panels[0].model.index(str(a / "f.txt"))
        win.panels[0].view.selectionModel().select(idx, flags)
        win.transfer(move=False)
        _idle(win)
        assert (root / "sub" / "f.txt").read_text() == "x"
        assert "f.txt" in rows()

        # Backspace goes up and stops at the root
        win.active_index = lambda: 1
        QTest.keyClick(p.view, Qt.Key_Backspace)
        assert p.remote_path == "/"
        QTest.keyClick(p.view, Qt.Key_Backspace)
        assert p.remote_path == "/" and p.in_remote
        p.set_path(str(b))
        assert not p.in_remote
    finally:
        stop.set()
        t.join(timeout=2)
        srv.close_all()
