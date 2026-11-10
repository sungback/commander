"""UI-independent archive access (zip, tar, tar.gz/bz2/xz): browse, extract, create."""
import os
import shutil
import tarfile
import zipfile
from collections.abc import Iterable
from contextlib import suppress
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
    dirs: set[str] = set()
    files: dict[str, int] = {}
    dir_sizes: dict[str, int] = {}
    for e in entries:
        if not e.path.startswith(prefix) or e.path == inner:
            continue
        rest = e.path[len(prefix):]
        name, sep, _ = rest.partition("/")
        if name in {"", ".", ".."} or "\\" in name or ":" in name:
            continue
        if sep or e.is_dir:
            dirs.add(name)
            if not e.is_dir:
                dir_sizes[name] = dir_sizes.get(name, 0) + e.size
        elif name not in dirs and name not in files:
            files[name] = e.size
    out = [Entry(prefix + d, True, dir_sizes.get(d, 0)) for d in dirs]
    out += [Entry(prefix + f, False, s) for f, s in files.items() if f not in dirs]
    return sorted(out, key=lambda e: (not e.is_dir, e.path.rsplit("/", 1)[-1].lower()))


def _safe_rel(path: str) -> str:
    if "\\" in path or path.startswith("/"):
        raise ValueError(f"unsafe path in archive: {path!r}")
    parts = path.split("/")
    if ".." in parts or (parts and ":" in parts[0]):
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


_TAR_MODES = {".tar": "w", ".tar.gz": "w:gz", ".tgz": "w:gz", ".tar.bz2": "w:bz2",
              ".tbz2": "w:bz2", ".tar.xz": "w:xz", ".txz": "w:xz"}


def can_create(name: str) -> bool:
    return name.lower().endswith(SUFFIXES)


class _Counting:
    """File wrapper that reports the bytes tarfile reads from it and honours pause/cancel."""

    def __init__(self, f, stats, control):
        self._f, self._stats, self._control = f, stats, control

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        if self._stats:
            self._stats.done_bytes += len(data)
        if self._control:
            self._control.checkpoint()
        return data


def _members(srcs: list[Path]):
    """(path, archive name, is_dir) for everything under srcs; symlinks and special files skipped."""
    for src in srcs:
        if src.is_symlink():
            continue
        if not src.is_dir():
            if src.is_file():
                yield src, src.name, False
            continue
        yield src, src.name, True
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames.sort()
            base = Path(dirpath)
            rel = src.name + "/" + base.relative_to(src).as_posix() if base != src else src.name
            for d in list(dirnames):
                if (base / d).is_symlink():
                    dirnames.remove(d)
                else:
                    yield base / d, f"{rel}/{d}", True
            for f in sorted(filenames):
                p = base / f
                if p.is_file() and not p.is_symlink():
                    yield p, f"{rel}/{f}", False


def create(dst: Path, srcs: Iterable[Path], overwrite: bool = False, stats=None, control=None,
           on_error=None) -> None:
    """Pack `srcs` (folders recursively) into a new zip / tar archive chosen by dst's extension.

    The archive is written under a temporary name and renamed when complete, so a cancelled
    or failed run leaves nothing behind. With `on_error`, unreadable files are reported and
    skipped.
    """
    from .transfer import CHUNK, PART_SUFFIX, measure

    dst = Path(dst)
    srcs = [Path(s) for s in srcs]
    lower = dst.name.lower()
    if not can_create(lower):
        raise ValueError(f"unsupported archive type: {dst.name} (use {', '.join(SUFFIXES)})")
    if not ops.is_valid_name(dst.name):
        raise ValueError(f"invalid name: {dst.name!r}")
    if (dst.exists() or dst.is_symlink()) and not overwrite:
        raise FileExistsError(dst)
    for s in srcs:
        if s.is_dir() and not s.is_symlink() and dst.resolve().is_relative_to(s.resolve()):
            raise ValueError(f"cannot create the archive inside {s.name}, which is being packed")
        if s.resolve() == dst.resolve():
            raise ValueError("cannot pack the archive into itself")
    if stats:
        stats.scanning = True
        stats.total_bytes, stats.total_items = measure(srcs, control)
        stats.scanning = False
    part = dst.with_name(dst.name + PART_SUFFIX)
    is_zip = lower.endswith(".zip")
    try:
        if is_zip:
            out = zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED)
        else:
            mode = next(m for s, m in _TAR_MODES.items() if lower.endswith(s))
            out = tarfile.open(part, mode)  # noqa: SIM115 - closed by the with block below
        with out:
            for path, name, is_dir in _members(srcs):
                if control:
                    control.checkpoint()
                if stats:
                    stats.current = path.name
                try:
                    if is_zip and is_dir:
                        out.write(path, name + "/")
                    elif is_zip:
                        info = zipfile.ZipInfo.from_file(path, name)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with open(path, "rb") as fin, out.open(info, "w", force_zip64=True) as fout:
                            reader = _Counting(fin, stats, control)
                            while chunk := reader.read(CHUNK):
                                fout.write(chunk)
                    else:
                        info = out.gettarinfo(str(path), name)
                        if is_dir:
                            out.addfile(info)
                        else:
                            with open(path, "rb") as fin:
                                out.addfile(info, _Counting(fin, stats, control))
                except OSError as e:
                    if on_error is None:
                        raise
                    on_error(path, e)
                    continue
                if stats and not is_dir:
                    stats.done_items += 1
        os.replace(part, dst)
    except BaseException:
        with suppress(OSError):
            part.unlink()
        raise
    ops.invalidate_size_cache(dst)
