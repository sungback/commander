"""UI-independent FTP/SFTP access (ftplib, paramiko). Paths are posix strings on the server."""
import ftplib
import posixpath
import stat
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import paramiko


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
    except paramiko.SSHException as e:
        raise ConnectionError(str(e)) from e
    except EOFError as e:
        raise ConnectionError("connection closed") from e


def _safe_name(name: str) -> bool:
    """Server-supplied names become local paths, so reject anything that could escape."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\\0")


def _unlink_if_symlink(local: Path) -> None:
    """Never write through a pre-existing local symlink; replace the link itself."""
    if local.is_symlink():
        local.unlink()


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class FtpClient:
    scheme = "ftp"

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
                out.append(RemoteEntry(name, kind == "dir", _int(facts.get("size"))))
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
            if (top.exists() or top.is_symlink()) and not overwrite:
                raise FileExistsError(top)
            self._download(p, top)
            if progress:
                progress(Path(posixpath.basename(p)))

    def _download(self, remote: str, local: Path) -> None:
        _unlink_if_symlink(local)
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


class SftpClient:
    """Same interface as FtpClient over SSH. Unknown host keys are rejected by default
    (connect once with ssh to add them to known_hosts); symlinks are not listed."""
    scheme = "sftp"

    def __init__(self, host: str, port: int = 22, user: str = "", password: str = "",
                 timeout: float = 15, host_key_policy: paramiko.MissingHostKeyPolicy | None = None):
        self.host, self.port, self.user = host, port, user
        self._ssh = paramiko.SSHClient()
        self._ssh.load_system_host_keys()
        self._ssh.set_missing_host_key_policy(host_key_policy or paramiko.RejectPolicy())
        try:
            with _oserrors():
                self._ssh.connect(host, port, user or None, password or None, timeout=timeout,
                                  banner_timeout=timeout, auth_timeout=timeout)
                self._sftp = self._ssh.open_sftp()
        except OSError:
            self._ssh.close()
            raise

    def close(self) -> None:
        self._ssh.close()

    def pwd(self) -> str:
        return self._sftp.normalize(".")

    def listdir(self, path: str) -> list[RemoteEntry]:
        """Folders first, then by name."""
        with _oserrors():
            attrs = self._sftp.listdir_attr(path)
        out = [RemoteEntry(a.filename, stat.S_ISDIR(a.st_mode), a.st_size or 0)
               for a in attrs
               if a.st_mode is not None and _safe_name(a.filename)
               and (stat.S_ISDIR(a.st_mode) or stat.S_ISREG(a.st_mode))]
        return sorted(out, key=lambda e: (not e.is_dir, e.name.lower()))

    def _is_dir(self, path: str) -> bool:
        try:
            return stat.S_ISDIR(self._sftp.stat(path).st_mode)
        except FileNotFoundError:
            return False

    def download(self, paths: Iterable[str], dst_dir: Path, overwrite: bool = False,
                 progress=None) -> None:
        """Download files and folders (recursively) into dst_dir."""
        dst_dir = Path(dst_dir)
        if not dst_dir.is_dir():
            raise NotADirectoryError(dst_dir)
        for p in paths:
            top = dst_dir / posixpath.basename(p.rstrip("/"))
            if (top.exists() or top.is_symlink()) and not overwrite:
                raise FileExistsError(top)
            with _oserrors():
                self._download(p, top)
            if progress:
                progress(Path(posixpath.basename(p)))

    def _download(self, remote: str, local: Path) -> None:
        _unlink_if_symlink(local)
        if self._is_dir(remote):
            local.mkdir(exist_ok=True)
            for e in self.listdir(remote):
                self._download(posixpath.join(remote, e.name), local / e.name)
        else:
            self._sftp.get(remote, str(local))

    def upload(self, srcs: Iterable[Path], remote_dir: str, overwrite: bool = False,
               progress=None) -> None:
        """Upload files and folders (recursively) into remote_dir."""
        for src in map(Path, srcs):
            existing = {e.name for e in self.listdir(remote_dir)}
            if src.name in existing and not overwrite:
                raise FileExistsError(posixpath.join(remote_dir, src.name))
            with _oserrors():
                self._upload(src, posixpath.join(remote_dir, src.name))
            if progress:
                progress(src)

    def _upload(self, local: Path, remote: str) -> None:
        if local.is_dir() and not local.is_symlink():
            if not self._is_dir(remote):
                self._sftp.mkdir(remote)
            for child in sorted(local.iterdir()):
                self._upload(child, posixpath.join(remote, child.name))
        else:
            self._sftp.put(str(local), remote)
