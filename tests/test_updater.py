import hashlib
import io
import json
import os
import ssl
import subprocess
import sys
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

    translocated = "/private/var/folders/xx/T/AppTranslocation/ABC/d/commander.app/Contents/MacOS/commander"
    with pytest.raises(RuntimeError, match="Applications"):
        updater.resolve_install_target("darwin", translocated, frozen=True)

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
                "digest": "sha256:ABCDEF",
            },
            {
                "name": "commander-win64.zip",
                "browser_download_url": "https://example.com/commander-win64.zip",
            },
        ],
    }
    contexts: list[object] = []

    def fake_urlopen(*_a, **kwargs):
        contexts.append(kwargs.get("context"))
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    mac_info = updater.fetch_latest_release(platform="darwin")
    assert isinstance(contexts[-1], ssl.SSLContext)
    assert mac_info.sha256 == "abcdef"
    assert mac_info.version == "0.3.0"
    assert mac_info.asset_name == "commander.zip"
    assert mac_info.download_url == "https://example.com/commander.zip"

    win_info = updater.fetch_latest_release(platform="win32")
    assert win_info.asset_name == "commander-win64.zip"
    assert win_info.sha256 == ""


def test_ssl_context_trusts_certifi_when_system_store_is_empty(monkeypatch):
    # PyInstaller 앱처럼 OpenSSL 기본 CA 경로가 비어 있는 상황(CERTIFICATE_VERIFY_FAILED 재현 조건)
    monkeypatch.setattr(
        ssl, "create_default_context", lambda *_a, **_k: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    ctx = updater.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50


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
        sha256=hashlib.sha256(mac_bytes).hexdigest(),
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

    # 다이제스트 불일치 / 잘린 다운로드는 스테이징 전에 거부
    bad_digest = updater.ReleaseInfo(
        version="0.3.0",
        tag_name="v0.3.0",
        asset_name="commander.zip",
        download_url="https://example.com/commander.zip",
        sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        updater.download_and_stage(bad_digest, dest_dir=tmp_path / "stage_bad", platform="darwin")
    assert not (tmp_path / "stage_bad" / "extracted").exists()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResp(mac_bytes[:10], {"Content-Length": str(len(mac_bytes))}),
    )
    with pytest.raises(ValueError, match="incomplete"):
        updater.download_and_stage(info_mac, dest_dir=tmp_path / "stage_cut", platform="darwin")

    # 임시 스테이징 폴더(dest_dir 미지정)는 실패 시 남기지 않는다
    made: list[str] = []
    real_mkdtemp = updater.tempfile.mkdtemp

    def fake_mkdtemp(**kwargs):
        made.append(real_mkdtemp(dir=tmp_path, **kwargs))
        return made[-1]

    monkeypatch.setattr(updater.tempfile, "mkdtemp", fake_mkdtemp)
    with pytest.raises(ValueError, match="incomplete"):
        updater.download_and_stage(info_mac, platform="darwin")
    assert len(made) == 1 and not Path(made[0]).exists()

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

    # exe가 압축 루트에 있는 경우에도 스테이징 루트(= 정리 대상)는 'extracted'의 부모여야 한다
    flat = tmp_path / "stage_flat" / "extracted"
    flat.mkdir(parents=True)
    updater.apply_update_and_restart(flat, target, platform="win32", pid=12345)
    assert calls[-1][-1] == str((tmp_path / "stage_flat").resolve())
    with pytest.raises(ValueError):
        updater.apply_update_and_restart(tmp_path, target, platform="win32", pid=12345)

    # 적용하지 않을 스테이징은 루트째 정리하되, 스테이징 경로가 아니면 아무것도 지우지 않는다
    updater.discard_staged(flat)
    assert not (tmp_path / "stage_flat").exists()
    updater.discard_staged(tmp_path)
    assert tmp_path.is_dir() and staged.is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh helper")
def test_mac_apply_script_swaps_and_rolls_back(tmp_path, monkeypatch):
    # `open`을 가짜로 바꿔 실제 sh 헬퍼를 끝까지 실행한다.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    opened = tmp_path / "opened.txt"
    fake_open = bin_dir / "open"
    fake_open.write_text(f'#!/bin/sh\necho "$1" >> "{opened}"\n', encoding="utf-8")
    fake_open.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def make(root: Path, marker: str) -> Path:
        exe = root / "Contents" / "MacOS" / "commander"
        exe.parent.mkdir(parents=True)
        exe.write_text(marker, encoding="utf-8")
        return root

    target = make(tmp_path / "Apps" / "commander.app", "old")
    marker = target / "Contents" / "MacOS" / "commander"
    sleeper = subprocess.Popen(["sleep", "0.3"])  # 종료를 기다려야 하는 '실행 중인 앱'

    # 1) 정상 교체: 새 버전 배치, 백업·스테이징 폴더 정리, 새 앱 실행
    stage_root = tmp_path / "stage"
    staged = make(stage_root / "extracted" / "commander.app", "new")
    proc = updater.apply_update_and_restart(staged, target, platform="darwin", pid=sleeper.pid)
    sleeper.wait()
    assert proc.wait(timeout=10) == 0
    assert marker.read_text(encoding="utf-8") == "new"
    assert not Path(f"{target}.old").exists() and not stage_root.exists()
    assert opened.read_text(encoding="utf-8").splitlines() == [str(target.resolve())]

    # 2) 새 버전 배치 실패: 기존 버전으로 되돌리고 그대로 다시 실행
    missing = tmp_path / "stage2" / "extracted" / "commander.app"
    missing.parent.mkdir(parents=True)
    proc = updater.apply_update_and_restart(missing, target, platform="darwin", pid=sleeper.pid)
    assert proc.wait(timeout=10) == 0
    assert marker.read_text(encoding="utf-8") == "new"
    assert not Path(f"{target}.old").exists()
    assert len(opened.read_text(encoding="utf-8").splitlines()) == 2


def _wait_update_idle(win) -> None:
    from PySide6.QtTest import QTest

    while win._update_worker is not None:
        QTest.qWait(10)


def test_ui_check_for_updates_flow(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    info_msgs: list[str] = []
    questions: list[str] = []
    answer = [QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, msg: info_msgs.append(msg))
    monkeypatch.setattr(
        QMessageBox, "question", lambda _p, _t, msg: (questions.append(msg), answer[0])[1]
    )

    # 1) Up-to-date release: silent check shows nothing, manual check shows up-to-date message
    same_rel = updater.ReleaseInfo(
        version=commander.__version__,
        tag_name=f"v{commander.__version__}",
        asset_name="commander.zip",
        download_url="https://example.com/commander.zip",
    )
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **_k: same_rel)

    win = MainWindow()
    assert win.check_update_action.text() == "자동 업데이트"
    win.check_for_updates(silent_if_up_to_date=True)
    _wait_update_idle(win)
    assert info_msgs == []

    win.check_update_action.trigger()
    _wait_update_idle(win)
    assert len(info_msgs) == 1 and commander.__version__ in info_msgs[0]
    assert questions == []

    # 2) Newer release + menu click: one click downloads, stages and applies with no prompt
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
        lambda _info, progress=None, cancel=None: (
            progress and (progress(50, 100), progress(100, 100)),
            staged_dir,
        )[1],
    )
    monkeypatch.setattr(
        updater, "apply_update_and_restart", lambda s, t: applied.append((s, t))
    )
    monkeypatch.setattr(app, "quit", lambda: None)

    win.check_update_action.trigger()
    _wait_update_idle(win)
    assert applied == [(staged_dir, target_dir)]
    assert questions == []

    # 3) Newer release found by the silent startup check: asks once, installs only on Yes
    answer[0] = QMessageBox.No
    win.check_for_updates(silent_if_up_to_date=True)
    _wait_update_idle(win)
    assert len(questions) == 1 and "99.0.0" in questions[0]
    assert len(applied) == 1

    answer[0] = QMessageBox.Yes
    win.check_for_updates(silent_if_up_to_date=True)
    _wait_update_idle(win)
    assert len(questions) == 2 and len(applied) == 2

    # 4) Cancel pressed while the archive is being extracted: staged files are discarded, app untouched
    import threading

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QProgressDialog

    late_stage = tmp_path / "late" / "extracted" / "commander.app"
    late_stage.mkdir(parents=True)
    started, resume = threading.Event(), threading.Event()

    def slow_stage(_info, progress=None, cancel=None):
        started.set()
        resume.wait(5)
        return late_stage

    monkeypatch.setattr(updater, "download_and_stage", slow_stage)
    win.check_update_action.trigger()
    while not started.is_set():
        QTest.qWait(10)
    dialogs = [d for d in win.findChildren(QProgressDialog) if d.isVisible()]
    assert len(dialogs) == 1
    dialogs[0].cancel()
    resume.set()
    _wait_update_idle(win)
    assert len(applied) == 2 and not (tmp_path / "late").exists()

    # 5) Failures surface on a manual click only, and never reach the install step
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, msg: errors.append(msg))

    def boom(**_k):
        raise OSError("certificate verify failed")

    monkeypatch.setattr(updater, "fetch_latest_release", boom)
    win.check_for_updates(silent_if_up_to_date=True)
    _wait_update_idle(win)
    assert errors == []
    win.check_update_action.trigger()
    _wait_update_idle(win)
    assert len(errors) == 1 and updater.RELEASES_URL in errors[0]
    assert len(applied) == 2
