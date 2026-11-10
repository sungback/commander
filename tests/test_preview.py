import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from test_smoke import _names, _select_file, _setup, _wait

from commander import preview
from commander.preview_widget import PreviewWidget
from commander.viewer import ViewerDialog


def test_detect_encoding_and_hex_lines():
    assert preview.detect_encoding(b"plain ascii") == "utf-8-sig"
    assert preview.detect_encoding("한글".encode()[:-1]) == "utf-8-sig"  # cut mid-character
    assert preview.detect_encoding("한글 텍스트".encode("cp949")) == "cp949"
    assert preview.detect_encoding("a".encode("utf-16")) == "utf-16"
    assert preview.detect_encoding(b"\x7fELF\0\0") is None
    assert preview.detect_encoding(b"") == "utf-8-sig"
    dump = preview.hex_lines(bytes(range(18)), offset=0x10)
    assert dump.split("\n") == [
        "00000010  00 01 02 03 04 05 06 07  08 09 0a 0b 0c 0d 0e 0f  |................|",
        "00000020  10 11                                             |..|",
    ]
    assert preview.hex_lines(b"Hi!") == "00000000  48 69 21                                          |Hi!|"


def test_text_reader_never_splits_characters(tmp_path):
    f = tmp_path / "k.txt"
    text = "가나다라마바사" * 50
    f.write_text(text, encoding="utf-8")
    reader = preview.TextReader(f, "utf-8-sig")
    parts = []
    while not reader.eof:
        parts.append(reader.read(7))  # 7 bytes never lands on a 3-byte boundary twice running
    assert "".join(parts) == text and "�" not in "".join(parts)


def test_large_text_is_paged(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(preview, "TEXT_CHUNK", 1000)
    monkeypatch.setattr(preview.TextReader.read, "__defaults__", (1000,))
    f = tmp_path / "big.log"
    f.write_text("".join(f"line {i}\n" for i in range(2000)))
    w = PreviewWidget()
    w.show_path(f)
    assert w.mode == "text" and "텍스트" in w.header.text() and "big.log" in w.header.text()
    first = w.text.document().blockCount()
    assert first < 500  # only the first piece is loaded
    assert w.load_more() and w.text.document().blockCount() > first
    while w.load_more():
        pass
    assert w.text.toPlainText() == f.read_text()
    assert not w.load_more()


def test_binary_hex_and_toggle(tmp_path):
    QApplication.instance() or QApplication([])
    f = tmp_path / "data.bin"
    f.write_bytes(bytes(range(256)) * 2)
    t = tmp_path / "t.txt"
    t.write_text("hello")
    w = PreviewWidget()
    w.show_path(f)
    assert w.mode == "hex"
    lines = w.text.toPlainText().split("\n")
    assert len(lines) == 32 and lines[0].startswith("00000000  00 01 02") and lines[-1].startswith("000001f0")
    w.show_path(t)
    assert w.mode == "text" and w.text.toPlainText() == "hello"
    w.toggle_hex()
    assert w.mode == "hex" and w.text.toPlainText().startswith("00000000  68 65 6c 6c 6f")
    w.toggle_hex()
    assert w.mode == "text"
    w.show_path(tmp_path)
    assert w.mode == "info" and "항목 2개" in w.info.text()
    w.show_path(tmp_path / "missing")
    assert w.mode == "info" and "FileNotFoundError" in w.info.text()
    w.toggle_hex()  # nothing to toggle
    assert w.mode == "info"


def test_image_preview_and_fake_image_falls_back(tmp_path):
    QApplication.instance() or QApplication([])
    img = QImage(40, 20, QImage.Format_RGB32)
    img.fill(QColor("red"))
    png = tmp_path / "pic.png"
    assert img.save(str(png))
    fake = tmp_path / "fake.png"
    fake.write_text("not really an image")
    w = PreviewWidget()
    w.resize(400, 300)
    w.show_path(png)
    assert w.mode == "image" and "40×20" in w.header.text() and not w.image.pixmap().isNull()
    w.toggle_hex()
    assert w.mode == "hex" and w.text.toPlainText().startswith("00000000  89 50 4e 47")
    w.show_path(fake)
    assert w.mode == "text" and w.text.toPlainText() == "not really an image"


def test_viewer_dialog_uses_preview_and_f3_toggles_hex(tmp_path):
    QApplication.instance() or QApplication([])
    f = tmp_path / "a.txt"
    f.write_text("abc")
    dlg = ViewerDialog(f, edit=False)
    assert dlg.editor.isReadOnly() and dlg.editor.toPlainText() == "abc"
    dlg.preview.toggle_hex()
    assert dlg.preview.mode == "hex"
    dlg.done(0)
    assert dlg.preview.path is None  # file handle released


def test_quick_view_replaces_inactive_panel(tmp_path, monkeypatch):
    import zipfile

    _app, win, a, _b = _setup(tmp_path, monkeypatch)
    win.active_index = lambda: 0
    (a / "other.bin").write_bytes(b"\0\1\2")
    with zipfile.ZipFile(a / "z.zip", "w") as z:
        z.writestr("in.txt", "hi")
    left, right = win.panels
    _wait(lambda: {"other.bin", "z.zip"} <= _names(left))
    sizes = win.splitter.sizes()
    _select_file(win)

    assert win.actions_by_name["quick_view"].shortcut().toString() in ("Ctrl+Q", "Meta+Q")
    assert win.toggle_quick_view() is True
    assert win.splitter.count() == 2 and win.splitter.widget(1) is win.quick
    assert right.isHidden() and not win.quick.isHidden()
    assert win.splitter.sizes() == sizes
    assert win.quick.mode == "text" and win.quick.text.toPlainText() == "x"

    left.select_names({"other.bin"})
    row = next(i for i in left._tab._name_rows() if i.data() == "other.bin")
    left.view.setCurrentIndex(row)  # moving the cursor refreshes the preview (debounced)
    _wait(lambda: win.quick.mode == "hex")
    win.switch_panel()  # no hidden panel to switch to
    assert win._active == 0

    left.set_path(str(a / "z.zip"))
    win._update_quick()
    assert win.quick.mode == "info" and "미리 볼 수 없습니다" in win.quick.info.text()
    win.save_state()  # still two panel sizes while Quick View is on

    assert win.toggle_quick_view() is False
    assert win.splitter.widget(1) is right and not right.isHidden() and win.quick.isHidden()
    assert win.splitter.sizes() == sizes and win.quick.path is None
