"""Integration checks for file menu action wiring and mode restrictions."""
import zipfile

from test_smoke import _idle, _select_file, _setup, _wait

from commander import file_parts


def test_file_menu_shares_existing_actions_and_restricts_nonlocal(tmp_path, monkeypatch):
    _app, win, _a, _b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    menu = win.menus["파일"]
    for name in ("diff", "checksum", "verify"):
        assert win.actions_by_name[name] in menu.actions()
        assert win.actions_by_name[name] in win.menus["도구"].actions()
    _select_file(win)
    win._sync_file_actions()
    assert win.actions_by_name["split_file"].isEnabled()
    assert not win.actions_by_name["test_archive"].isEnabled()
    monkeypatch.setattr(type(win.panels[0]), "in_remote", property(lambda _self: True))
    win._sync_file_actions()
    assert not win.actions_by_name["split_file"].isEnabled()
    assert not win.actions_by_name["properties"].isEnabled()


def test_file_menu_unpack_and_split_submit_jobs(tmp_path, monkeypatch):
    _app, win, a, b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    path = a / "data.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("item.txt", "contents")
    monkeypatch.setattr(win, "_single_file", lambda: path)
    monkeypatch.setattr(win, "_ask", lambda *_args: True)
    win.unpack()
    _idle(win)
    assert (b / "item.txt").read_text() == "contents"
    source = a / "f.txt"
    monkeypatch.setattr(win, "_single_file", lambda: source)
    monkeypatch.setattr("commander.app.QInputDialog.getInt", lambda *_args: (1, True))
    win.split_file()
    _idle(win)
    manifest = b / (source.name + file_parts.MANIFEST_SUFFIX)
    _wait(lambda: manifest.exists())
    assert file_parts.read_manifest(manifest)["name"] == source.name
