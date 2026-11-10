import ctypes
import json
import sys
from contextlib import suppress
from ctypes import util
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QByteArray, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
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
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import __version__, archive, checksum, ops, rename, shell, transfer, updater
from .dialogs import TextDialog
from .panel import FilePanel
from .queue_panel import QueuePanel
from .remote import FtpClient, SftpClient
from .search_dialog import SearchDialog
from .viewer import ViewerDialog
from .workers import Job, UpdateCheckWorker, UpdateDownloadWorker, Worker

STATE_FILE = Path.home() / ".config" / "commander" / "state.json"


class MainWindow(QMainWindow):
    def __init__(self, state_file: Path | None = None, auto_check_updates: bool = False):
        super().__init__()
        self.setWindowTitle("commander")
        self.setWindowIcon(app_icon())
        self.resize(1100, 650)
        self._worker: Worker | None = None  # modal task (remote transfers)
        self.jobs: list[Job] = []  # non-modal background jobs shown in the queue panel
        self._conflicts: list[tuple[Job, str, str]] = []
        self._asking = False
        self._update_worker: UpdateCheckWorker | UpdateDownloadWorker | None = None
        self._state_file = state_file
        self._active = 0
        self.favorites: list[str] = []
        home = str(Path.home())
        self.panels = [FilePanel(home), FilePanel(home)]
        self.splitter = QSplitter()
        for p in self.panels:
            self.splitter.addWidget(p)
            p.file_activated.connect(self.open_default)
            p.context_requested.connect(lambda pos, p=p: self.show_context_menu(p, pos))
            # Deferred: the confirmation dialog must not run inside the drag-and-drop event.
            p.files_dropped.connect(
                lambda paths, target, move: QTimer.singleShot(
                    0, lambda: self._transfer_local(list(paths), Path(target), move)))
        fn_specs = [
            ("F2", "F2 Rename", self.batch_rename),
            ("F3", "F3 View", lambda: self.open_file(edit=False)),
            ("F4", "F4 Edit", lambda: self.open_file(edit=True)),
            ("F5", "F5 Copy", lambda: self.transfer(move=False)),
            ("F6", "F6 Move", lambda: self.transfer(move=True)),
            ("F7", "F7 Mkdir", self.make_dir),
            ("F8", "F8 Delete", self.delete),
        ]
        self.fn_bar = QWidget()
        fn_layout = QHBoxLayout(self.fn_bar)
        fn_layout.setContentsMargins(2, 2, 2, 2)
        fn_layout.setSpacing(4)
        self.fn_buttons: dict[str, QPushButton] = {}
        for key, label, handler in fn_specs:
            btn = QPushButton(label)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(handler)
            fn_layout.addWidget(btn, 1)
            self.fn_buttons[key] = btn
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(2)
        self.queue = QueuePanel()
        root_layout.addWidget(self.splitter, 1)
        root_layout.addWidget(self.queue)
        root_layout.addWidget(self.fn_bar)
        self.setCentralWidget(central)
        self.tools_menu = self.menuBar().addMenu("도구")
        self.actions_by_name: dict[str, QAction] = {}
        for name, label, shortcut, handler in (
            ("checksum", "체크섬 계산...", "", self.checksum),
            ("verify", "체크섬 검증", "", self.verify_checksum),
        ):
            act = QAction(label, self)
            act.setMenuRole(QAction.NoRole)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(lambda _=False, h=handler: h())
            self.tools_menu.addAction(act)
            self.actions_by_name[name] = act
        help_menu = self.menuBar().addMenu("도움말")
        self.check_update_action = QAction("업데이트 확인...", self)
        self.check_update_action.setMenuRole(QAction.NoRole)
        self.check_update_action.triggered.connect(lambda: self.check_for_updates(silent_if_up_to_date=False))
        help_menu.addAction(self.check_update_action)
        self.about_action = QAction("정보", self)
        self.about_action.setMenuRole(QAction.NoRole)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addAction(self.about_action)
        QShortcut(QKeySequence("Tab"), self, activated=self.switch_panel)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.panels[self.active_index()].new_tab())
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.panels[self.active_index()].close_tab())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.connect_ftp)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.toggle_hidden)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.panels[self.active_index()].show_filter())
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.show_favorites)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, activated=self.copy_path)
        for seq in ("Alt+Left", "Ctrl+["):
            QShortcut(QKeySequence(seq), self, activated=lambda: self.panels[self.active_index()].go_back())
        for seq in ("Alt+Right", "Ctrl+]"):
            QShortcut(QKeySequence(seq), self, activated=lambda: self.panels[self.active_index()].go_forward())
        QShortcut(QKeySequence("Ctrl+."), self, activated=self.toggle_hidden)
        QShortcut(QKeySequence("F2"), self, activated=self.batch_rename)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.open_file(edit=False))
        QShortcut(QKeySequence("F4"), self, activated=lambda: self.open_file(edit=True))
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.transfer(move=False))
        QShortcut(QKeySequence("F6"), self, activated=lambda: self.transfer(move=True))
        QShortcut(QKeySequence("F7"), self, activated=self.make_dir)
        for seq in ("F8", "Del", "Ctrl+Backspace"):
            QShortcut(QKeySequence(seq), self, activated=self.delete)
        for seq in ("Shift+F8", "Shift+Del"):
            QShortcut(QKeySequence(seq), self, activated=lambda: self.delete(permanent=True))
        self.panels[0].view.setFocus()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        if self._state_file is not None:
            self.load_state(self._state_file)
        if auto_check_updates:
            self.check_for_updates(silent_if_up_to_date=True)

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
            "favorites": self.favorites,
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
            favs = data.get("favorites")
            if isinstance(favs, list):
                self.favorites = [f for f in favs if isinstance(f, str) and f]
            sizes = data.get("splitter")
            if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(x, int) and x > 0 for x in sizes):
                self.splitter.setSizes(sizes)
            active = int(data.get("active_panel", 0))
            if active in (0, 1):
                self._active = active
                self.panels[active].view.setFocus()

    def busy(self) -> bool:
        return bool(self.jobs) or self._worker is not None

    def start_job(self, title: str, run_fn, total_items: int = 0, on_done=None) -> Job:
        """Run `run_fn(job)` in the background; it appears in the queue panel until it ends.

        `on_done(job)` runs on the UI thread afterwards unless the job was cancelled.
        """
        job = Job(title, run_fn, total_items, self)
        job.conflict.connect(self._on_job_conflict)
        job.finished.connect(lambda: self._job_finished(job, on_done))
        self.jobs.append(job)
        self.queue.add(job)
        job.start()
        return job

    def _job_finished(self, job: Job, on_done=None) -> None:
        if job not in self.jobs:
            return
        self.jobs.remove(job)
        self.queue.remove(job)
        self._conflicts = [c for c in self._conflicts if c[0] is not job]
        errors = job.errors
        job.deleteLater()
        if errors:
            shown = "\n".join(errors[:10])
            more = f"\n... (+{len(errors) - 10} more)" if len(errors) > 10 else ""
            QMessageBox.warning(self, job.title, f"{len(errors)} item(s) failed:\n\n{shown}{more}")
        if on_done and not job.cancelled:
            on_done(job)

    def _on_job_conflict(self, job: Job, src: str, dst: str) -> None:
        """Answer jobs' conflict questions one dialog at a time, even when several jobs ask."""
        self._conflicts.append((job, src, dst))
        if self._asking:
            return
        self._asking = True
        try:
            while self._conflicts:
                job, src, dst = self._conflicts.pop(0)
                if job.cancelled or job not in self.jobs:
                    continue
                answer = self._ask_conflict(dst, None, True, _conflict_detail(Path(src), Path(dst)))
                job.answer(*(answer or (None, False)))
        finally:
            self._asking = False

    def closeEvent(self, event) -> None:
        if self.jobs:
            if not self._ask("Quit", f"{len(self.jobs)} background job(s) are still running.\n"
                                     "Cancel them and quit?"):
                event.ignore()
                return
            for job in list(self.jobs):
                job.cancel()
            for job in list(self.jobs):
                job.wait(5000)
        self.save_state()
        ops.save_size_cache()
        super().closeEvent(event)

    def _on_focus_changed(self, _old, now) -> None:
        """Remember the panel that last held focus (its list, path bar or filter box)."""
        for i, panel in enumerate(self.panels):
            if now is not None and panel.isAncestorOf(now):
                self._active = i

    def active_index(self) -> int:
        return self._active

    def is_favorite(self, path: str) -> bool:
        return any(ops.norm_key(f) == ops.norm_key(path) for f in self.favorites)

    def add_favorite(self, path: str) -> None:
        if path and not self.is_favorite(path):
            self.favorites.append(path)
            self.save_state()

    def remove_favorite(self, path: str) -> None:
        self.favorites = [f for f in self.favorites if ops.norm_key(f) != ops.norm_key(path)]
        self.save_state()

    def go_favorite(self, path: str) -> None:
        if not Path(path).is_dir():
            self._error(FileNotFoundError(path))
            return
        self.panels[self.active_index()].set_path(path)

    def _favorites_menu(self) -> QMenu:
        panel = self.panels[self.active_index()]
        local = not (panel.in_archive or panel.in_remote)
        here = panel.current_path()
        menu = QMenu(self)
        for fav in self.favorites:
            menu.addAction(fav, lambda f=fav: self.go_favorite(f))
        if self.favorites:
            menu.addSeparator()
        if self.is_favorite(here):
            menu.addAction("현재 폴더를 즐겨찾기에서 제거", lambda: self.remove_favorite(here))
        else:
            menu.addAction("현재 폴더를 즐겨찾기에 추가", lambda: self.add_favorite(here)).setEnabled(local)
        return menu

    def show_favorites(self) -> None:
        bar = self.panels[self.active_index()].path_bar
        self._favorites_menu().exec(bar.mapToGlobal(bar.rect().bottomLeft()))

    def switch_panel(self) -> None:
        self.panels[1 - self.active_index()].view.setFocus()

    def toggle_hidden(self) -> bool:
        return self.panels[self.active_index()].toggle_hidden()

    def open_default(self, path: Path) -> None:
        """Open a local file with the application the OS associates with it."""
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._error(OSError(f"no application could open {path}"))

    def copy_path(self) -> None:
        labels = self.panels[self.active_index()].selection_labels()
        QApplication.clipboard().setText("\n".join(labels))

    def reveal(self) -> None:
        panel = self.panels[self.active_index()]
        paths = panel.selected_paths()
        try:
            shell.reveal(paths[0] if paths else Path(panel.current_path()))
        except OSError as e:
            self._error(e)

    def open_terminal(self) -> None:
        try:
            shell.open_terminal(Path(self.panels[self.active_index()].current_path()))
        except OSError as e:
            self._error(e)

    def _context_menu(self, panel: FilePanel) -> QMenu:
        """Right-click menu for `panel`; entries follow its mode (local / archive / remote)."""
        menu = QMenu(self)
        local = not (panel.in_archive or panel.in_remote)
        paths = panel.selected_paths()
        selected = bool(paths or panel.archive_selection() or panel.remote_selection())
        if local and len(paths) == 1:
            target = paths[0]
            if target.is_dir() or archive.is_archive(target):
                menu.addAction("열기", lambda: panel.set_path(str(target)))
            else:
                menu.addAction("열기", lambda: self.open_default(target))
                menu.addAction("보기\tF3", lambda: self.open_file(edit=False))
                menu.addAction("편집\tF4", lambda: self.open_file(edit=True))
            menu.addSeparator()
        if selected:
            label = "복사" if local else ("추출" if panel.in_archive else "다운로드")
            menu.addAction(f"{label}\tF5", lambda: self.transfer(move=False))
        if local and paths:
            menu.addAction("이동\tF6", lambda: self.transfer(move=True))
            menu.addAction("이름 변경\tF2", self.batch_rename)
            menu.addAction("휴지통으로 이동\tF8", self.delete)
            menu.addAction("영구 삭제\tShift+F8", lambda: self.delete(permanent=True))
        if selected:
            menu.addSeparator()
        menu.addAction("경로 복사\tCtrl+Shift+C", self.copy_path)
        if local:
            menu.addAction(_REVEAL_LABEL, self.reveal)
            menu.addAction("여기서 터미널 열기", self.open_terminal)
            menu.addSeparator()
            menu.addAction("새 폴더\tF7", self.make_dir)
            here = panel.current_path()
            if not self.is_favorite(here):
                menu.addAction("현재 폴더를 즐겨찾기에 추가", lambda: self.add_favorite(here))
        return menu

    def show_context_menu(self, panel: FilePanel, pos) -> None:
        self._active = self.panels.index(panel)
        self._context_menu(panel).exec(pos)

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
            fn = client.upload
            verb = "Upload"

            def refresh():
                dst_panel.connect_remote(client, dst)
        elif src_panel.in_remote:
            dst = Path(dst_panel.current_path())
            srcs = src_panel.remote_selection()
            names = [s.rsplit("/", 1)[-1] for s in srcs]
            fn = src_panel.remote.download
            verb = "Download"
        elif src_panel.in_archive:
            dst = Path(dst_panel.current_path())
            arc, srcs = src_panel.archive, src_panel.archive_selection()
            names = [e.rsplit("/", 1)[-1] for e in srcs]
            verb = "Extract"
        else:
            self._transfer_local(src_panel.selected_paths(), Path(dst_panel.current_path()), move)
            return
        if not srcs:
            return
        if not self._ask(verb, f"{verb} {len(srcs)} item(s) to\n{dst}?"):
            return
        if dst_panel.in_remote:
            conflicts = [n for n in names if n in taken]
        else:
            conflicts = [n for n in names if (dst / n).exists() or (dst / n).is_symlink()]
        decisions = self._resolve_conflicts(conflicts, allow_rename=False)
        if decisions is None:
            return
        srcs = [s for s, n in zip(srcs, names, strict=True) if decisions.get(n) != "skip"]
        if not srcs:
            return
        overwrite = "overwrite" in decisions.values()
        if src_panel.in_archive:
            self.start_job(
                f"Extract {len(srcs)} item(s) to {dst}",
                lambda job: archive.extract(arc, srcs, dst, overwrite=overwrite, progress=job.on_item),
                total_items=len(srcs))
            return
        # Remote transfers stay modal: an FTP/SFTP connection cannot be shared with browsing.
        self._run_task(verb, len(srcs),
                       lambda progress: fn(srcs, dst, overwrite=overwrite, progress=progress),
                       on_done=refresh)

    def _transfer_local(self, srcs: list[Path], dst: Path, move: bool) -> None:
        """Copy/move local paths into the local folder `dst` (F5/F6 and drag & drop).

        Runs as a background job; existing folders are merged and each conflicting file is
        asked about when the job reaches it.
        """
        if not srcs:
            return
        verb = "Move" if move else "Copy"
        if not self._ask(verb, f"{verb} {len(srcs)} item(s) to\n{dst}?"):
            return
        self.start_job(
            f"{verb} {len(srcs)} item(s) to {dst}",
            lambda job: transfer.transfer(srcs, dst, move=move, on_conflict=job.ask,
                                          on_error=job.on_error, stats=job.stats,
                                          control=job.control))

    def _resolve_conflicts(self, names: list[str], allow_rename: bool) -> dict[str, str] | None:
        """Ask what to do with each already-existing name; None means the user cancelled."""
        decisions: dict[str, str] = {}
        for i, name in enumerate(names):
            answer = self._ask_conflict(name, len(names) - i - 1, allow_rename)
            if answer is None:
                return None
            choice, apply_all = answer
            if apply_all:
                decisions.update(dict.fromkeys(names[i:], choice))
                break
            decisions[name] = choice
        return decisions

    def _ask_conflict(self, name: str, remaining: int | None, allow_rename: bool,
                      detail: str = "") -> tuple[str, bool] | None:
        """(choice, apply_to_all) with choice in overwrite/skip/rename, or None to cancel.

        `remaining` is how many more conflicts are known; None when that is not known yet.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Already exists")
        box.setText(f"'{name}' already exists in the destination.")
        box.setInformativeText(detail or "Overwrite replaces the existing item.")
        buttons = {box.addButton("Overwrite", QMessageBox.DestructiveRole): "overwrite",
                   box.addButton("Skip", QMessageBox.AcceptRole): "skip"}
        if allow_rename:
            buttons[box.addButton("Keep both", QMessageBox.AcceptRole)] = "rename"
        box.addButton(QMessageBox.Cancel)
        check = None
        if remaining is None or remaining:
            more = "" if remaining is None else f" ({remaining} more)"
            check = QCheckBox(f"Apply to all{more}")
            box.setCheckBox(check)
        box.exec()
        choice = buttons.get(box.clickedButton())
        if choice is None:
            return None
        return choice, bool(check and check.isChecked())

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
        pattern, ok = QInputDialog.getText(
            self, "Search", "Name (glob like *.txt, or part of a name):", QLineEdit.Normal, "*")
        if not ok or not pattern.strip():
            return
        text, ok = QInputDialog.getText(
            self, "Search", "Containing text (leave empty to search by name only):")
        if not ok:
            return
        dlg = SearchDialog(Path(panel.current_path()), pattern, self, text=text)
        dlg.exec()
        if dlg.chosen is not None:
            target = dlg.chosen if dlg.chosen.is_dir() else dlg.chosen.parent
            panel.set_path(str(target))

    def _local_selection(self) -> tuple[FilePanel, list[Path]] | None:
        """Active panel and its selected local paths; reports an error outside local mode."""
        panel = self.panels[self.active_index()]
        if panel.in_archive or panel.in_remote:
            self._error(ValueError("this command works on local folders only"))
            return None
        return panel, panel.selected_paths()

    def checksum(self) -> None:
        """Compute checksums of the selected files (folders are included recursively)."""
        sel = self._local_selection()
        if sel is None or not sel[1]:
            return
        panel, paths = sel
        label, ok = QInputDialog.getItem(
            self, "Checksum", "Algorithm:", list(checksum.ALGORITHMS), 0, False)
        if not ok:
            return
        algo = checksum.ALGORITHMS[label]
        base = Path(panel.current_path())
        stem = paths[0].name if len(paths) == 1 else "checksums"

        def done(job: Job) -> None:
            if job.result:
                TextDialog(f"{label} checksums", job.result, self,
                           save_as=base / f"{stem}.{algo}").exec()

        self.start_job(
            f"{label} of {len(paths)} item(s)",
            lambda job: checksum.hash_files(paths, base, algo, job.stats, job.control, job.on_error),
            on_done=done)

    def verify_checksum(self) -> None:
        """Verify the files listed in the selected .sha256 / .sha1 / .md5 file."""
        sel = self._local_selection()
        if sel is None:
            return
        sums = [p for p in sel[1] if p.suffix.lower() in checksum.EXTENSIONS and p.is_file()]
        if len(sums) != 1:
            self._error(ValueError("select one .sha256, .sha1 or .md5 file to verify"))
            return

        def done(job: Job) -> None:
            if not job.result:
                return
            bad = sum(1 for _, status in job.result if status != "OK")
            summary = (f"All {len(job.result)} file(s) match." if not bad
                       else f"{bad} of {len(job.result)} file(s) did NOT verify.")
            body = "\n".join(f"{status:<8}{name}" for name, status in job.result)
            TextDialog(f"Verify {sums[0].name}", body, self, summary=summary).exec()

        self.start_job(f"Verify {sums[0].name}",
                       lambda job: checksum.verify(sums[0], job.stats, job.control), on_done=done)

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

    def delete(self, permanent: bool = False) -> None:
        """F8 moves the selection to the trash; Shift+F8 deletes permanently."""
        panel = self.panels[self.active_index()]
        if panel.in_archive or panel.in_remote:
            self._error(ValueError("archives and remote folders are read-only"))
            return
        paths = panel.selected_paths()
        if not paths:
            return
        if permanent:
            question = f"Permanently delete {len(paths)} item(s)?\nThis cannot be undone."
        else:
            question = f"Move {len(paths)} item(s) to the Trash?"
        if not self._ask("Delete", question):
            return
        fn = ops.delete if permanent else ops.trash
        verb = "Delete" if permanent else "Trash"
        self.start_job(f"{verb} {len(paths)} item(s)",
                       lambda job: fn(paths, progress=job.on_item, on_error=job.on_error),
                       total_items=len(paths))

    def show_about(self) -> None:
        QMessageBox.information(self, "정보", f"commander v{__version__}")

    def check_for_updates(self, silent_if_up_to_date: bool = False) -> None:
        if self._update_worker is not None:
            return
        worker = UpdateCheckWorker(self)

        def on_checked(info: updater.ReleaseInfo) -> None:
            _delete_when_done(worker)
            self._update_worker = None
            if updater.is_newer(info.version, __version__):
                self._prompt_and_install_update(info)
            elif not silent_if_up_to_date:
                QMessageBox.information(
                    self, "업데이트 확인", f"현재 최신 버전(v{__version__})을 사용 중입니다."
                )

        def on_failed(msg: str) -> None:
            _delete_when_done(worker)
            self._update_worker = None
            if not silent_if_up_to_date:
                QMessageBox.critical(self, "업데이트 확인 실패", msg)

        worker.checked.connect(on_checked)
        worker.failed.connect(on_failed)
        self._update_worker = worker
        worker.start()

    def _prompt_and_install_update(self, info: updater.ReleaseInfo) -> None:
        notes = f"\n\n{info.notes[:400]}" if info.notes else ""
        msg = (
            f"새 버전 v{info.version}이 출시되었습니다 (현재 v{__version__})."
            f"{notes}\n\n지금 다운로드하여 기존 앱을 교체하고 다시 시작할까요?"
        )
        if not self._ask("업데이트 발견", msg):
            return
        try:
            target = updater.resolve_install_target()
        except RuntimeError as e:
            QMessageBox.information(
                self, "업데이트 안내", f"{e}\n\n릴리스 페이지: {info.html_url or info.download_url}"
            )
            return

        dialog = QProgressDialog("업데이트 다운로드 중...", "Cancel", 0, 100, self)
        dialog.setWindowTitle("업데이트 설치")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        worker = UpdateDownloadWorker(info, self)

        def on_progress(done: int, total: int) -> None:
            if total > 0:
                dialog.setValue(min(100, int(done * 100 / total)))
                dialog.setLabelText(
                    f"다운로드 중... ({ops.format_size(done)} / {ops.format_size(total)})"
                )
            else:
                dialog.setLabelText(f"다운로드 중... ({ops.format_size(done)})")

        def on_staged(staged_path: Path) -> None:
            dialog.reset()
            dialog.deleteLater()
            _delete_when_done(worker)
            self._update_worker = None
            try:
                self.save_state()
                ops.save_size_cache()
                updater.apply_update_and_restart(staged_path, target)
                self.close()
                app = QApplication.instance()
                if app is not None:
                    app.quit()
            except OSError as e:
                self._error(e)

        def on_failed(err: str) -> None:
            dialog.reset()
            dialog.deleteLater()
            _delete_when_done(worker)
            self._update_worker = None
            QMessageBox.critical(self, "업데이트 설치 실패", err)

        def on_finished() -> None:
            if self._update_worker is worker:
                dialog.reset()
                dialog.deleteLater()
                _delete_when_done(worker)
                self._update_worker = None

        worker.progress.connect(on_progress)
        worker.staged.connect(on_staged)
        worker.failed.connect(on_failed)
        worker.finished.connect(on_finished)
        dialog.canceled.connect(worker.cancel)
        self._update_worker = worker
        worker.start()


def _delete_when_done(thread) -> None:
    """Delete a QThread only after it has really stopped (its result signal fires while it runs)."""
    if thread.isFinished():
        thread.deleteLater()
    else:
        thread.finished.connect(thread.deleteLater)


_REVEAL_LABEL = {"darwin": "Finder에서 보기", "win32": "탐색기에서 보기"}.get(
    sys.platform, "파일 관리자에서 보기")


def _conflict_detail(src: Path, dst: Path) -> str:
    """Size and modification time of both sides, to help decide which one to keep."""
    def info(p: Path) -> str:
        try:
            st = p.stat()
        except OSError:
            return "?"
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        return f"{'folder' if p.is_dir() else ops.format_size(st.st_size)}, {when}"

    return f"Existing: {info(dst)}\nNew: {info(src)}\nFrom: {src.parent}"


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
    win = MainWindow(state_file=STATE_FILE, auto_check_updates=True)
    app.aboutToQuit.connect(win.save_state)
    app.aboutToQuit.connect(ops.save_size_cache)
    win.show()
    _set_macos_app_name("commander")
    return app.exec()
