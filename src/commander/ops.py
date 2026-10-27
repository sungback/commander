"""UI-independent file operations."""
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

Progress = Callable[[Path], None]


def _target(src: Path, dst_dir: Path, overwrite: bool) -> Path:
    dst = dst_dir / src.name
    if dst.exists() or dst.is_symlink():
        if not overwrite:
            raise FileExistsError(dst)
        remove(dst)
    return dst


def _check(src: Path, dst_dir: Path) -> None:
    if not dst_dir.is_dir():
        raise NotADirectoryError(dst_dir)
    if src.is_dir() and not src.is_symlink() and dst_dir.resolve().is_relative_to(src.resolve()):
        raise ValueError(f"cannot place {src} inside itself")


def copy(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None) -> None:
    for src in map(Path, srcs):
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        if progress:
            progress(src)


def move(srcs: Iterable[Path], dst_dir: Path, overwrite: bool = False,
         progress: Progress | None = None) -> None:
    for src in map(Path, srcs):
        _check(src, dst_dir)
        dst = _target(src, dst_dir, overwrite)
        shutil.move(src, dst)
        if progress:
            progress(src)


def remove(path: Path) -> None:
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def delete(paths: Iterable[Path], progress: Progress | None = None) -> None:
    for p in map(Path, paths):
        remove(p)
        if progress:
            progress(p)


def make_dir(parent: Path, name: str) -> Path:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"invalid folder name: {name!r}")
    path = Path(parent) / name
    path.mkdir()
    return path
