import io
import json
import os
import tomllib
import urllib.request
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import commander
from commander import updater
from commander.app import MainWindow


class _FakeResp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert commander.__version__ == data["project"]["version"]


def test_parse_version_and_is_newer():
    assert updater.parse_version("v0.2.0") == (0, 2, 0)
    assert updater.parse_version("1.10.2-beta1") == (1, 10, 2)
    assert updater.is_newer("v0.3.0", "0.2.0")
    assert not updater.is_newer("v0.2.0", "0.2.0")
    assert not updater.is_newer("0.1.9", "0.2.0")
    for bad in ["", "v", "abc", "1.x.0"]:
        with pytest.raises(ValueError):
            updater.parse_version(bad)


def test_resolve_install_target(tmp_path):
    app_exe = tmp_path / "commander.app" / "Contents" / "MacOS" / "commander"
    app_exe.parent.mkdir(parents=True)
    app_exe.write_text("", encoding="utf-8")
    assert updater.resolve_install_target("darwin", str(app_exe), frozen=True) == (
        tmp_path / "commander.app"
    ).resolve()
    assert updater.resolve_install_target("darwin", str(tmp_path / "python"), frozen=False) == Path(
        "/Applications/commander.app"
    )

    win_exe = tmp_path / "dist" / "commander" / "commander.exe"
    win_exe.parent.mkdir(parents=True)
    win_exe.write_text("", encoding="utf-8")
    assert updater.resolve_install_target("win32", str(win_exe), frozen=True) == win_exe.parent.resolve()
    with pytest.raises(RuntimeError):
        updater.resolve_install_target("win32", str(win_exe), frozen=False)


def test_fetch_latest_release(monkeypatch):
    payload = {
        "tag_name": "v0.3.0",
        "body": "Bug fixes and improvements",
        "html_url": "https://github.com/sungback/commander/releases/tag/v0.3.0",
        "assets": [
            {
                "name": "commander.zip",
                "browser_download_url": "https://example.com/commander.zip",
            },
            {
                "name": "commander-win64.zip",
                "browser_download_url": "https://example.com/commander-win64.zip",
            },
        ],
    }
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResp(json.dumps(payload).encode("utf-8")),
    )
    mac_info = updater.fetch_latest_release(platform="darwin")
    assert mac_info.version == "0.3.0"
    assert mac_info.asset_name == "commander.zip"
    assert mac_info.download_url == "https://example.com/commander.zip"

    win_info = updater.fetch_latest_release(platform="win32")
    assert win_info.asset_name == "commander-win64.zip"


def test_download_and_stage_mac_and_windows(tmp_path, monkeypatch):
    # Create a fake macOS commander.zip
    mac_zip_buf = io.BytesIO()
    with zipfile.ZipFile(mac_zip_buf, "w") as zf:
        zf.writestr("commander.app/Contents/MacOS/commander", "#!/bin/sh\n")
    mac_bytes = mac_zip_buf.getvalue()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(mac_bytes))
    progress_calls: list[tuple[int, int]] = []
    info_mac = updater.ReleaseInfo(
        version="0.3.0",
        tag_name="v0.3.0",
        asset_name="commander.zip",
        download_url="https://example.com/commander.zip",
    )
    staged_mac = updater.download_and_stage(
        info_mac,
        dest_dir=tmp_path / "stage_mac",
        platform="darwin",
        progress=lambda d, t: progress_calls.append((d, t)),
    )
    assert staged_mac.name == "commander.app"
    assert (staged_mac / "Contents" / "MacOS" / "commander").is_file()
    assert progress_calls and progress_calls[-1] == (len(mac_bytes), len(mac_bytes))

    # Create a fake Windows commander-win64.zip
    win_zip_buf = io.BytesIO()
    with zipfile.ZipFile(win_zip_buf, "w") as zf:
        zf.writestr("commander/commander.exe", "MZ")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(win_zip_buf.getvalue())
    )
    info_win = updater.ReleaseInfo(
        version="0.3.0",
        tag_name="v0.3.0",
        asset_name="commander-win64.zip",
        download_url="https://example.com/commander-win64.zip",
    )
    staged_win = updater.download_and_stage(
        info_win, dest_dir=tmp_path / "stage_win", platform="win32"
    )
    assert (staged_win / "commander.exe").is_file()


def test_apply_update_and_restart_spawns_helper(tmp_path, monkeypatch):
    import subprocess

    calls: list[list[str]] = []

    class _DummyProc:
        pass

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return _DummyProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    staged = tmp_path / "stage" / "extracted" / "commander.app"
    staged.mkdir(parents=True)
    target = tmp_path / "Applications" / "commander.app"

    updater.apply_update_and_restart(staged, target, platform="darwin", pid=12345)
    assert calls[-1][0] == "/bin/sh"
    assert calls[-1][4:] == ["12345", str(staged.resolve()), str(target.resolve()), str(staged.parent.parent.resolve())]

    updater.apply_update_and_restart(staged, target, platform="win32", pid=12345)
    assert calls[-1][0] == "powershell.exe"
    ps_file = Path(calls[-1][calls[-1].index("-File") + 1])
    assert ps_file.is_file()
    assert "commander.exe" in ps_file.read_text(encoding="utf-8")


def test_ui_check_for_updates_flow(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    info_msgs: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, msg: info_msgs.append(msg))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)

    # 1) Up-to-date release: silent check shows nothing, manual check shows up-to-date message
    same_rel = updater.ReleaseInfo(
        version=commander.__version__,
        tag_name=f"v{commander.__version__}",
        asset_name="commander.zip",
        download_url="https://example.com/commander.zip",
    )
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **_k: same_rel)

    win = MainWindow()
    win.check_for_updates(silent_if_up_to_date=True)
    while win._update_worker is not None:
        QTest.qWait(10)
    assert info_msgs == []

    win.check_update_action.trigger()
    while win._update_worker is not None:
        QTest.qWait(10)
    assert len(info_msgs) == 1 and commander.__version__ in info_msgs[0]

    # 2) Newer release: downloads, stages, and invokes apply_update_and_restart
    new_rel = updater.ReleaseInfo(
        version="99.0.0",
        tag_name="v99.0.0",
        asset_name="commander.zip",
        download_url="https://example.com/commander.zip",
        notes="Major update",
    )
    staged_dir = tmp_path / "staged.app"
    staged_dir.mkdir()
    target_dir = tmp_path / "target.app"
    applied: list[tuple[Path, Path]] = []

    monkeypatch.setattr(updater, "fetch_latest_release", lambda **_k: new_rel)
    monkeypatch.setattr(updater, "resolve_install_target", lambda: target_dir)
    monkeypatch.setattr(
        updater,
        "download_and_stage",
        lambda _info, progress=None, cancel=None: (progress and progress(100, 100), staged_dir)[1],
    )
    monkeypatch.setattr(
        updater, "apply_update_and_restart", lambda s, t: applied.append((s, t))
    )
    monkeypatch.setattr(app, "quit", lambda: None)

    win.check_update_action.trigger()
    while win._update_worker is not None:
        QTest.qWait(10)
    assert applied == [(staged_dir, target_dir)]
