"""File checksums: compute, write/parse `sha256sum`-style lists, verify (UI-independent)."""
import hashlib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from . import ops
from .transfer import CHUNK, Control, Stats, measure

ALGORITHMS = {"SHA-256": "sha256", "SHA-1": "sha1", "MD5": "md5"}
EXTENSIONS = {".sha256": "sha256", ".sha1": "sha1", ".md5": "md5"}
_BY_LENGTH = {64: "sha256", 40: "sha1", 32: "md5"}


def file_hash(path: Path, algo: str = "sha256", stats: Stats | None = None,
              control: Control | None = None) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            if stats:
                stats.done_bytes += len(chunk)
            if control:
                control.checkpoint()
    return h.hexdigest()


def expand(paths: Iterable[Path]) -> list[Path]:
    """Regular files in `paths`, folders expanded recursively (symlinks are not followed)."""
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_symlink():
            continue
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and not f.is_symlink()))
        elif p.is_file():
            out.append(p)
    return out


def hash_files(paths: Iterable[Path], base: Path, algo: str = "sha256",
               stats: Stats | None = None, control: Control | None = None,
               on_error: ops.OnError | None = None) -> str:
    """Checksum list text ('<hash> *<path relative to base>' per line) for `paths`."""
    files = expand(paths)
    if stats:
        stats.total_bytes, stats.total_items = measure(files, control)
    lines = []
    for f in files:
        if stats:
            stats.current = f.name
        try:
            digest = file_hash(f, algo, stats, control)
        except OSError as e:
            if on_error is None:
                raise
            on_error(f, e)
            continue
        lines.append(f"{digest} *{f.relative_to(base).as_posix()}")
        if stats:
            stats.done_items += 1
    return "\n".join(lines) + ("\n" if lines else "")


def parse_sums(text: str) -> list[tuple[str, str]]:
    """[(hex digest, relative name)] from 'digest  name' / 'digest *name' lines."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition(" ")
        name = name.strip().removeprefix("*")
        if name and len(digest) in _BY_LENGTH and all(c in "0123456789abcdefABCDEF" for c in digest):
            out.append((digest.lower(), name))
    return out


def _safe_rel(name: str) -> bool:
    """Names in a checksum list must stay inside the list's own folder."""
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return bool(parts) and not name.startswith(("/", "\\")) and ":" not in parts[0] \
        and ".." not in parts and "\0" not in name


def verify(sum_file: Path, stats: Stats | None = None,
           control: Control | None = None) -> list[tuple[str, str]]:
    """[(name, 'OK' | 'FAILED' | 'MISSING' | 'INVALID')] for every entry of a checksum list."""
    sum_file = Path(sum_file)
    entries = parse_sums(sum_file.read_text(encoding="utf-8", errors="replace"))
    if not entries:
        raise ValueError(f"no checksums found in {sum_file.name}")
    base = sum_file.parent
    if stats:
        targets = [base / n for _, n in entries if _safe_rel(n)]
        stats.total_bytes, stats.total_items = measure([t for t in targets if t.is_file()], control)
    results = []
    for digest, name in entries:
        algo = EXTENSIONS.get(sum_file.suffix.lower(), _BY_LENGTH[len(digest)])
        target = base / name
        if stats:
            stats.current = name
        if not _safe_rel(name):
            status = "INVALID"
        elif not target.is_file():
            status = "MISSING"
        else:
            try:
                status = "OK" if file_hash(target, algo, stats, control) == digest else "FAILED"
            except OSError:
                status = "FAILED"
        results.append((name, status))
        if stats:
            stats.done_items += 1
    return results
