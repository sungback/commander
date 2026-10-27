import threading

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from commander.remote import FtpClient


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("u", "p", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth})
    srv = FTPServer(("127.0.0.1", 0), handler)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            srv.serve_forever(timeout=0.05, blocking=False)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield root, srv.address[1]
    stop.set()
    t.join(timeout=2)
    srv.close_all()


@pytest.fixture
def client(server):
    root, port = server
    c = FtpClient("127.0.0.1", port, "u", "p", timeout=5)
    yield root, c
    c.close()


def test_listdir_sorted_dirs_first(client):
    root, c = client
    (root / "b.txt").write_text("hello")
    (root / "a.txt").write_text("x")
    (root / "zdir").mkdir()
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


def test_download_refuses_overwrite(client, tmp_path):
    root, c = client
    (root / "f.txt").write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "f.txt").write_text("old")
    with pytest.raises(FileExistsError):
        c.download(["/f.txt"], dst)
    assert (dst / "f.txt").read_text() == "old"
    c.download(["/f.txt"], dst, overwrite=True)
    assert (dst / "f.txt").read_text() == "new"


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
        FtpClient("127.0.0.1", port, "u", "wrong", timeout=5)


def test_listdir_drops_unsafe_names(client):
    _, c = client
    c._ftp.mlsd = lambda *a, **k: iter([
        ("../evil", {"type": "file", "size": "1"}),
        ("a/b", {"type": "file", "size": "1"}),
        ("a\\b", {"type": "dir"}),
        ("ok.txt", {"type": "file", "size": "2"}),
    ])
    assert [e.name for e in c.listdir("/")] == ["ok.txt"]
