"""UI-independent archive access (zip, tar, tar.gz/bz2/xz): browse, extract, create."""
import os
import tarfile
import tempfile
import zipfile
import zlib
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


def _is_hangul(ch: str) -> bool:
    code = ord(ch)
    return (
        0xAC00 <= code <= 0xD7A3  # 완성형 한글 음절 (가~힣)
        or 0x1100 <= code <= 0x11FF  # 한글 자모
        or 0x3130 <= code <= 0x318F  # 한글 호환 자모
    )


def _has_hangul(s: str) -> bool:
    return any(_is_hangul(c) for c in s)


def _detect_zip_encoding(zf: zipfile.ZipFile) -> str | None:
    """Detect whether non-flagged entries in `zf` are encoded in CP949 or UTF-8.

    Returns 'cp949', 'utf-8', or None if no non-ASCII non-flagged entries or undetermined.
    """
    raw_list: list[bytes] = []
    for info in zf.infolist():
        if info.flag_bits & 0x800:
            continue
        try:
            raw = info.filename.encode("cp437")
        except UnicodeEncodeError:
            continue
        if not raw.isascii():
            raw_list.append(raw)

    if not raw_list:
        return None

    has_invalid_utf8 = False
    all_valid_cp949 = True
    for raw in raw_list:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            has_invalid_utf8 = True

        try:
            raw.decode("cp949")
        except UnicodeDecodeError:
            all_valid_cp949 = False

    # If any entry is invalid UTF-8 and all are valid CP949, it's definitively CP949.
    if has_invalid_utf8 and all_valid_cp949:
        return "cp949"

    # If all entries are valid UTF-8, check if decoding as UTF-8 gives Korean characters.
    if not has_invalid_utf8:
        for raw in raw_list:
            try:
                if _has_hangul(raw.decode("utf-8")):
                    return "utf-8"
            except UnicodeDecodeError:
                pass

    # If all entries are valid CP949, check if decoding as CP949 gives Korean characters.
    if all_valid_cp949:
        for raw in raw_list:
            try:
                if _has_hangul(raw.decode("cp949")):
                    return "cp949"
            except UnicodeDecodeError:
                pass

    return None


def _decode_entry_filename(name: str, flag_bits: int, encoding: str | None) -> str:
    """Decode a zip entry filename using the detected encoding or heuristics."""
    if flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    if raw.isascii():
        return name

    if encoding:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass

    utf8_str: str | None = None
    try:
        utf8_str = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    cp949_str: str | None = None
    try:
        cp949_str = raw.decode("cp949")
    except UnicodeDecodeError:
        pass

    if utf8_str is not None and cp949_str is not None:
        if not _has_hangul(utf8_str) and _has_hangul(cp949_str):
            return cp949_str
        return utf8_str
    if cp949_str is not None:
        return cp949_str
    if utf8_str is not None:
        return utf8_str
    return name


def _normalize_zip_filenames(zf: zipfile.ZipFile) -> None:
    enc = _detect_zip_encoding(zf)
    for info in zf.infolist():
        new_name = _decode_entry_filename(info.filename, info.flag_bits, enc)
        if new_name != info.filename:
            info.filename = new_name
            zf.NameToInfo[new_name] = info


def _open(path: Path):
    try:
        if path.name.lower().endswith(".zip"):
            zf = zipfile.ZipFile(path)
            _normalize_zip_filenames(zf)
            return zf
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
            progress=None, control=None) -> None:
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
                if control:
                    control.checkpoint()
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
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                        if control:
                            control.checkpoint()
            if progress:
                progress(Path(sel))


def extract_all(path: Path, dst_dir: Path, *, on_conflict=None, stats=None, control=None,
                on_error=None) -> None:
    """Extract to a private staging directory, then merge using the safe transfer engine.

    The archive itself can never be a destination, even with an overwrite callback.
    """
    from .transfer import transfer

    path, dst_dir = Path(path), Path(dst_dir)
    if not dst_dir.is_dir():
        raise NotADirectoryError(dst_dir)
    entries = read_entries(path)
    for entry in entries:
        _safe_rel(entry.path)
    top = children(entries)
    if any((dst_dir / e.path).resolve() == path.resolve() for e in entries):
        raise ValueError("archive cannot overwrite itself during extraction")
    with tempfile.TemporaryDirectory(prefix=".commander-extract-", dir=dst_dir) as stage:
        stage_dir = Path(stage)
        try:
            extract(path, [e.path for e in top], stage_dir, control=control)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, zlib.error) as exc:
            raise ValueError(f"cannot extract archive {path.name}: {exc}") from exc
        def safe_conflict(src, dst):
            if src.is_dir() != (dst.is_dir() and not dst.is_symlink()):
                raise ValueError(f"cannot replace a file with a folder or vice versa: {dst}")
            return on_conflict(src, dst) if on_conflict else ""

        transfer([stage_dir / e.path for e in top], dst_dir, on_conflict=safe_conflict,
                 on_error=on_error, stats=stats, control=control)


def test_integrity(path: Path, *, stats=None, control=None) -> list[tuple[str, str]]:
    """Read every regular archive member; return member errors without extracting files."""
    from .transfer import CHUNK

    errors = []
    with _open(Path(path)) as arc:
        is_zip = isinstance(arc, zipfile.ZipFile)
        members = arc.infolist() if is_zip else arc.getmembers()
        files = [m for m in members if not m.is_dir()] if is_zip else [m for m in members if m.isfile()]
        if stats:
            stats.total_items = len(files)
            stats.total_bytes = sum(m.file_size if is_zip else m.size for m in files)
        for member in files:
            if control:
                control.checkpoint()
            name = member.filename if is_zip else member.name
            if stats:
                stats.current = name
            try:
                with (arc.open(member) if is_zip else arc.extractfile(member)) as source:
                    while chunk := source.read(CHUNK):
                        if stats:
                            stats.done_bytes += len(chunk)
                        if control:
                            control.checkpoint()
            except (OSError, EOFError, ValueError, RuntimeError, zipfile.BadZipFile,
                    tarfile.TarError, zlib.error) as exc:
                errors.append((name, str(exc)))
            if stats:
                stats.done_items += 1
    return errors


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
    from .transfer import CHUNK, _temporary_sibling, measure

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
    is_zip = lower.endswith(".zip")
    with _temporary_sibling(dst) as (part, temp):
        if is_zip:
            out = zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED)
        else:
            mode = next(m for s, m in _TAR_MODES.items() if lower.endswith(s))
            out = tarfile.open(fileobj=temp, mode=mode)  # noqa: SIM115
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
        temp.close()  # Windows cannot replace an open file.
        os.replace(part, dst)
    ops.invalidate_size_cache(dst)
