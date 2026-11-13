import ctypes
import json
import os
import sys
from contextlib import suppress
from ctypes import util
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QByteArray, QLibraryInfo, QRectF, Qt, QTimer, QTranslator, QUrl
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
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
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

from . import (
    __version__,
    archive,
    checksum,
    compare,
    ops,
    rename,
    settings,
    shell,
    sync,
    theme,
    transfer,
    updater,
)
from .commands import default_commands, effective_shortcuts
from .dialogs import TextDialog
from .diff_dialog import DiffDialog
from .panel import FilePanel
from .preview_widget import PreviewWidget
from .queue_panel import QueuePanel
from .remote import FtpClient, SftpClient
from .rename_dialog import RenameDialog
from .search_dialog import SearchDialog
from .settings_dialog import SettingsDialog
from .sync_dialog import SyncDialog
from .viewer import ViewerDialog
from .workers import Job, UpdateCheckWorker, UpdateDownloadWorker, Worker

STATE_FILE = Path.home() / ".config" / "commander" / "state.json"


class MainWindow(QMainWindow):
    def __init__(self, state_file: Path | None = None, auto_check_updates: bool = False,
                 settings_file: Path | None = None):
        super().__init__()
        self._settings_file = settings_file
        self.settings = settings.load(settings_file)
        self.commands = default_commands()
        self.setWindowTitle(f"commander v{__version__}")
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
            p.current_changed.connect(lambda: self._quick_side is not None and self._quick_timer.start())
            p.current_changed.connect(self._update_prompt)
            p.context_requested.connect(lambda pos, p=p: self.show_context_menu(p, pos))
            # Deferred: the confirmation dialog must not run inside the drag-and-drop event.
            p.files_dropped.connect(
                lambda paths, target, move: QTimer.singleShot(
                    0, lambda: self._transfer_local(list(paths), Path(target), move)))
        fn_specs = [
            ("F2", "F2 이름변경", self.batch_rename),
            ("F3", "F3 보기", lambda: self.open_file(edit=False)),
            ("F4", "F4 편집", lambda: self.open_file(edit=True)),
            ("F5", "F5 복사", lambda: self.transfer(move=False)),
            ("F6", "F6 이동", lambda: self.transfer(move=True)),
            ("F7", "F7 새 폴더", self.make_dir),
            ("F8", "F8 삭제", self.delete),
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
        self.quick = PreviewWidget()  # Quick View: takes the inactive panel's place while on
        self.quick.hide()
        self._quick_side: int | None = None
        self._quick_timer = QTimer(self)
        self._quick_timer.setSingleShot(True)
        self._quick_timer.setInterval(60)
        self._quick_timer.timeout.connect(self._update_quick)
        self.tool_bar = QWidget()  # buttons for user-defined tools; hidden while there are none
        tool_layout = QHBoxLayout(self.tool_bar)
        tool_layout.setContentsMargins(2, 2, 2, 0)
        tool_layout.setSpacing(4)
        self.cmd_bar = QWidget()
        cmd_layout = QHBoxLayout(self.cmd_bar)
        cmd_layout.setContentsMargins(4, 0, 4, 0)
        self.cmd_prompt = QLabel()
        self.cmd_edit = _CommandEdit(self)
        cmd_layout.addWidget(self.cmd_prompt)
        cmd_layout.addWidget(self.cmd_edit, 1)
        root_layout.addWidget(self.tool_bar)
        root_layout.addWidget(self.splitter, 1)
        root_layout.addWidget(self.queue)
        root_layout.addWidget(self.cmd_bar)
        root_layout.addWidget(self.fn_bar)
        self.setCentralWidget(central)

        handlers = {
            "view": lambda: self.open_file(edit=False), "edit": lambda: self.open_file(edit=True),
            "copy": lambda: self.transfer(move=False), "move": lambda: self.transfer(move=True),
            "mkdir": self.make_dir, "rename": self.batch_rename, "delete": self.delete,
            "delete_permanent": lambda: self.delete(permanent=True), "pack": self.pack,
            "copy_path": self.copy_path, "switch_panel": self.switch_panel,
            "back": lambda: self.panels[self.active_index()].go_back(),
            "forward": lambda: self.panels[self.active_index()].go_forward(),
            "favorites": self.show_favorites,
            "filter": lambda: self.panels[self.active_index()].show_filter(),
            "search": self.search, "toggle_hidden": self.toggle_hidden, "flat": self.toggle_flat,
            "quick_view": self.toggle_quick_view,
            "new_tab": lambda: self.panels[self.active_index()].new_tab(),
            "close_tab": lambda: self.panels[self.active_index()].close_tab(),
            "command_line": self.focus_command_line, "connect": self.connect_ftp,
            "compare_dirs": self.compare_dirs, "sync": self.sync_dirs, "diff": self.diff_files,
            "checksum": self.checksum, "verify": self.verify_checksum, "settings": self.open_settings,
        }
        self.actions_by_name: dict[str, QAction] = {}
        self.menus: dict[str, QMenu] = {}
        for cmd in self.commands:
            act = QAction(cmd.label, self)
            act.setMenuRole(QAction.NoRole)
            if cmd.id in ("toggle_hidden", "flat", "quick_view"):
                act.setCheckable(True)
            act.triggered.connect(lambda _=False, c=cmd.id, h=handlers[cmd.id]: self._run_command(c, h))
            if cmd.menu not in self.menus:
                self.menus[cmd.menu] = self.menuBar().addMenu(cmd.menu)
            self.menus[cmd.menu].addAction(act)
            self.addAction(act)  # keeps the shortcut alive even if the menu bar is hidden
            self.actions_by_name[cmd.id] = act
        self.menus["탐색"].aboutToShow.connect(self._sync_view_actions)
        self.tools_menu = self.menus["도구"]
        self._tool_actions: list[QAction] = []
        help_menu = self.menuBar().addMenu("도움말")
        self.check_update_action = QAction("업데이트 확인...", self)
        self.check_update_action.setMenuRole(QAction.NoRole)
        self.check_update_action.triggered.connect(lambda: self.check_for_updates(silent_if_up_to_date=False))
        help_menu.addAction(self.check_update_action)
        self.about_action = QAction("정보", self)
        self.about_action.setMenuRole(QAction.NoRole)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addAction(self.about_action)
        self.apply_settings()
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

    def _run_command(self, command_id: str, handler) -> None:
        """Run a command from its shortcut or menu entry.

        Deleting is ignored while a text field has focus, so Del / Ctrl+Backspace typed in
        the path bar, filter box or command line can never reach the file list.
        """
        if command_id in _LIST_ONLY and isinstance(QApplication.focusWidget(), QLineEdit):
            return
        handler()

    def apply_settings(self) -> None:
        """Apply `self.settings`: shortcuts, bars, columns, theme and user tools."""
        st = self.settings
        for cmd_id, keys in effective_shortcuts(self.commands, st.shortcuts).items():
            self.actions_by_name[cmd_id].setShortcuts([QKeySequence(k) for k in keys])
        self.cmd_bar.setVisible(st.show_command_line)
        self.fn_bar.setVisible(st.show_fn_bar)
        hidden = {i + 1 for i, key in enumerate(settings.COLUMNS) if not st.columns.get(key, True)}
        for panel in self.panels:
            panel.set_hidden_columns(hidden)
        app = QApplication.instance()
        if st.theme != "system" or theme.customized():
            theme.apply(app, st.theme)
        for act in self._tool_actions:
            self.tools_menu.removeAction(act)
            self.removeAction(act)
            act.deleteLater()
        self._tool_actions = []
        layout = self.tool_bar.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if st.tools:
            self._tool_actions.append(self.tools_menu.addSeparator())
        for tool in st.tools:
            act = QAction(tool["name"], self)
            act.setMenuRole(QAction.NoRole)
            if tool.get("shortcut"):
                act.setShortcut(QKeySequence(tool["shortcut"]))
            act.triggered.connect(lambda _=False, t=tool: self.run_tool(t))
            self.tools_menu.addAction(act)
            self.addAction(act)
            self._tool_actions.append(act)
            btn = QPushButton(tool["name"])
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(tool["command"])
            btn.clicked.connect(lambda _=False, t=tool: self.run_tool(t))
            layout.addWidget(btn)
        layout.addStretch(1)
        self.tool_bar.setVisible(bool(st.tools))
        self._update_prompt()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.commands, self)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        self.settings = dlg.result_settings
        settings.save(self.settings, self._settings_file)
        self.apply_settings()

    def run_tool(self, tool: dict) -> None:
        """Start a user-defined tool with the active panel's selection (no shell involved)."""
        panel = self.panels[self.active_index()]
        folder = Path(panel.current_path())
        try:
            shell.launch(shell.tool_argv(tool["command"], panel.selected_paths(), folder), cwd=folder)
        except (OSError, ValueError) as e:
            self._error(e)

    def _update_prompt(self) -> None:
        folder = self.panels[self.active_index()].current_path()
        self.cmd_prompt.setText(f"{Path(folder).name or folder}>")
        self.cmd_prompt.setToolTip(folder)

    def focus_command_line(self) -> None:
        if not self.cmd_bar.isVisible():
            self.cmd_bar.show()
        self._update_prompt()
        self.cmd_edit.setFocus()
        self.cmd_edit.selectAll()

    def run_command_line(self, text: str) -> None:
        """Run what was typed in the command line, in the active panel's folder.

        `cd [path]` moves the panel; anything else goes to the system shell as a background
        job (cancellable from the queue) and its output is shown when it ends.
        """
        text = text.strip()
        if not text:
            return
        panel = self.panels[self.active_index()]
        cwd = Path(panel.current_path())
        if text == "cd" or text.startswith("cd "):
            arg = text[2:].strip().strip('"')
            target = Path(arg).expanduser() if arg else Path.home()
            target = target if target.is_absolute() else cwd / target
            if not target.is_dir():
                self._error(NotADirectoryError(str(target)))
                return
            panel.set_path(os.path.normpath(target))
            return

        def done(job: Job) -> None:
            code, output = job.result
            if output.strip() or code != 0:
                TextDialog(f"$ {text}", output, self, summary=f"종료 코드 {code}").exec()

        self.start_job(f"$ {text}", lambda job: shell.run_capture(text, cwd, job.control), on_done=done)

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
        self._refresh_flat()
        if errors:
            shown = "\n".join(errors[:10])
            more = f"\n... (외 {len(errors) - 10}개)" if len(errors) > 10 else ""
            QMessageBox.warning(self, job.title, f"{len(errors)}개 항목이 실패했습니다:\n\n{shown}{more}")
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
            if not self._ask("종료", f"백그라운드 작업 {len(self.jobs)}개가 아직 실행 중입니다.\n"
                                     "모두 취소하고 종료할까요?"):
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
        self._go_favorite_in_panel(self.panels[self.active_index()], path)

    def _go_favorite_in_panel(self, panel: FilePanel, path: str) -> None:
        if not Path(path).is_dir():
            self._error(FileNotFoundError(path))
            return
        panel.set_path(path)
        panel.view.setFocus()

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
        if self._quick_side is None:  # with Quick View on, the other panel is not visible
            self.panels[1 - self.active_index()].view.setFocus()

    def toggle_flat(self) -> None:
        panel = self.panels[self.active_index()]
        if panel.in_archive or panel.in_remote:
            self._error(ValueError("평면 보기는 로컬 폴더에서만 쓸 수 있습니다"))
            self._sync_view_actions()
            return
        panel.toggle_flat()
        self._sync_view_actions()

    def _refresh_flat(self) -> None:
        for panel in self.panels:
            panel.refresh_flat()

    def toggle_quick_view(self) -> bool:
        """Show a live preview of the file under the cursor in place of the inactive panel."""
        if self._quick_side is None:
            active = self.active_index()
            self._active = active
            self._quick_side = 1 - active
            self.splitter.replaceWidget(self._quick_side, self.quick)
            self.panels[self._quick_side].hide()
            self.quick.show()
            self.panels[active].view.setFocus()
            self._update_quick()
        else:
            side, self._quick_side = self._quick_side, None
            self._quick_timer.stop()
            self.splitter.replaceWidget(side, self.panels[side])
            self.quick.hide()
            self.quick.show_message("")
            self.panels[side].show()
        self._sync_view_actions()
        return self._quick_side is not None

    def _update_quick(self) -> None:
        if self._quick_side is None:
            return
        panel = self.panels[1 - self._quick_side]
        path = panel.current_local_path()
        if path is not None:
            self.quick.show_path(path)
        elif panel.in_archive or panel.in_remote:
            self.quick.show_message("압축 파일·원격 항목은 미리 볼 수 없습니다.\n먼저 로컬 폴더로 추출하거나 내려받으세요.")
        else:
            self.quick.show_message("")

    def toggle_hidden(self) -> bool:
        shown = self.panels[self.active_index()].toggle_hidden()
        self._sync_view_actions()
        return shown

    def _sync_view_actions(self) -> None:
        panel = self.panels[self.active_index()]
        self.actions_by_name["toggle_hidden"].setChecked(panel.show_hidden)
        self.actions_by_name["flat"].setChecked(panel.flat)
        self.actions_by_name["quick_view"].setChecked(self._quick_side is not None)

    def open_default(self, path: Path) -> None:
        """Open a local file with the application the OS associates with it."""
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._error(OSError(f"이 파일을 열 수 있는 앱이 없습니다: {path}"))

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
            menu.addAction("압축...\tAlt+F5", self.pack)
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
        QMessageBox.critical(self, "오류", f"{type(e).__name__}: {e}")

    def transfer(self, move: bool) -> None:
        src_panel = self.panels[self.active_index()]
        dst_panel = self.panels[1 - self.active_index()]
        if src_panel.in_remote and dst_panel.in_remote:
            self._error(ValueError("원격에서 원격으로의 전송은 지원하지 않습니다"))
            return
        if (src_panel.in_remote or dst_panel.in_remote) and move:
            self._error(ValueError("원격 폴더는 복사(F5)만 지원합니다"))
            return
        if dst_panel.in_archive or (src_panel.in_archive and move):
            self._error(ValueError("압축 파일은 읽기 전용입니다. F5로 추출하세요"))
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
            verb = "업로드"

            def refresh():
                dst_panel.connect_remote(client, dst)
        elif src_panel.in_remote:
            dst = Path(dst_panel.current_path())
            srcs = src_panel.remote_selection()
            names = [s.rsplit("/", 1)[-1] for s in srcs]
            fn = src_panel.remote.download
            verb = "다운로드"
        elif src_panel.in_archive:
            dst = Path(dst_panel.current_path())
            arc, srcs = src_panel.archive, src_panel.archive_selection()
            names = [e.rsplit("/", 1)[-1] for e in srcs]
            verb = "추출"
        else:
            self._transfer_local(src_panel.selected_paths(), Path(dst_panel.current_path()), move)
            return
        if not srcs:
            return
        if not self._ask(verb, f"{len(srcs)}개 항목을 다음 위치로 {verb}할까요?\n{dst}"):
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
                f"추출: {len(srcs)}개 항목 → {dst}",
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
        verb = "이동" if move else "복사"
        if not self._ask(verb, f"{len(srcs)}개 항목을 다음 위치로 {verb}할까요?\n{dst}"):
            return
        self.start_job(
            f"{verb}: {len(srcs)}개 항목 → {dst}",
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
        box.setWindowTitle("이미 있는 항목")
        box.setText(f"대상 위치에 이미 있습니다:\n{name}")
        box.setInformativeText(detail or "덮어쓰면 기존 항목이 새 항목으로 바뀝니다.")
        buttons = {box.addButton("덮어쓰기", QMessageBox.DestructiveRole): "overwrite",
                   box.addButton("건너뛰기", QMessageBox.AcceptRole): "skip"}
        if allow_rename:
            buttons[box.addButton("둘 다 유지", QMessageBox.AcceptRole)] = "rename"
        box.addButton(QMessageBox.Cancel)
        check = None
        if remaining is None or remaining:
            more = "" if remaining is None else f" (외 {remaining}개)"
            check = QCheckBox(f"모두 적용{more}")
            box.setCheckBox(check)
        box.exec()
        choice = buttons.get(box.clickedButton())
        if choice is None:
            return None
        return choice, bool(check and check.isChecked())

    def _run_task(self, title: str, total: int, task, on_done=None) -> None:
        if self._worker is not None:
            return  # one background task at a time
        dialog = QProgressDialog(title, "취소", 0, total, self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(500)
        worker = Worker(task, self)
        worker.progress.connect(lambda n, name: (dialog.setValue(n), dialog.setLabelText(name)))
        worker.failed.connect(lambda msg: QMessageBox.critical(self, "오류", msg))
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
            self, "원격 서버 연결", "URL (ftp|sftp://[user[:password]@]host[:port][/path]):")
        if not ok or not url.strip():
            return
        u = urlsplit(url.strip() if "://" in url else f"ftp://{url.strip()}")
        try:
            if u.scheme not in ("ftp", "sftp") or not u.hostname:
                raise ValueError("ftp|sftp://host[:port][/path] 형식이어야 합니다")
            sftp = u.scheme == "sftp"
            user = unquote(u.username) if u.username else ("" if sftp else "anonymous")
            password = unquote(u.password) if u.password is not None else ""
            if u.username and u.password is None:
                password, ok = QInputDialog.getText(
                    self, "원격 서버 연결", f"{user}@{u.hostname} 비밀번호:", QLineEdit.Password)
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
            self._error(ValueError("원격 폴더에서는 검색할 수 없습니다"))
            return
        pattern, ok = QInputDialog.getText(
            self, "파일 검색", "이름 (*.txt 같은 패턴 또는 이름 일부):", QLineEdit.Normal, "*")
        if not ok or not pattern.strip():
            return
        text, ok = QInputDialog.getText(
            self, "파일 검색", "포함할 텍스트 (비워 두면 이름으로만 검색):")
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
            self._error(ValueError("이 명령은 로컬 폴더에서만 쓸 수 있습니다"))
            return None
        return panel, panel.selected_paths()

    def _both_local(self) -> tuple[FilePanel, FilePanel] | None:
        """(active, other) panels when both show local folders; otherwise reports an error."""
        active = self.panels[self.active_index()]
        other = self.panels[1 - self.active_index()]
        if any(p.in_archive or p.in_remote for p in (active, other)):
            self._error(ValueError("양쪽 패널이 모두 로컬 폴더여야 합니다"))
            return None
        return active, other

    def compare_dirs(self) -> None:
        """Select, in both panels, the entries that are missing or newer than on the other side."""
        both = self._both_local()
        if both is None:
            return
        left, right = self.panels
        try:
            l_mark, r_mark = compare.differing_names(
                Path(left.current_path()), Path(right.current_path()),
                include_hidden=left.show_hidden or right.show_hidden)
        except (OSError, ValueError) as e:
            self._error(e)
            return
        left.select_names(l_mark)
        right.select_names(r_mark)
        if not l_mark and not r_mark:
            QMessageBox.information(self, "폴더 비교", "두 폴더의 내용이 같습니다.")

    def sync_dirs(self) -> None:
        """Compare both panels' folders recursively, review, then synchronize as a background job."""
        if self._both_local() is None:
            return
        left, right = (Path(p.current_path()) for p in self.panels)
        try:
            sync.check_roots(left, right)
        except (OSError, ValueError) as e:
            self._error(e)
            return
        dlg = SyncDialog(left, right, self.panels[self.active_index()].show_hidden, self)
        if dlg.exec() != SyncDialog.Accepted or not dlg.plan:
            return
        plan = dlg.plan
        self.start_job(
            f"동기화: {left.name} ↔ {right.name} ({len(plan)}개 항목)",
            lambda job: sync.apply(plan, left, right, job.stats, job.control, job.on_error))

    def diff_files(self) -> None:
        """Compare two files: both selected in the active panel, or one against the other panel."""
        both = self._both_local()
        if both is None:
            return
        active, other = both
        mine = [p for p in active.selected_paths() if p.is_file()]
        theirs = [p for p in other.selected_paths() if p.is_file()]
        if len(mine) == 2:
            a, b = mine
        elif len(mine) == 1 and len(theirs) == 1:
            a, b = mine[0], theirs[0]
        elif len(mine) == 1 and (Path(other.current_path()) / mine[0].name).is_file():
            a, b = mine[0], Path(other.current_path()) / mine[0].name
        else:
            self._error(ValueError(
                "파일 두 개를 선택하거나, 반대편 패널에도 있는 파일 하나를 선택하세요"))
            return
        if self.active_index() == 1 and len(mine) == 1:
            a, b = b, a  # keep the left panel's file on the left
        try:
            if compare.files_identical(a, b):
                QMessageBox.information(self, "파일 내용 비교", "두 파일의 내용이 같습니다.")
                return
            lines_a, lines_b = compare.read_lines(a), compare.read_lines(b)
        except OSError as e:
            self._error(e)
            return
        if lines_a is None or lines_b is None:
            QMessageBox.information(
                self, "파일 내용 비교",
                "두 파일의 내용이 다릅니다.\n(바이너리이거나 2MB를 넘는 파일은 줄 단위로 표시하지 않습니다.)")
            return
        DiffDialog(a, b, lines_a, lines_b, self).exec()

    def pack(self) -> None:
        """Pack the selection into a new archive in the other panel's folder (Alt+F5)."""
        sel = self._local_selection()
        if sel is None or not sel[1]:
            return
        panel, paths = sel
        other = self.panels[1 - self.active_index()]
        folder = Path(panel.current_path() if other.in_archive or other.in_remote
                      else other.current_path())
        stem = paths[0].name if len(paths) == 1 else (Path(panel.current_path()).name or "archive")
        name, ok = QInputDialog.getText(
            self, "압축 파일 만들기",
            f"{len(paths)}개 항목을 다음 폴더에 압축합니다:\n{folder}\n\n"
            "파일 이름 (.zip, .tar, .tar.gz, .tar.bz2, .tar.xz):",
            QLineEdit.Normal, f"{stem}.zip")
        if not ok or not name.strip():
            return
        dst = folder / name.strip()
        if not archive.can_create(dst.name) or not ops.is_valid_name(dst.name):
            self._error(ValueError(f"지원하지 않거나 잘못된 압축 파일 이름입니다: {name.strip()}"))
            return
        overwrite = dst.exists() or dst.is_symlink()
        if overwrite and not self._ask("압축 파일 만들기", f"{dst.name} 파일이 이미 있습니다. 덮어쓸까요?"):
            return
        self.start_job(
            f"압축: {len(paths)}개 항목 → {dst.name}",
            lambda job: archive.create(dst, paths, overwrite=overwrite, stats=job.stats,
                                       control=job.control, on_error=job.on_error))

    def checksum(self) -> None:
        """Compute checksums of the selected files (folders are included recursively)."""
        sel = self._local_selection()
        if sel is None or not sel[1]:
            return
        panel, paths = sel
        label, ok = QInputDialog.getItem(
            self, "체크섬 계산", "알고리즘:", list(checksum.ALGORITHMS), 0, False)
        if not ok:
            return
        algo = checksum.ALGORITHMS[label]
        base = Path(panel.current_path())
        stem = paths[0].name if len(paths) == 1 else "checksums"

        def done(job: Job) -> None:
            if job.result:
                TextDialog(f"{label} 체크섬", job.result, self,
                           save_as=base / f"{stem}.{algo}").exec()

        self.start_job(
            f"{label} 계산: {len(paths)}개 항목",
            lambda job: checksum.hash_files(paths, base, algo, job.stats, job.control, job.on_error),
            on_done=done)

    def verify_checksum(self) -> None:
        """Verify the files listed in the selected .sha256 / .sha1 / .md5 file."""
        sel = self._local_selection()
        if sel is None:
            return
        sums = [p for p in sel[1] if p.suffix.lower() in checksum.EXTENSIONS and p.is_file()]
        if len(sums) != 1:
            self._error(ValueError(".sha256, .sha1, .md5 파일 하나를 선택하세요"))
            return

        def done(job: Job) -> None:
            if not job.result:
                return
            bad = sum(1 for _, status in job.result if status != "OK")
            summary = (f"파일 {len(job.result)}개가 모두 일치합니다." if not bad
                       else f"파일 {len(job.result)}개 중 {bad}개가 일치하지 않습니다.")
            body = "\n".join(f"{status:<8}{name}" for name, status in job.result)
            TextDialog(f"검증 - {sums[0].name}", body, self, summary=summary).exec()

        self.start_job(f"검증: {sums[0].name}",
                       lambda job: checksum.verify(sums[0], job.stats, job.control), on_done=done)

    def batch_rename(self) -> None:
        panel = self.panels[self.active_index()]
        if panel.in_archive or panel.in_remote:
            self._error(ValueError("압축 파일과 원격 폴더는 읽기 전용입니다"))
            return
        paths = panel.selected_paths()
        if not paths:
            return
        dlg = RenameDialog(paths, self)
        if dlg.exec() != RenameDialog.Accepted or not dlg.pairs:
            return
        try:
            rename.apply(dlg.pairs)
        except (OSError, ValueError) as e:
            self._error(e)
        self._refresh_flat()

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
            self._error(ValueError("원격 폴더는 읽기 전용입니다"))
            return
        name, ok = QInputDialog.getText(self, "새 폴더", "이름:")
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
            self._error(ValueError("압축 파일과 원격 폴더는 읽기 전용입니다"))
            return
        paths = panel.selected_paths()
        if not paths:
            return
        if permanent:
            question = f"{len(paths)}개 항목을 영구 삭제할까요?\n이 작업은 되돌릴 수 없습니다."
        else:
            question = f"{len(paths)}개 항목을 휴지통으로 옮길까요?"
        if not self._ask("삭제", question):
            return
        fn = ops.delete if permanent else ops.trash
        verb = "영구 삭제" if permanent else "휴지통으로 이동"
        self.start_job(f"{verb}: {len(paths)}개 항목",
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

        dialog = QProgressDialog("업데이트 다운로드 중...", "취소", 0, 100, self)
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
                QMessageBox.critical(
                    self, "업데이트 설치 실패",
                    f"업데이트 도우미를 시작하지 못해 앱을 교체하지 않았습니다.\n\n{e}\n\n"
                    f"릴리스 페이지에서 직접 받아 설치해 주세요: {info.html_url or info.download_url}",
                )

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


_LIST_ONLY = {"delete", "delete_permanent"}


class _CommandEdit(QLineEdit):
    """Command line: Enter runs, Up/Down walk the history, Esc returns to the file list."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self._window = window
        self.history: list[str] = []
        self._pos = 0
        self.setPlaceholderText("명령 입력 후 Enter — 현재 폴더에서 실행 (cd 폴더 이동 지원)")

    def keyPressEvent(self, e) -> None:
        key = e.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            text = self.text().strip()
            if text:
                if not self.history or self.history[-1] != text:
                    self.history.append(text)
                    del self.history[:-50]
                self._pos = len(self.history)
                self.clear()
                self._window.run_command_line(text)
        elif key == Qt.Key_Up and self.history:
            self._pos = max(0, self._pos - 1)
            self.setText(self.history[self._pos])
        elif key == Qt.Key_Down and self.history:
            self._pos = min(len(self.history), self._pos + 1)
            self.setText(self.history[self._pos] if self._pos < len(self.history) else "")
        elif key == Qt.Key_Escape:
            self.clear()
            self._window.panels[self._window.active_index()].view.setFocus()
        else:
            super().keyPressEvent(e)


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
        return f"{'폴더' if p.is_dir() else ops.format_size(st.st_size)}, {when}"

    return f"기존 항목: {info(dst)}\n새 항목: {info(src)}\n원본 위치: {src.parent}"


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


def _install_korean_qt_texts(app: QApplication) -> None:
    """Qt's own buttons (Yes / No / Cancel ...) in Korean, matching the rest of the UI."""
    translator = QTranslator(app)
    # 1. Standard Qt translations directory
    trans_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_ko", trans_path):
        app.installTranslator(translator)
        return

    # 2. PyInstaller bundled search paths
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.extend([
            Path(meipass) / "translations",
            Path(meipass) / "PySide6" / "translations",
        ])
    exe_dir = Path(sys.executable).parent
    candidates.extend([
        exe_dir / "translations",
        exe_dir / "PySide6" / "translations",
        exe_dir / "_internal" / "translations",
        exe_dir / "_internal" / "PySide6" / "translations",
    ])
    for c in candidates:
        if c.is_dir() and translator.load("qtbase_ko", str(c)):
            app.installTranslator(translator)
            return


def main() -> int:
    _set_macos_app_name("commander")
    _set_windows_app_id()
    ops.load_size_cache()
    app = QApplication(sys.argv)
    app.setApplicationName("commander")
    # Leave display name empty so Qt on Windows doesn't append " - commander" to the title bar
    app.setApplicationDisplayName("")
    app.setWindowIcon(app_icon())
    _install_korean_qt_texts(app)
    win = MainWindow(state_file=STATE_FILE, auto_check_updates=True,
                     settings_file=settings.SETTINGS_FILE)
    app.aboutToQuit.connect(win.save_state)
    app.aboutToQuit.connect(ops.save_size_cache)
    win.show()
    _set_macos_app_name("commander")
    return app.exec()
