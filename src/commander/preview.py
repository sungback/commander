"""File content helpers for the viewer and Quick View (UI-independent)."""
import codecs
import io
from pathlib import Path

TEXT_CHUNK = 256 * 1024
HEX_CHUNK = 64 * 1024
_SAMPLE = 64 * 1024


def detect_encoding(sample: bytes) -> str | None:
    """Encoding to read a text file with, or None when the content looks binary."""
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\0" in sample:
        return None
    for enc in ("utf-8-sig", "cp949"):
        try:
            # final=False: the sample may end in the middle of a multi-byte character
            codecs.getincrementaldecoder(enc)().decode(sample, final=False)
        except UnicodeDecodeError:
            continue
        return enc
    return None


def sniff(path: Path) -> str | None:
    with open(path, "rb") as f:
        return detect_encoding(f.read(_SAMPLE))


def hex_lines(data: bytes, offset: int = 0) -> str:
    """Classic dump: offset, 16 hex bytes, printable ASCII."""
    out = []
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        hexes = " ".join(f"{b:02x}" for b in row)
        if len(row) > 8:
            hexes = hexes[:23] + " " + hexes[23:]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        out.append(f"{offset + i:08x}  {hexes:<48}  |{text}|")
    return "\n".join(out)


class TextReader:
    """Reads a text file piece by piece without splitting multi-byte characters."""

    def __init__(self, path: Path, encoding: str):
        self._f = open(path, "rb")  # noqa: SIM115 - closed by close()
        # Newlines are translated like text mode does, so CRLF files never show stray '\r'
        # (also when a CRLF pair is split across two pieces).
        self._decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder(encoding)(errors="replace"), translate=True)
        self.eof = False

    def read(self, size: int = TEXT_CHUNK) -> str:
        if self.eof:
            return ""
        data = self._f.read(size)
        if not data:
            self.eof = True
            self._f.close()
            return self._decoder.decode(b"", final=True)
        return self._decoder.decode(data)

    def close(self) -> None:
        self.eof = True
        self._f.close()


class HexReader:
    def __init__(self, path: Path):
        self._f = open(path, "rb")  # noqa: SIM115 - closed by close()
        self._offset = 0
        self.eof = False

    def read(self, size: int = HEX_CHUNK) -> str:
        if self.eof:
            return ""
        data = self._f.read(size)
        if not data:
            self.close()
            return ""
        text = hex_lines(data, self._offset)
        self._offset += len(data)
        return text

    def close(self) -> None:
        self.eof = True
        self._f.close()
