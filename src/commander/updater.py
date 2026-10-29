"""GitHub Releases self-updater (UI-independent)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def apply_update_and_restart(
    staged: Path,
    target: Path,
    platform: str = sys.platform,
    pid: int | None = None,
) -> subprocess.Popen:
    """Spawn a detached OS helper that waits for `pid` to exit, replaces `target`, and relaunches."""
    cur_pid = os.getpid() if pid is None else pid
    staged = Path(staged).resolve()
    target = Path(target).resolve()

    if platform == "darwin":
        script = (
            'while kill -0 "$1" 2>/dev/null; do sleep 0.1; done; '
            'rm -rf "$3" && mv "$2" "$3" && open "$3"; '
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
        stage_root = staged.parent.parent
        ps_script = stage_root / "apply_update.ps1"
        ps_script.write_text(
            "param([int]$PidToWait, [string]$Staged, [string]$Target, [string]$StageRoot)\n"
            "Wait-Process -Id $PidToWait -ErrorAction SilentlyContinue\n"
            "Start-Sleep -Milliseconds 300\n"
            "if (Test-Path $Target) {\n"
            "  Get-ChildItem -Path $Target -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue\n"
            "} else {\n"
            "  New-Item -ItemType Directory -Force -Path $Target | Out-Null\n"
            "}\n"
            "Get-ChildItem -Path $Staged -Force | Copy-Item -Destination $Target -Recurse -Force\n"
            "Start-Process -FilePath (Join-Path $Target 'commander.exe')\n"
            "Remove-Item -Path $StageRoot -Recurse -Force -ErrorAction SilentlyContinue\n",
            encoding="utf-8",
        )
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            [
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
                str(staged),
                str(target),
                str(stage_root),
            ],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    raise ValueError(f"unsupported platform: {platform!r}")
