"""UI-independent read-only archive access (zip, tar, tar.gz/bz2/xz)."""
import os
import shutil
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import ops

SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


@dataclass(frozen=True)
class Entry:
    path: str  # posix path inside the archive, no trailing slash
    is_dir: bool
    size: int = 0


def is_archive(path: Path) -> bool:
    return path.name.lower().endswith(SUFFIXES) and path.is_file()


def _open(path: Path):
    try:
        if path.name.lower().endswith(".zip"):
            return zipfile.ZipFile(path)
        return tarfile.open(path)
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise ValueError(f"cannot read archive {path.name}: {e}") from e


def read_entries(path: Path) -> list[Entry]:
    """All files and directories. Links and special tar members are not listed."""
    with _open(path) as a:
        if isinstance(a, zipfile.ZipFile):
            return [Entry(i.filename.rstrip("/"), i.is_dir(), i.file_size) for i in a.infolist()]
        return [Entry(m.name.rstrip("/"), m.isdir(), m.size)
                for m in a.getmembers() if m.isfile() or m.isdir()]


def children(entries: Iterable[Entry], inner: str = "") -> list[Entry]:
    """Immediate children of `inner` (directories first, then by name); implicit dirs included."""
    prefix = inner + "/" if inner else ""
    found: dict[str, Entry] = {}
    for e in entries:
        if not e.path.startswith(prefix) or e.path == inner:
            continue
        rest = e.path[len(prefix):]
        name, sep, _ = rest.partition("/")
        if name in {"", ".", ".."}:
            continue
        if sep:
            found[name] = Entry(prefix + name, True)
        elif name not in found:
            found[name] = Entry(e.path, e.is_dir, e.size)
    return sorted(found.values(), key=lambda e: (not e.is_dir, e.path.rsplit("/", 1)[-1].lower()))


def _safe_rel(path: str) -> str:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts:
        raise ValueError(f"unsafe path in archive: {path!r}")
    return path


def extract(archive: Path, inner_paths: Iterable[str], dst_dir: Path, overwrite: bool = False,
            progress=None) -> None:
    """Extract the given entries (directories recursively) into dst_dir.

    Raises ValueError for paths escaping dst_dir. Links and special members are skipped.
    """
    dst_dir = Path(dst_dir)
    if not dst_dir.is_dir():
        raise NotADirectoryError(dst_dir)
    root = os.path.abspath(dst_dir)
    with _open(archive) as a:
        is_zip = isinstance(a, zipfile.ZipFile)
        members = a.infolist() if is_zip else a.getmembers()
        for sel in inner_paths:
            sel = _safe_rel(sel.rstrip("/"))
            parent = sel.rpartition("/")[0]
            top = dst_dir / sel.rsplit("/", 1)[-1]
            if top.exists() or top.is_symlink():
                if not overwrite:
                    raise FileExistsError(top)
                ops.remove(top)
            for m in members:
                name = (m.filename if is_zip else m.name).rstrip("/")
                if name != sel and not name.startswith(sel + "/"):
                    continue
                is_dir = m.is_dir() if is_zip else m.isdir()
                if not is_zip and not (is_dir or m.isfile()):
                    continue
                rel = _safe_rel(name[len(parent) + 1:] if parent else name)
                dest = os.path.abspath(dst_dir / rel)
                if os.path.commonpath([root, dest]) != root:
                    raise ValueError(f"unsafe path in archive: {name!r}")
                if is_dir:
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = a.open(m) if is_zip else a.extractfile(m)
                with src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            if progress:
                progress(Path(sel))
