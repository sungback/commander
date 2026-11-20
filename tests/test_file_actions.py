import pytest

from commander import file_actions, settings, shell


def test_associations_persist_and_ignore_bad_entries(tmp_path):
    config = tmp_path / "settings.json"
    state = settings.Settings(associations={".txt": "/an app/editor"})
    settings.save(state, config)
    assert settings.load(config).associations == state.associations
    config.write_text('{"associations":{".TXT":"/editor","bad":"/app", ".x":42}}')
    assert settings.load(config).associations == {".txt": "/editor"}


def test_open_with_passes_file_as_literal_argument(tmp_path, monkeypatch):
    app = tmp_path / "editor"
    app.touch()
    path = tmp_path / "file $(command).txt"
    path.touch()
    calls = []
    monkeypatch.setattr(shell, "launch", lambda argv: calls.append(argv))
    file_actions.open_with(path, str(app), platform="win32")
    assert calls == [[str(app), str(path)]]
    assert file_actions.open_associated(path, {".txt": str(app)})
    assert not file_actions.open_associated(path, {})


def test_macos_app_bundle_and_missing_app(tmp_path, monkeypatch):
    app = tmp_path / "My App.app"
    app.mkdir()
    path = tmp_path / "file.txt"
    path.touch()
    calls = []
    monkeypatch.setattr(shell, "launch", lambda argv: calls.append(argv))
    file_actions.open_with(path, str(app), platform="darwin")
    assert calls == [["open", "-a", str(app), str(path)]]
    with pytest.raises(ValueError):
        file_actions.open_with(path, str(tmp_path / "missing"))


def test_print_rejects_binary_without_showing_dialog(tmp_path, monkeypatch):
    path = tmp_path / "binary"
    path.write_bytes(b"\x00\x01")
    monkeypatch.setattr(file_actions, "QPrintDialog", lambda *args: pytest.fail("dialog opened"))
    with pytest.raises(ValueError, match="인쇄 형식"):
        file_actions.print_file(None, path)
