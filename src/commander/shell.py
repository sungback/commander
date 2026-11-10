"""Hand paths to the desktop: reveal in the system file manager, open a terminal (UI-independent)."""
import subprocess
import sys
from pathlib import Path


def reveal_command(path: Path, platform: str = sys.platform) -> list[str]:
    """argv that shows `path` in Finder / Explorer / the default Linux file manager."""
    path = Path(path)
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform == "win32":
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path if path.is_dir() else path.parent)]


def terminal_command(platform: str = sys.platform) -> list[str]:
    """argv that opens a terminal window; run it with cwd set to the wanted folder."""
    if platform == "darwin":
        return ["open", "-a", "Terminal", "."]
    if platform == "win32":
        return ["cmd.exe", "/c", "start", "cmd.exe"]
    return ["x-terminal-emulator"]


def launch(argv: list[str], cwd: Path | None = None) -> None:
    """Start a detached helper process (never through a shell). Raises OSError if it cannot start."""
    subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal(path: Path) -> None:
    launch(reveal_command(path))


def open_terminal(folder: Path) -> None:
    launch(terminal_command(), cwd=Path(folder))
