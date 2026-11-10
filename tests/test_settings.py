"""Settings, command table, shortcuts, command line and user tools."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox
from test_smoke import _idle, _names, _select_file, _setup, _wait

from commander import commands, settings, shell, theme
from commander.app import MainWindow
from commander.dialogs import TextDialog
from commander.settings_dialog import SettingsDialog, normalize_keys


def _win(tmp_path, monkeypatch, **kwargs):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    return win, a, b


def test_settings_roundtrip_and_validation(tmp_path):
    path = tmp_path / "cfg" / "settings.json"
    assert settings.load(path) == settings.Settings() and settings.load(None) == settings.Settings()
    s = settings.Settings(theme="dark", show_fn_bar=False, columns={"size": True, "type": False, "date": True},
                          shortcuts={"copy": ["F9"]},
                          tools=[{"name": "Code", "command": "code {dir}", "shortcut": "Ctrl+E"}])
    settings.save(s, path)
    assert settings.load(path) == s
    settings.save(s, None)  # no file configured: nothing happens
    path.write_text(json.dumps({
        "theme": "neon", "show_fn_bar": "yes", "columns": {"type": 0}, "shortcuts": {"copy": "F9", "x": ["A", 3]},
        "tools": [{"name": "", "command": "x"}, {"name": "ok", "command": "run"}, "junk"]}))
    loaded = settings.load(path)
    assert loaded.theme == "system" and loaded.show_fn_bar is True
    assert loaded.columns == {"size": True, "type": False, "date": True}
    assert loaded.shortcuts == {"x": ["A"]}
    assert loaded.tools == [{"name": "ok", "command": "run", "shortcut": ""}]
    path.write_text("{broken")
    assert settings.load(path) == settings.Settings()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_default_shortcuts_are_unique_and_parse(platform):
    QApplication.instance() or QApplication([])
    cmds = commands.default_commands(platform)
    assert len({c.id for c in cmds}) == len(cmds)
    mapping = commands.effective_shortcuts(cmds, {})
    assert commands.find_conflicts(mapping) == []
    for keys in mapping.values():
        assert normalize_keys(" ".join(keys)) == keys  # every default is a canonical Qt key name
    if platform == "darwin":  # Cmd+Q / Cmd+M / Cmd+H belong to the system there
        flat = [k for keys in mapping.values() for k in keys]
        assert not {"Ctrl+Q", "Ctrl+M", "Ctrl+H"} & set(flat)
    assert commands.effective_shortcuts(cmds, {"copy": ["F9"]})["copy"] == ["F9"]
    assert commands.find_conflicts({"a": ["F5"], "b": ["f5 "], "c": [""]}) == [("f5 ", "a", "b")]


def test_every_command_has_a_working_action(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    assert set(win.actions_by_name) == {c.id for c in win.commands}
    assert win.actions_by_name["copy"].shortcut().toString() == "F5"
    assert [k.toString() for k in win.actions_by_name["delete"].shortcuts()] == [
        "F8", "Del", "Ctrl+Backspace"]
    assert list(win.menus) == ["파일", "탐색", "도구"]
    _select_file(win)
    win.actions_by_name["copy"].trigger()
    _idle(win)
    assert (_b / "f.txt").exists()
    win.actions_by_name["filter"].trigger()
    assert not win.panels[0]._tab.filter_bar.isHidden()


def test_delete_shortcut_ignored_while_typing(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    _select_file(win)
    monkeypatch.setattr(QApplication, "focusWidget", lambda: win.cmd_edit)
    win.actions_by_name["delete"].trigger()
    win.actions_by_name["delete_permanent"].trigger()
    assert not win.busy() and (a / "f.txt").exists()
    monkeypatch.setattr(QApplication, "focusWidget", lambda: win.panels[0].view)
    win.actions_by_name["delete"].trigger()
    _idle(win)
    assert not (a / "f.txt").exists()


def test_settings_dialog_collects_and_validates(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    dlg = SettingsDialog(win.settings, win.commands, win)
    row = {c.id: i for i, c in enumerate(win.commands)}
    assert dlg.collect() == win.settings  # untouched dialog changes nothing

    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    dlg.columns["type"].setChecked(False)
    dlg.fn_bar.setChecked(False)
    dlg.keys.item(row["copy"], 1).setText("f9, ctrl+shift+k")
    dlg.add_tool("Editor", "code {dir}", "ctrl+e")
    dlg.add_tool("", "", "")  # blank rows are dropped
    out = dlg.collect()
    assert out.theme == "dark" and out.columns["type"] is False and out.show_fn_bar is False
    assert out.shortcuts == {"copy": ["F9", "Ctrl+Shift+K"]}  # only what differs from the defaults
    assert out.tools == [{"name": "Editor", "command": "code {dir}", "shortcut": "Ctrl+E"}]

    dlg.keys.item(row["move"], 1).setText("F9")  # same key as copy
    dlg.accept()
    assert "중복" in dlg.error.text() and dlg.result() == 0
    dlg.keys.item(row["move"], 1).setText("NotAKey")
    dlg.accept()
    assert "알 수 없는 키" in dlg.error.text()
    dlg.keys.item(row["move"], 1).setText("F6")
    dlg.tools.item(0, 2).setText("F9")  # a tool may not steal a command's key either
    dlg.accept()
    assert "중복" in dlg.error.text()
    dlg.tools.item(0, 2).setText("")
    dlg.tools.item(0, 1).setText("")
    dlg.accept()
    assert "이름과 명령" in dlg.error.text()
    dlg.tools.setCurrentCell(0, 0)
    dlg.remove_tool()
    dlg.reset_shortcuts()
    dlg.keys.setCurrentCell(row["search"], 1)
    from PySide6.QtGui import QKeySequence
    dlg.capture.setKeySequence(QKeySequence("Ctrl+Shift+F"))
    dlg.assign_captured()
    dlg.accept()
    assert dlg.result() == 1 and dlg.result_settings.shortcuts == {"search": ["Ctrl+Shift+F"]}


def test_apply_settings_shortcuts_bars_columns_and_persistence(tmp_path, monkeypatch):
    import zipfile

    cfg = tmp_path / "settings.json"
    _app, _w, a, _b = _setup(tmp_path, monkeypatch)
    win = MainWindow(settings_file=cfg)
    win.show()
    win.active_index = lambda: 0
    left = win.panels[0]
    left.set_path(str(a))
    assert not left.view.isColumnHidden(2) and win.cmd_bar.isVisible() and win.fn_bar.isVisible()

    def edit(dlg):
        row = {c.id: i for i, c in enumerate(win.commands)}
        dlg.keys.item(row["copy"], 1).setText("F9")
        dlg.columns["type"].setChecked(False)
        dlg.columns["date"].setChecked(False)
        dlg.command_line.setChecked(False)
        dlg.fn_bar.setChecked(False)
        dlg.accept()
        return dlg.result()

    monkeypatch.setattr(SettingsDialog, "exec", edit)
    win.actions_by_name["settings"].trigger()
    assert win.actions_by_name["copy"].shortcut().toString() == "F9"
    assert not win.cmd_bar.isVisible() and not win.fn_bar.isVisible()
    assert [left.view.isColumnHidden(c) for c in (1, 2, 3)] == [False, True, True]

    left.new_tab()  # new tabs follow the same column setup
    assert left.view.isColumnHidden(3)
    with zipfile.ZipFile(a / "z.zip", "w") as z:
        z.writestr("in.txt", "hi")
    left.set_path(str(a / "z.zip"))  # archive listings have fewer columns; nothing breaks
    assert not left.view.isColumnHidden(1)
    left.set_path(str(a))  # ...and the local columns are hidden again after the model swap
    assert [left.view.isColumnHidden(c) for c in (1, 2, 3)] == [False, True, True]

    assert json.loads(cfg.read_text())["shortcuts"] == {"copy": ["F9"]}
    win2 = MainWindow(settings_file=cfg)
    assert win2.actions_by_name["copy"].shortcut().toString() == "F9"
    assert win2.panels[0].view.isColumnHidden(2) and win2.cmd_bar.isHidden()
    monkeypatch.setattr(SettingsDialog, "exec", lambda dlg: 0)  # cancelled dialog changes nothing
    win.open_settings()
    assert win.actions_by_name["copy"].shortcut().toString() == "F9"


def test_theme_switching(tmp_path, monkeypatch):
    win, _a, _b = _win(tmp_path, monkeypatch)
    app = QApplication.instance()
    before = app.palette().window().color().name()
    try:
        win.settings.theme = "dark"
        win.apply_settings()  # goes through MainWindow without errors
        how = theme.apply(app, "dark")
        assert how in ("native", "palette")
        if how == "palette":  # a native switch may repaint a moment later, so only check ours
            assert theme.is_dark(app)
            assert theme.apply(app, "light") == "palette" and not theme.is_dark(app)
    finally:
        win.settings.theme = "system"
        win.apply_settings()
    assert theme.apply(app, "system") == "system"
    assert app.palette().window().color().name() == before


def test_user_tools_menu_buttons_and_argv(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    assert win.tool_bar.isHidden()
    win.settings.tools = [{"name": "Editor", "command": "edit --wait {files}", "shortcut": "Ctrl+E"},
                          {"name": "Here", "command": "term {dir}", "shortcut": ""}]
    win.apply_settings()
    assert not win.tool_bar.isHidden()
    labels = [x.text() for x in win.tools_menu.actions() if not x.isSeparator()]
    assert labels[-2:] == ["Editor", "Here"]
    launched, errors = [], []
    monkeypatch.setattr(shell, "launch", lambda argv, cwd=None: launched.append((argv, cwd)))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    win.panels[0].view.clearSelection()
    win._tool_actions[1].trigger()  # needs a file, nothing selected
    assert launched == [] and len(errors) == 1
    (a / "my file.txt").write_text("x")
    _wait(lambda: "my file.txt" in _names(win.panels[0]))
    win.panels[0].select_names({"f.txt", "my file.txt"})
    win._tool_actions[1].trigger()
    buttons = [win.tool_bar.layout().itemAt(i).widget() for i in range(2)]
    assert [b.text() for b in buttons] == ["Editor", "Here"]
    buttons[1].click()
    assert launched == [(["edit", "--wait", str(a / "f.txt"), str(a / "my file.txt")], a),
                        (["term", str(a)], a)]
    win.settings.tools = []
    win.apply_settings()  # removing tools cleans the menu and hides the bar
    assert win.tool_bar.isHidden() and "Editor" not in [x.text() for x in win.tools_menu.actions()]


def test_normalize_keys_separators():
    QApplication.instance() or QApplication([])
    assert normalize_keys("f8, del  ctrl+backspace") == ["F8", "Del", "Ctrl+Backspace"]
    assert normalize_keys("Ctrl+, F1") == ["Ctrl+,", "F1"]
    assert normalize_keys("Ctrl+,, F1") == ["Ctrl+,", "F1"]
    assert normalize_keys("  ") == []
    with pytest.raises(ValueError):
        normalize_keys("F5 bogus")


def test_tool_argv_placeholders(tmp_path):
    files = [tmp_path / "a b.txt", tmp_path / "c;rm -rf.txt"]
    assert shell.tool_argv('open -a "My App" {file}', files, tmp_path, windows=False) == [
        "open", "-a", "My App", str(files[0])]
    assert shell.tool_argv("x --name={name} {files} {dir}", files, tmp_path, windows=False) == [
        "x", "--name=a b.txt", str(files[0]), str(files[1]), str(tmp_path)]  # one argv item per file
    assert shell.tool_argv('"C:\\Tools\\x.exe" {dir}', [], tmp_path, windows=True) == [
        "C:\\Tools\\x.exe", str(tmp_path)]
    with pytest.raises(ValueError):
        shell.tool_argv("x {file}", [], tmp_path)
    with pytest.raises(ValueError):
        shell.tool_argv("   ", files, tmp_path)


def test_command_line_cd_history_and_escape(tmp_path, monkeypatch):
    win, a, b = _win(tmp_path, monkeypatch)
    (a / "sub").mkdir()
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    left = win.panels[0]
    win.actions_by_name["command_line"].trigger()
    assert win.cmd_prompt.text() == "a>" and win.cmd_prompt.toolTip() == str(a)
    edit = win.cmd_edit
    for text, expected in (("cd sub", a / "sub"), ("cd ..", a), (f'cd "{b}"', b)):
        edit.setText(text)
        QTest.keyClick(edit, Qt.Key_Return)
        assert left.current_path() == str(expected) and edit.text() == ""
    assert win.cmd_prompt.text() == "b>"  # the prompt follows the panel
    edit.setText("cd nowhere")
    QTest.keyClick(edit, Qt.Key_Return)
    assert len(errors) == 1 and left.current_path() == str(b)
    win.run_command_line("cd")
    assert left.current_path() == os.path.normpath(os.path.expanduser("~"))
    win.run_command_line("   ")

    QTest.keyClick(edit, Qt.Key_Up)
    assert edit.text() == "cd nowhere"
    QTest.keyClick(edit, Qt.Key_Up)
    assert edit.text() == f'cd "{b}"'
    QTest.keyClick(edit, Qt.Key_Down)
    QTest.keyClick(edit, Qt.Key_Down)
    assert edit.text() == ""
    edit.setText("half typed")
    QTest.keyClick(edit, Qt.Key_Escape)
    assert edit.text() == "" and isinstance(edit, QLineEdit)


def test_command_line_runs_in_folder_and_shows_output(tmp_path, monkeypatch):
    win, a, _b = _win(tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(TextDialog, "exec", lambda self: shown.append(
        (self.windowTitle(), self.view.toPlainText().strip())))
    py = f'"{sys.executable}"'
    cmd = f"{py} -c \"import os; print(os.path.basename(os.getcwd())); open('made.txt', 'w').close()\""
    win.run_command_line(cmd)
    _idle(win)
    assert shown == [(f"$ {cmd}", "a")] and (a / "made.txt").exists()

    win.run_command_line(f'{py} -c "pass"')  # silent success: no dialog
    _idle(win)
    assert len(shown) == 1
    win.run_command_line(f'{py} -c "import sys; sys.exit(3)"')  # failure is always reported
    _idle(win)
    assert len(shown) == 2

    win.run_command_line(f'{py} -c "import time; time.sleep(60)"')
    job = win.jobs[0]
    _wait(lambda: job.isRunning())
    win.queue.row(job).cancel_btn.click()  # cancelling kills the process
    _idle(win)
    assert len(shown) == 2 and job.cancelled
