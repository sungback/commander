"""GitHub Releases self-updater (UI-independent)."""
import hashlib
import json
import os
import shutil
import ssl
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
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
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
    sha256: str = ""


def ssl_context() -> ssl.SSLContext:
    """TLS context that trusts the OS store plus the bundled certifi CA file.

    PyInstaller 앱(및 python.org macOS 빌드)은 OpenSSL 기본 CA 경로가 비어 있어
    `CERTIFICATE_VERIFY_FAILED`가 발생하므로 certifi 번들을 항상 함께 로드한다.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    return ctx


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
                if "/AppTranslocation/" in str(parent):
                    raise RuntimeError(
                        "commander.app이 임시 격리 위치에서 실행 중이라 교체할 수 없습니다. "
                        "앱을 /Applications 폴더로 옮긴 뒤 다시 실행해 주세요."
                    )
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
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "").strip()
    ver_tuple = parse_version(tag)
    norm_ver = ".".join(str(x) for x in ver_tuple)
    assets = data.get("assets") or []
    for asset in assets:
        if asset.get("name") == asset_name:
            url = str(asset.get("browser_download_url") or "")
            digest = str(asset.get("digest") or "")
            if url.startswith(("https://", "http://")):
                return ReleaseInfo(
                    version=norm_ver,
                    tag_name=tag,
                    asset_name=asset_name,
                    download_url=url,
                    notes=str(data.get("body") or "").strip(),
                    html_url=str(data.get("html_url") or ""),
                    sha256=digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else "",
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
    if dest_dir is not None:
        return _download_and_stage(info, Path(dest_dir), platform, progress, cancel, timeout)
    stage_dir = Path(tempfile.mkdtemp(prefix="commander_update_"))
    try:
        return _download_and_stage(info, stage_dir, platform, progress, cancel, timeout)
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)  # 실패·취소 시 받은 파일을 남기지 않는다
        raise


def _download_and_stage(
    info: ReleaseInfo,
    stage_dir: Path,
    platform: str,
    progress: Callable[[int, int], None] | None,
    cancel: Callable[[], bool] | None,
    timeout: float,
) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=True)
    archive_path = stage_dir / info.asset_name

    req = urllib.request.Request(info.download_url, headers={"User-Agent": f"commander/{__version__}"})
    sha = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
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
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    if cancel and cancel():
        raise UpdateCancelled
    if total and done != total:
        raise ValueError(f"download incomplete: {done} of {total} bytes")
    if info.sha256 and sha.hexdigest() != info.sha256:
        raise ValueError("downloaded archive does not match the release SHA-256 digest")

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


def stage_root_of(staged: Path) -> Path:
    """Return the staging root that `download_and_stage` created for `staged`.

    교체 헬퍼와 `discard_staged`가 통째로 지우는 폴더이므로, 'extracted' 폴더의 부모만 인정한다.
    """
    staged = Path(staged).resolve()
    for p in (staged, *staged.parents):
        if p.name == "extracted":
            return p.parent
    raise ValueError(f"not a staged update path: {staged}")


def discard_staged(staged: Path) -> None:
    """Delete a staged update that will not be applied (e.g. cancelled after download)."""
    with suppress(ValueError):
        shutil.rmtree(stage_root_of(staged), ignore_errors=True)


# $1=pid  $2=staged .app  $3=target .app  $4=staging root
MAC_APPLY_SCRIPT = """\
while kill -0 "$1" 2>/dev/null; do sleep 0.1; done
bak="$3.old"
rm -rf "$bak"
if [ ! -e "$3" ] || mv "$3" "$bak"; then
  if mv "$2" "$3"; then
    rm -rf "$bak"
  else
    rm -rf "$3"
    if [ -e "$bak" ]; then mv "$bak" "$3"; fi
  fi
fi
open "$3"
rm -rf "$4"
"""

WIN_APPLY_SCRIPT = """\
param([int]$PidToWait, [string]$Staged, [string]$Target, [string]$StageRoot)
Wait-Process -Id $PidToWait -ErrorAction SilentlyContinue
$Backup = "$Target.old"
if (Test-Path -LiteralPath $Backup) {
  Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
}
$moved = -not (Test-Path -LiteralPath $Target)
for ($i = 0; $i -lt 50 -and -not $moved; $i++) {
  try {
    Move-Item -LiteralPath $Target -Destination $Backup -Force -ErrorAction Stop
    $moved = $true
  } catch {
    Start-Sleep -Milliseconds 200
  }
}
if ($moved) {
  try {
    Copy-Item -LiteralPath $Staged -Destination $Target -Recurse -Force -ErrorAction Stop
    Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
  } catch {
    Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Backup) {
      Move-Item -LiteralPath $Backup -Destination $Target -Force
    }
  }
}
Start-Process -FilePath (Join-Path $Target 'commander.exe')
Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
"""


def apply_update_and_restart(
    staged: Path,
    target: Path,
    platform: str = sys.platform,
    pid: int | None = None,
) -> subprocess.Popen:
    """Spawn a detached OS helper that waits for `pid` to exit, swaps `target`, and relaunches.

    기존 앱은 `<target>.old`로 옮겨 두었다가 새 버전 배치에 성공하면 지우고, 실패하면 되돌린다.
    어느 경우든 마지막에 앱을 다시 실행하므로 교체 실패 시에도 기존 버전이 그대로 뜬다.
    """
    cur_pid = os.getpid() if pid is None else pid
    staged = Path(staged).resolve()
    target = Path(target).resolve()
    stage_root = stage_root_of(staged)
    helper_cwd = tempfile.gettempdir()  # 교체 대상 폴더를 헬퍼의 cwd로 잡고 있지 않게 한다

    if platform == "darwin":
        return subprocess.Popen(
            ["/bin/sh", "-c", MAC_APPLY_SCRIPT, "commander-updater",
             str(cur_pid), str(staged), str(target), str(stage_root)],
            start_new_session=True,
            cwd=helper_cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if platform == "win32":
        ps_script = stage_root / "apply_update.ps1"
        ps_script.write_text(WIN_APPLY_SCRIPT, encoding="utf-8")
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
            cwd=helper_cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    raise ValueError(f"unsupported platform: {platform!r}")
