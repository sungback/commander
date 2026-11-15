"""Optional macOS helper; all other installations keep the Python scanner."""
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path


def _helper_path() -> Path | None:
    if sys.platform != "darwin":
        return None
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "build"))
    path = base / "commander-dir-size"
    return path if path.is_file() else None


def calculate(path: Path, cancel=None, on_progress=None) -> tuple[int, bool] | None:
    """Return size/completion, or None to request the portable fallback.

    The helper only reads metadata. It is always reaped, including on cancellation
    or when a progress callback raises. Never cache a failed/partial native result.
    """
    helper = _helper_path()
    if helper is None:
        return None
    if cancel and cancel():
        return 0, False
    try:
        proc = subprocess.Popen([str(helper), os.path.abspath(path)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None
    total, complete = 0, False
    pending = b""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                if cancel and cancel():
                    return total, False
                if not selector.select(0.05):
                    continue
                data = os.read(proc.stdout.fileno(), 4096)
                if not data:
                    break
                pending += data
                if len(pending) > 65536:
                    return None
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    record = json.loads(line)
                    value = record["bytes"]
                    if type(value) is not int or value < total:
                        return None
                    total = value
                    complete = record["complete"] is True
                    if on_progress and not complete:
                        on_progress(total)
        if proc.wait(timeout=1) == 0 and complete and not pending:
            return total, True
        return None
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        proc.stdout.close()
