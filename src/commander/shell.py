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


def tool_argv(command: str, files: list[Path], folder: Path, windows: bool = sys.platform == "win32") -> list[str]:
    """argv for a user-defined tool. Placeholders are filled per argument, never via a shell,
    so file names with spaces or shell metacharacters stay single, literal arguments.

    {file} first selected path, {files} all selected paths (expands to several arguments),
    {name} first selected file name, {dir} the current folder.
    """
    import shlex

    tokens = shlex.split(command, posix=not windows)
    if windows:
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in tokens]
    if not tokens:
        raise ValueError("empty tool command")
    needs_file = any(p in command for p in ("{file}", "{files}", "{name}"))
    if needs_file and not files:
        raise ValueError("this tool needs a selected file")
    first = files[0] if files else Path()
    argv: list[str] = []
    for tok in tokens:
        if tok == "{files}":
            argv.extend(str(f) for f in files)
            continue
        argv.append(tok.replace("{files}", " ".join(str(f) for f in files))
                    .replace("{file}", str(first)).replace("{name}", first.name)
                    .replace("{dir}", str(folder)))
    return argv


_OUTPUT_LIMIT = 1_000_000


def run_capture(command: str, cwd: Path, control=None) -> tuple[int, str]:
    """Run a command line the user typed through the system shell; (exit code, combined output).

    Polls `control` so the caller can cancel: the whole process group is then killed.
    """
    import locale
    import os
    import signal

    from .transfer import Cancelled

    posix = os.name != "nt"
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=posix)
    while True:
        try:
            out, _ = proc.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if control is not None and control.cancelled:
                if posix:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                proc.communicate()
                raise Cancelled from None
    text = out[-_OUTPUT_LIMIT:].decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")
    return proc.returncode, text
