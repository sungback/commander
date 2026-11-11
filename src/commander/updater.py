"""GitHub Releases self-updater (UI-independent)."""
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPO = "sungback/commander"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
MAC_ASSET = "commander.zip"
WIN_ASSET = "commander-win64.zip"
CHUNK_SIZE = 64 * 1024
HELPER_MARKER = "helper_started"
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    asset_name: str
    download_url: str
    notes: str = ""
    html_url: str = ""


def parse_version(ver: str) -> tuple[int, ...]:
    """Parse 'v0.2.0' or '0.2.0-rc1' into an integer tuple (0, 2, 0)."""
    cleaned = ver.strip().lstrip("vV").split("-", 1)[0].strip()
    if not cleaned:
        raise ValueError(f"invalid version string: {ver!r}")
    parts = cleaned.split(".")
    if not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid version string: {ver!r}")
    return tuple(int(p) for p in parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def expected_asset_name(platform: str = sys.platform) -> str:
    if platform == "darwin":
        return MAC_ASSET
    if platform == "win32":
        return WIN_ASSET
    raise ValueError(f"auto-update is not supported on platform {platform!r}")


def resolve_install_target(
    platform: str = sys.platform,
    executable: str | None = None,
    frozen: bool | None = None,
) -> Path:
    """Determine the target application path to replace on update."""
    exe = Path(executable or sys.executable).resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if platform == "darwin":
        for parent in (exe, *exe.parents):
            if parent.name.lower().endswith(".app"):
                return parent
        return Path("/Applications/commander.app")
    if platform == "win32":
        if not is_frozen:
            raise RuntimeError(
                "Windows 소스 실행 모드에서는 자동 설치를 지원하지 않습니다. "
                "패키징된 commander.exe에서 실행해 주세요."
            )
        return exe.parent
    raise RuntimeError(f"자동 업데이트를 지원하지 않는 운영체제입니다: {platform}")


def _ssl_context() -> ssl.SSLContext:
    """Create an SSL context with trusted CA certificates, especially on macOS / packaged apps."""
    cafile: str | None = None
    try:
        import certifi

        cafile = certifi.where()
    except (ImportError, OSError):
        if sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file():
            cafile = "/etc/ssl/cert.pem"

    if cafile and Path(cafile).is_file():
        with suppress(ssl.SSLError, OSError):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def fetch_latest_release(
    api_url: str = API_URL,
    platform: str = sys.platform,
    timeout: float = 10.0,
) -> ReleaseInfo:
    """Query GitHub Releases API for the latest release and matching OS asset."""
    asset_name = expected_asset_name(platform)
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"commander/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "").strip()
    ver_tuple = parse_version(tag)
    norm_ver = ".".join(str(x) for x in ver_tuple)
    assets = data.get("assets") or []
    for asset in assets:
        if asset.get("name") == asset_name:
            url = str(asset.get("browser_download_url") or "")
            if url.startswith(("https://", "http://")):
                return ReleaseInfo(
                    version=norm_ver,
                    tag_name=tag,
                    asset_name=asset_name,
                    download_url=url,
                    notes=str(data.get("body") or "").strip(),
                    html_url=str(data.get("html_url") or ""),
                )
    raise ValueError(f"release {tag} does not contain asset {asset_name!r}")


def _extract_zip(archive_path: Path, extract_dir: Path, platform: str) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    if platform == "darwin" and Path("/usr/bin/ditto").is_file():
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive_path), str(extract_dir)],
            check=True,
        )
        return
    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(extract_dir)
    if platform == "darwin":
        for bin_path in (extract_dir / "commander.app" / "Contents" / "MacOS").glob("*"):
            with suppress(OSError):
                bin_path.chmod(bin_path.stat().st_mode | 0o755)


