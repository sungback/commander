import os
import socket
import threading

import paramiko
import pytest
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface
from paramiko.sftp import SFTP_OK

from commander.remote import SftpClient

HOST_KEY = paramiko.RSAKey.generate(2048)


class _Auth(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        ok = (username, password) == ("u", "p")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED


def _make_fs(root):
    class Handle(SFTPHandle):
        def stat(self):
            return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    class FS(SFTPServerInterface):
        def _p(self, path):
            return os.path.join(str(root), os.path.normpath("/" + path).lstrip("/\\"))

        def list_folder(self, path):
            out = []
            for n in os.listdir(self._p(path)):
                a = SFTPAttributes.from_stat(os.lstat(os.path.join(self._p(path), n)))
                a.filename = n
                out.append(a)
            return out

        def stat(self, path):
            try:
                return SFTPAttributes.from_stat(os.stat(self._p(path)))
            except OSError as e:
                return SFTPServer.convert_errno(e.errno)

        lstat = stat

        def open(self, path, flags, attr):
            mode = "rb" if flags & os.O_WRONLY == 0 and flags & os.O_RDWR == 0 else "wb"
            try:
                f = open(self._p(path), mode)  # noqa: SIM115 (closed by the handle)
            except OSError as e:
                return SFTPServer.convert_errno(e.errno)
            h = Handle(flags)
            h.filename = self._p(path)
            if mode == "rb":
                h.readfile = f
            else:
                h.writefile = f
            return h

        def mkdir(self, path, attr):
            try:
                os.mkdir(self._p(path))
            except OSError as e:
                return SFTPServer.convert_errno(e.errno)
            return SFTP_OK

    return FS


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    stop = threading.Event()

    def serve():
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            t = paramiko.Transport(conn)
            t.add_server_key(HOST_KEY)
            t.set_subsystem_handler("sftp", SFTPServer, _make_fs(root))
            t.start_server(server=_Auth())

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    yield root, sock.getsockname()[1]
    stop.set()
    th.join(timeout=2)
    sock.close()


@pytest.fixture
def client(server):
    root, port = server
    c = SftpClient("127.0.0.1", port, "u", "p", timeout=5,
                   host_key_policy=paramiko.AutoAddPolicy())
    yield root, c
    c.close()


def test_listdir_sorted_dirs_first(client):
    root, c = client
    (root / "b.txt").write_text("hello")
    (root / "a.txt").write_text("x")
    (root / "zdir").mkdir()
    (root / "link").symlink_to(root / "a.txt")
    entries = c.listdir("/")
    assert [(e.name, e.is_dir) for e in entries] == [("zdir", True), ("a.txt", False), ("b.txt", False)]
    assert next(e for e in entries if e.name == "b.txt").size == 5


def test_download_file_and_folder(client, tmp_path):
    root, c = client
    (root / "f.txt").write_text("data")
    (root / "d" / "sub").mkdir(parents=True)
    (root / "d" / "sub" / "g.txt").write_text("deep")
    dst = tmp_path / "dst"
    dst.mkdir()
    seen = []
    c.download(["/f.txt", "/d"], dst, progress=seen.append)
    assert (dst / "f.txt").read_text() == "data"
    assert (dst / "d" / "sub" / "g.txt").read_text() == "deep"
    assert len(seen) == 2
    (dst / "f.txt").write_text("old")
    with pytest.raises(FileExistsError):
        c.download(["/f.txt"], dst)
    assert (dst / "f.txt").read_text() == "old"


def test_upload_file_and_folder(client, tmp_path):
    root, c = client
    src = tmp_path / "src"
    (src / "inner").mkdir(parents=True)
    (src / "inner" / "a.txt").write_text("A")
    top = tmp_path / "top.txt"
    top.write_text("T")
    c.upload([top, src], "/")
    assert (root / "top.txt").read_text() == "T"
    assert (root / "src" / "inner" / "a.txt").read_text() == "A"
    with pytest.raises(FileExistsError):
        c.upload([top], "/")
    top.write_text("T2")
    c.upload([top], "/", overwrite=True)
    assert (root / "top.txt").read_text() == "T2"


def test_bad_login_raises(server):
    _, port = server
    with pytest.raises(OSError):
        SftpClient("127.0.0.1", port, "u", "wrong", timeout=5,
                   host_key_policy=paramiko.AutoAddPolicy())


def test_unknown_host_key_rejected_by_default(server):
    _, port = server
    with pytest.raises(OSError):
        SftpClient("127.0.0.1", port, "u", "p", timeout=5)


def test_listdir_missing_folder_raises(client):
    _, c = client
    with pytest.raises(OSError):
        c.listdir("/nope")


def test_download_does_not_write_through_symlink(client, tmp_path):
    root, c = client
    (root / "f.txt").write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (dst / "f.txt").symlink_to(victim)
    with pytest.raises(FileExistsError):
        c.download(["/f.txt"], dst)
    c.download(["/f.txt"], dst, overwrite=True)
    assert victim.read_text() == "keep"
    assert not (dst / "f.txt").is_symlink() and (dst / "f.txt").read_text() == "new"
