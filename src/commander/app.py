import ctypes
import json
import sys
from contextlib import suppress
from ctypes import util
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
)

from . import archive, ops, rename
from .panel import FilePanel
from .remote import FtpClient, SftpClient
from .search_dialog import SearchDialog
from .viewer import ViewerDialog
from .workers import Worker

STATE_FILE = Path.home() / ".config" / "commander" / "state.json"


class MainWindow(QMainWindow):
    def __init__(self, state_file: Path | None = None):
        super().__init__()
        self.setWindowTitle("commander")
        self.setWindowIcon(app_icon())
        self.resize(1100, 650)
        self._worker: Worker | None = None
        self._state_file = state_file
        home = str(Path.home())
        self.panels = [FilePanel(home), FilePanel(home)]
        self.splitter = QSplitter()
        for p in self.panels:
            self.splitter.addWidget(p)
        self.setCentralWidget(self.splitter)
        QShortcut(QKeySequence("Tab"), self, activated=self.switch_panel)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.panels[self.active_index()].new_tab())
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.panels[self.active_index()].close_tab())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.connect_ftp)
        QShortcut(QKeySequence("F2"), self, activated=self.batch_rename)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.open_file(edit=False))
        QShortcut(QKeySequence("F4"), self, activated=lambda: self.open_file(edit=True))
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.transfer(move=False))
        QShortcut(QKeySequence("F6"), self, activated=lambda: self.transfer(move=True))
        QShortcut(QKeySequence("F7"), self, activated=self.make_dir)
        QShortcut(QKeySequence("F8"), self, activated=self.delete)
        self.panels[0].view.setFocus()
        if self._state_file is not None:
            self.load_state(self._state_file)

    def save_state(self, path: Path | None = None) -> None:
        target = path or self._state_file
        if target is None:
            return
        data = {
            "geometry": bytes(self.saveGeometry().toHex()).decode("ascii"),
            "size": [self.width(), self.height()],
            "splitter": self.splitter.sizes(),
            "active_panel": self.active_index(),
            "panels": [p.to_state() for p in self.panels],
        }
        with suppress(OSError):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_state(self, path: Path | None = None) -> None:
        target = path or self._state_file
        if target is None or not target.is_file():
            return
        with suppress(OSError, ValueError, TypeError, KeyError):
            data = json.loads(target.read_text(encoding="utf-8"))
            geom = data.get("geometry")
            if isinstance(geom, str) and geom:
                self.restoreGeometry(QByteArray.fromHex(geom.encode("ascii")))
            sz = data.get("size")
            if isinstance(sz, list) and len(sz) == 2 and all(isinstance(x, int) and x > 100 for x in sz):
                self.resize(sz[0], sz[1])
            panels = data.get("panels") or []
            for p, ps in zip(self.panels, panels, strict=False):
                if isinstance(ps, dict):
                    p.restore_state(ps)
            sizes = data.get("splitter")
            if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(x, int) and x > 0 for x in sizes):
                self.splitter.setSizes(sizes)
            active = int(data.get("active_panel", 0))
            if active in (0, 1):
                self.panels[active].view.setFocus()

    def closeEvent(self, event) -> None:
        self.save_state()
        ops.save_size_cache()
        super().closeEvent(event)

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
        if src_panel.in_remote and dst_panel.in_remote:
            self._error(ValueError("remote to remote transfer is not supported"))
            return
        if (src_panel.in_remote or dst_panel.in_remote) and move:
            self._error(ValueError("remote folders support copy (F5) only"))
            return
        if dst_panel.in_archive or (src_panel.in_archive and move):
            self._error(ValueError("archives are read-only; use F5 to extract"))
            return
        refresh = None
        if dst_panel.in_remote:
            client, dst = dst_panel.remote, dst_panel.remote_path
            srcs = src_panel.selected_paths()
            names = [s.name for s in srcs]
            try:
                taken = {e.name for e in client.listdir(dst)}
            except OSError as e:
                self._error(e)
                return
            conflicts = [n for n in names if n in taken]
            fn = client.upload
            verb = "Upload"

            def refresh():
                dst_panel.connect_remote(client, dst)
        elif src_panel.in_remote:
            dst = Path(dst_panel.current_path())
            client = src_panel.remote
            srcs = src_panel.remote_selection()
            names = [s.rsplit("/", 1)[-1] for s in srcs]
            conflicts = [n for n in names if (dst / n).exists()]
            fn = client.download
            verb = "Download"
        elif src_panel.in_archive:
            dst = Path(dst_panel.current_path())
            arc, entries = src_panel.archive, src_panel.archive_selection()
            names = [e.rsplit("/", 1)[-1] for e in entries]

            def fn(srcs, dst, overwrite, progress):
                archive.extract(arc, entries, dst, overwrite=overwrite, progress=progress)
            srcs = entries
            verb = "Extract"
        else:
            dst = Path(dst_panel.current_path())
            srcs = src_panel.selected_paths()
            names = [s.name for s in srcs]
            fn = ops.move if move else ops.copy
            verb = "Move" if move else "Copy"
        if not srcs:
            return
        if not self._ask(verb, f"{verb} {len(srcs)} item(s) to\n{dst}?"):
            return
        if not (dst_panel.in_remote or src_panel.in_remote):
            conflicts = [n for n in names if (dst / n).exists()]
        overwrite = False
        if conflicts:
            shown = ", ".join(conflicts[:5]) + (" ..." if len(conflicts) > 5 else "")
            if not self._ask("Overwrite", f"Already exists:\n{shown}\n\nOverwrite?"):
                return
            overwrite = True
        self._run_task(verb, len(srcs),
                       lambda progress: fn(srcs, dst, overwrite=overwrite, progress=progress),
                       on_done=refresh)

    def _run_task(self, title: str, total: int, task, on_done=None) -> None:
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
            if on_done:
                try:
                    on_done()
                except OSError as e:
                    self._error(e)

        worker.finished.connect(finished)
        self._worker = worker
        worker.start()

    def connect_ftp(self) -> None:
        panel = self.panels[self.active_index()]
        url, ok = QInputDialog.getText(
            self, "Connect", "URL (ftp|sftp://[user[:password]@]host[:port][/path]):")
        if not ok or not url.strip():
            return
        u = urlsplit(url.strip() if "://" in url else f"ftp://{url.strip()}")
        try:
            if u.scheme not in ("ftp", "sftp") or not u.hostname:
                raise ValueError("expected ftp|sftp://host[:port][/path]")
            sftp = u.scheme == "sftp"
            user = unquote(u.username) if u.username else ("" if sftp else "anonymous")
            password = unquote(u.password) if u.password is not None else ""
            if u.username and u.password is None:
                password, ok = QInputDialog.getText(
                    self, "Connect", f"Password for {user}@{u.hostname}:", QLineEdit.Password)
                if not ok:
                    return
            if sftp:
                client = SftpClient(u.hostname, u.port or 22, user, password)
            else:
                client = FtpClient(u.hostname, u.port or 21, user, password)
            try:
                panel.connect_remote(client, unquote(u.path) or "/")
            except OSError:
                client.close()
                raise
        except (OSError, ValueError) as e:
            self._error(e)

    def search(self) -> None:
        panel = self.panels[self.active_index()]
        if panel.in_remote:
            self._error(ValueError("search is not available on remote folders"))
            return
        pattern, ok = QInputDialog.getText(self, "Search", "Name (glob like *.txt, or text):")
        if not ok or not pattern.strip():
            return
        dlg = SearchDialog(Path(panel.current_path()), pattern, self)
        dlg.exec()
        if dlg.chosen is not None:
            target = dlg.chosen if dlg.chosen.is_dir() else dlg.chosen.parent
            panel.set_path(str(target))

    def batch_rename(self) -> None:
        panel = self.panels[self.active_index()]
        if panel.in_archive or panel.in_remote:
            self._error(ValueError("archives and remote folders are read-only"))
            return
        paths = panel.selected_paths()
        if not paths:
            return
        find, ok = QInputDialog.getText(self, "Rename", "Find (prefix 're:' for regex):")
        if not ok or not find:
            return
        replace, ok = QInputDialog.getText(self, "Rename", "Replace with:")
        if not ok:
            return
        regex = find.startswith("re:")
        try:
            pairs = rename.plan(paths, find[3:] if regex else find, replace, regex=regex)
            if not pairs:
                return
            preview = "\n".join(f"{s.name} -> {d.name}" for s, d in pairs[:10])
            if len(pairs) > 10:
                preview += f"\n... (+{len(pairs) - 10} more)"
            if not self._ask("Rename", f"Rename {len(pairs)} item(s)?\n\n{preview}"):
                return
            rename.apply(pairs)
        except (OSError, ValueError) as e:
            self._error(e)

    def open_file(self, edit: bool) -> None:
        paths = [p for p in self.panels[self.active_index()].selected_paths() if p.is_file()]
        if not paths:
            return
        try:
            ViewerDialog(paths[0], edit, self).exec()
        except (OSError, ValueError) as e:
            self._error(e)

    def make_dir(self) -> None:
        panel = self.panels[self.active_index()]
        if panel.in_remote:
            self._error(ValueError("remote folders are read-only"))
            return
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


