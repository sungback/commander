import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
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


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
