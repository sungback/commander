"""UI-independent FTP access (ftplib). Paths are posix strings on the server."""
import ftplib
import posixpath
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    is_dir: bool
    size: int = 0


@contextmanager
def _oserrors():
    """FTP protocol errors surface as OSError like other I/O failures."""
    try:
        yield
    except ftplib.Error as e:
        raise ConnectionError(str(e)) from e
    except EOFError as e:
        raise ConnectionError("connection closed") from e


def _safe_name(name: str) -> bool:
    """Server-supplied names become local paths, so reject anything that could escape."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\\0")


class FtpClient:
    def __init__(self, host: str, port: int = 21, user: str = "anonymous", password: str = "",
                 timeout: float = 15):
        self.host, self.port, self.user = host, port, user
        self._ftp = ftplib.FTP(encoding="utf-8")
        try:
            with _oserrors():
                self._ftp.connect(host, port, timeout)
                self._ftp.login(user, password)
        except OSError:
            self._ftp.close()
            raise

    def close(self) -> None:
        try:
            self._ftp.quit()
        except (OSError, ftplib.Error):
            self._ftp.close()

    def pwd(self) -> str:
        return self._ftp.pwd()

    def listdir(self, path: str) -> list[RemoteEntry]:
        """Folders first, then by name. Requires server MLSD support."""
        out = []
        with _oserrors():
            listing = list(self._ftp.mlsd(path, facts=["type", "size"]))
        for name, facts in listing:
            kind = facts.get("type", "")
            if kind in ("cdir", "pdir") or not _safe_name(name):
                continue
            if kind in ("dir", "file"):
                out.append(RemoteEntry(name, kind == "dir", int(facts.get("size", 0))))
        return sorted(out, key=lambda e: (not e.is_dir, e.name.lower()))

    def _is_dir(self, path: str) -> bool:
        cwd = self._ftp.pwd()
        try:
            self._ftp.cwd(path)
            return True
        except ftplib.error_perm:
            return False
        finally:
            self._ftp.cwd(cwd)

    def download(self, paths: Iterable[str], dst_dir: Path, overwrite: bool = False,
                 progress=None) -> None:
        """Download files and folders (recursively) into dst_dir."""
        dst_dir = Path(dst_dir)
        if not dst_dir.is_dir():
            raise NotADirectoryError(dst_dir)
        for p in paths:
            top = dst_dir / posixpath.basename(p.rstrip("/"))
            if top.exists() and not overwrite:
                raise FileExistsError(top)
            self._download(p, top)
            if progress:
                progress(Path(posixpath.basename(p)))

    def _download(self, remote: str, local: Path) -> None:
        if self._is_dir(remote):
            local.mkdir(exist_ok=True)
            for e in self.listdir(remote):
                self._download(posixpath.join(remote, e.name), local / e.name)
        else:
            with open(local, "wb") as f, _oserrors():
                self._ftp.retrbinary(f"RETR {remote}", f.write)

    def upload(self, srcs: Iterable[Path], remote_dir: str, overwrite: bool = False,
               progress=None) -> None:
        """Upload files and folders (recursively) into remote_dir."""
        for src in map(Path, srcs):
            existing = {e.name for e in self.listdir(remote_dir)}
            if src.name in existing and not overwrite:
                raise FileExistsError(posixpath.join(remote_dir, src.name))
            self._upload(src, posixpath.join(remote_dir, src.name))
            if progress:
                progress(src)

    def _upload(self, local: Path, remote: str) -> None:
        if local.is_dir() and not local.is_symlink():
            try:
                self._ftp.mkd(remote)
            except ftplib.error_perm:
                if not self._is_dir(remote):
                    raise PermissionError(f"cannot create {remote}") from None
            except ftplib.Error as e:
                raise ConnectionError(str(e)) from e
            for child in sorted(local.iterdir()):
                self._upload(child, posixpath.join(remote, child.name))
        else:
            with open(local, "rb") as f, _oserrors():
                self._ftp.storbinary(f"STOR {remote}", f)