def _set_macos_app_name(name: str) -> None:
    if sys.platform != "darwin":
        return
    with suppress(OSError, AttributeError, ValueError):
        cf = ctypes.cdll.LoadLibrary(util.find_library("CoreFoundation") or "")
        cf.CFBundleGetMainBundle.restype = ctypes.c_void_p
        cf.CFBundleGetInfoDictionary.argtypes = [ctypes.c_void_p]
        cf.CFBundleGetInfoDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        utf8 = 0x08000100
        val = cf.CFStringCreateWithCString(None, name.encode("utf-8"), utf8)
        info = cf.CFBundleGetInfoDictionary(cf.CFBundleGetMainBundle())
        if info:
            for key in (b"CFBundleName", b"CFBundleDisplayName"):
                cf.CFDictionarySetValue(info, cf.CFStringCreateWithCString(None, key, utf8), val)
        ls = ctypes.cdll.LoadLibrary(util.find_library("ApplicationServices") or "")
        ls._LSGetCurrentApplicationASN.argtypes = []
        ls._LSGetCurrentApplicationASN.restype = ctypes.c_void_p
        ls._LSSetApplicationInformationItem.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        ]
        ls._LSSetApplicationInformationItem.restype = ctypes.c_int32
        asn = ls._LSGetCurrentApplicationASN()
        if asn:
            key = ctypes.c_void_p.in_dll(ls, "_kLSDisplayNameKey")
            ls._LSSetApplicationInformationItem(-2, asn, key, val, None)