def download_and_stage(
    info: ReleaseInfo,
    dest_dir: Path | None = None,
    platform: str = sys.platform,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download the release archive and extract it into a staging directory."""
    stage_dir = Path(dest_dir) if dest_dir is not None else Path(tempfile.mkdtemp(prefix="commander_update_"))
    stage_dir.mkdir(parents=True, exist_ok=True)
    archive_path = stage_dir / info.asset_name

    req = urllib.request.Request(info.download_url, headers={"User-Agent": f"commander/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with archive_path.open("wb") as out:
            while True:
                if cancel and cancel():
                    raise UpdateCancelled
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    if cancel and cancel():
        raise UpdateCancelled

    extract_dir = stage_dir / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    _extract_zip(archive_path, extract_dir, platform)

    if platform == "darwin":
        app_bundle = extract_dir / "commander.app"
        exe = app_bundle / "Contents" / "MacOS" / "commander"
        if not exe.is_file():
            raise ValueError("downloaded archive does not contain a valid commander.app bundle")
        if Path("/usr/bin/xattr").is_file():
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["/usr/bin/xattr", "-cr", str(app_bundle)], check=False)
        return app_bundle

    if platform == "win32":
        nested = extract_dir / "commander"
        app_dir = nested if (nested / "commander.exe").is_file() else extract_dir
        if not (app_dir / "commander.exe").is_file():
            raise ValueError("downloaded archive does not contain commander.exe")
        return app_dir

    raise ValueError(f"unsupported platform: {platform!r}")


def _wait_for_helper(proc: subprocess.Popen, marker: Path, timeout: float) -> None:
    """Wait until the update helper creates `marker`; fail loudly if it dies or never starts."""
    deadline = time.monotonic() + timeout
    while not marker.exists():
        code = proc.poll()
        if code is not None:
            raise OSError(f"update helper exited before starting (exit code {code})")
        if time.monotonic() > deadline:
            with suppress(OSError):
                proc.kill()
            raise OSError(f"update helper did not start within {timeout:.0f} seconds")
        time.sleep(0.1)


def apply_update_and_restart(
    staged: Path,
    target: Path,
    platform: str = sys.platform,
    pid: int | None = None,
    helper_timeout: float = 20.0,
) -> subprocess.Popen:
    """Spawn a detached OS helper that waits for `pid` to exit, replaces `target`, and relaunches.

    On Windows this blocks until the helper confirms it is running (marker file) and raises
    OSError if it exits or does not start in time, so the caller never quits for nothing.
    """
    cur_pid = os.getpid() if pid is None else pid
    staged = Path(staged).resolve()
    target = Path(target).resolve()

    if platform == "darwin":
        script = (
            'while kill -0 "$1" 2>/dev/null; do sleep 0.1; done; '
            'BACKUP="${3}.old.$1"; '
            'if [ -e "$3" ]; then mv "$3" "$BACKUP"; fi; '
            'if mv "$2" "$3"; then '
            '  rm -rf "$BACKUP" && open "$3"; '
            'else '
            '  if [ -e "$BACKUP" ]; then mv "$BACKUP" "$3" && open "$3"; fi; '
            'fi; '
            'rm -rf "$4"'
        )
        stage_root = staged.parent.parent
        return subprocess.Popen(
            ["/bin/sh", "-c", script, "commander-updater", str(cur_pid), str(staged), str(target), str(stage_root)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if platform == "win32":
        # stage_root: tempdir that contains both apply_update.ps1 and the extracted/ subtree.
        # staged is always the directory holding commander.exe (e.g. …/extracted/commander/).
        # Walk up until we reach the directory whose parent is the system temp root so the
        # script file is written outside the extracted tree and survives until cleanup.
        stage_root = staged.parent  # …/extracted/
        if stage_root.name == "extracted":
            stage_root = stage_root.parent  # …/commander_update_XXXX/

        ps_script = stage_root / "apply_update.ps1"
        ps_script.write_text(
            "param([int]$PidToWait, [string]$Staged, [string]$Target, [string]$StageRoot)\n"
            "$logFile = Join-Path $env:TEMP 'commander_updater.log'\n"
            "function Write-Log($msg) {\n"
            "  $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'\n"
            "  \"[$ts] $msg\" | Out-File -FilePath $logFile -Append -Encoding utf8 -ErrorAction SilentlyContinue\n"
            "}\n"
            "Write-Log \"Update started. PID=$PidToWait Staged=$Staged Target=$Target\"\n"
            # 앱은 이 표시 파일이 생길 때까지 종료하지 않고 기다린다 (helper 시작 확인용)
            f"New-Item -ItemType File -Path (Join-Path $StageRoot '{HELPER_MARKER}') -Force | Out-Null\n"
            # ── 1. 프로세스 완전 종료 대기 (최대 10초, 미종료 시 강제 Stop) ──────────
            "$proc = Get-Process -Id $PidToWait -ErrorAction SilentlyContinue\n"
            "if ($proc) {\n"
            "  Write-Log 'Waiting for process to exit...'\n"
            "  $proc.WaitForExit(7000)\n"
            "  if (-not $proc.HasExited) {\n"
            "    Write-Log 'Process still active, forcing Stop-Process...'\n"
            "    Stop-Process -Id $PidToWait -Force -ErrorAction SilentlyContinue\n"
            "    Start-Sleep -Seconds 1\n"
            "  }\n"
            "}\n"
            "Start-Sleep -Seconds 1\n"
            # ── 2. 기존 설치 파일들 백업 폴더로 일괄 이동 ─────────────────────────
            # Windows에서도 실행 중이 아닌 폴더/파일의 rename은 원자적(instant)으로 즉시 완료됨
            "$backupDir = Join-Path $env:TEMP ('commander_old_' + $PidToWait)\n"
            "if (Test-Path $backupDir) { Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue }\n"
            "New-Item -ItemType Directory -Path $backupDir -Force | Out-Null\n"
            "$oldExe = Join-Path $Target 'commander.exe'\n"
            "$oldInternal = Join-Path $Target '_internal'\n"
            "if (Test-Path $oldExe) {\n"
            "  try { Move-Item -Path $oldExe -Destination $backupDir -Force -ErrorAction Stop }\n"
            "  catch { Write-Log \"Move commander.exe failed: $_\" }\n"
            "}\n"
            "if (Test-Path $oldInternal) {\n"
            "  try { Move-Item -Path $oldInternal -Destination $backupDir -Force -ErrorAction Stop }\n"
            "  catch { Write-Log \"Move _internal failed: $_\" }\n"
            "}\n"
            # ── 3. 새 파일 복사 (robocopy: 재시도 1회로 불필요한 지연 방지) ───────
            "Write-Log 'Copying new files via robocopy...'\n"
            "$rc = Start-Process 'robocopy.exe' -ArgumentList @($Staged, $Target, '/E', '/R:1', '/W:1', '/NP', '/NDL') -Wait -NoNewWindow -PassThru\n"
            "Write-Log \"Robocopy exit code: $($rc.ExitCode)\"\n"
            "if ($rc.ExitCode -ge 8) {\n"
            "  Write-Log 'Robocopy failed, rolling back from backup...'\n"
            "  Get-ChildItem -Path $backupDir -Force | Move-Item -Destination $Target -Force -ErrorAction SilentlyContinue\n"
            "}\n"
            # ── 4. 백업 디렉터리 정리 ─────────────────────────────────────────
            "Remove-Item -Path $backupDir -Recurse -Force -ErrorAction SilentlyContinue\n"
            # ── 5. 새 버전 재실행 ─────────────────────────────────────────────
            "$targetExe = Join-Path $Target 'commander.exe'\n"
            "if (Test-Path $targetExe) {\n"
            "  Write-Log \"Relaunching $targetExe\"\n"
            "  Start-Process -FilePath $targetExe -WorkingDirectory $Target\n"
            "} else {\n"
            "  Write-Log \"ERROR: $targetExe not found after update!\"\n"
            "}\n"
            # ── 6. 스테이지 디렉터리 정리 ────────────────────────────────────
            "Start-Sleep -Seconds 2\n"
            "if (Test-Path $StageRoot) {\n"
            "  Remove-Item -Path $StageRoot -Recurse -Force -ErrorAction SilentlyContinue\n"
            "}\n"
            "Write-Log 'Update script finished.'\n",
            # BOM: Windows PowerShell 5.1 reads BOM-less files in the ANSI code page (CP949 on Korean Windows)
            encoding="utf-8-sig",
        )
        marker = stage_root / HELPER_MARKER
        with suppress(FileNotFoundError):
            marker.unlink()
        args = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps_script),
            str(cur_pid),
            str(staged).rstrip("\\/"),
            str(target).rstrip("\\/"),
            str(stage_root).rstrip("\\/"),
        ]
        # CREATE_NO_WINDOW (not DETACHED_PROCESS): powershell.exe is a console program and needs a
        # (hidden) console. CREATE_BREAKAWAY_FROM_JOB: if commander runs inside a job object, the
        # helper must not be killed together with it when commander exits.
        base = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        popen_kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        try:
            proc = subprocess.Popen(args, creationflags=base | _CREATE_BREAKAWAY_FROM_JOB, **popen_kw)
        except PermissionError:
            # the job forbids breakaway; launching inside it is the best we can do
            proc = subprocess.Popen(args, creationflags=base, **popen_kw)
        _wait_for_helper(proc, marker, helper_timeout)
        return proc

    raise ValueError(f"unsupported platform: {platform!r}")