def app_icon(size: int = 256) -> QIcon:
    """Dual-pane file manager icon in macOS squircle style."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)

    # Outer squircle background (deep slate-blue gradient)
    bg = QLinearGradient(0, 0, 0, s)
    bg.setColorAt(0.0, QColor("#2563EB"))
    bg.setColorAt(1.0, QColor("#1E3A8A"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    pad = s * 0.06
    p.drawRoundedRect(QRectF(pad, pad, s - 2 * pad, s - 2 * pad), s * 0.22, s * 0.22)

    # Folder tab on top-left
    folder = QLinearGradient(0, s * 0.16, 0, s * 0.84)
    folder.setColorAt(0.0, QColor("#60A5FA"))
    folder.setColorAt(1.0, QColor("#3B82F6"))
    p.setBrush(folder)
    tab_path = QPainterPath()
    tab_path.addRoundedRect(QRectF(s * 0.14, s * 0.16, s * 0.30, s * 0.12), s * 0.04, s * 0.04)
    p.drawPath(tab_path)

    # Inner dual-panel window frame
    p.setBrush(QColor("#0F172A"))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.23, s * 0.72, s * 0.61), s * 0.07, s * 0.07)

    # Left & right panels
    for x in (s * 0.18, s * 0.51):
        p.setBrush(QColor("#F8FAFC"))
        p.drawRoundedRect(QRectF(x, s * 0.28, s * 0.31, s * 0.51), s * 0.04, s * 0.04)
        # Header bar inside each pane
        p.setBrush(QColor("#3B82F6"))
        p.drawRoundedRect(QRectF(x + s * 0.025, s * 0.31, s * 0.26, s * 0.045), s * 0.015, s * 0.015)
        # File rows inside each pane
        for i, highlight in enumerate((True, False, False, False)):
            ry = s * (0.39 + i * 0.085)
            p.setBrush(QColor("#93C5FD") if highlight else QColor("#CBD5E1"))
            p.drawRoundedRect(QRectF(x + s * 0.025, ry, s * 0.26, s * 0.05), s * 0.015, s * 0.015)

    # Transfer badge in the center between the two panels
    p.setBrush(QColor("#F59E0B"))
    p.setPen(QPen(QColor("#0F172A"), s * 0.02))
    p.drawEllipse(QRectF(s * 0.415, s * 0.455, s * 0.17, s * 0.17))
    p.setPen(QPen(QColor("#FFFFFF"), s * 0.024, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(QRectF(s * 0.455, s * 0.54, s * 0.09, 0).topLeft(),
               QRectF(s * 0.455, s * 0.54, s * 0.09, 0).topRight())
    p.drawLine(QRectF(s * 0.515, s * 0.505, s * 0.03, s * 0.035).topLeft(),
               QRectF(s * 0.515, s * 0.505, s * 0.03, s * 0.035).bottomRight())
    p.drawLine(QRectF(s * 0.515, s * 0.54, s * 0.03, s * 0.035).bottomLeft(),
               QRectF(s * 0.515, s * 0.54, s * 0.03, s * 0.035).topRight())
    p.end()
    return QIcon(px)


def _set_windows_app_id(app_id: str = "com.sungback.commander") -> None:
    if sys.platform != "win32":
        return
    with suppress(OSError, AttributeError, ValueError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def main() -> int:
    _set_macos_app_name("commander")
    _set_windows_app_id()
    ops.load_size_cache()
    app = QApplication(sys.argv)
    app.setApplicationName("commander")
    app.setApplicationDisplayName("commander")
    app.setWindowIcon(app_icon())
    win = MainWindow(state_file=STATE_FILE)
    app.aboutToQuit.connect(win.save_state)
    app.aboutToQuit.connect(ops.save_size_cache)
    win.show()
    _set_macos_app_name("commander")
    return app.exec()
