"""Conservative editing of Total Commander-style descript.ion entries."""
import os
import re
import stat
from pathlib import Path

from .transfer import _temporary_sibling

_ENTRY = re.compile(r'^(?:"([^"\r\n]+)"|([^\s"]+))[ \t]+(.*)$')


def _read(path: Path) -> tuple[bytes, str, str, str]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("설명 파일은 일반 파일이어야 합니다")
        if info.st_size > 10 * 1024 * 1024:
            raise ValueError("설명 파일이 너무 큽니다")
        data = path.read_bytes()
    except FileNotFoundError:
        return b"", "utf-8", "\n", ""
    if data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        try:
            data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "cp949"
    try:
        content = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("설명 파일의 인코딩을 해석할 수 없습니다") from exc
    if "\x00" in content:
        raise ValueError("설명 파일에 지원하지 않는 데이터가 있습니다")
    newline = "\r\n" if "\r\n" in content else "\n"
    return data, encoding, newline, content


def _name(name: str) -> None:
    if not name or name in (".", "..") or any(c in name for c in '/\\"\r\n\0'):
        raise ValueError("지원하지 않는 파일 이름입니다")


def read_description(directory: Path, name: str) -> str:
    """Return a single existing entry, refusing ambiguous duplicate entries."""
    _name(name)
    _, _, _, text = _read(Path(directory) / "descript.ion")
    entries = []
    for line in text.splitlines():
        match = _ENTRY.match(line)
        if match and (match.group(1) or match.group(2)) == name:
            entries.append(match.group(3))
    if len(entries) > 1:
        raise ValueError("같은 이름의 설명이 여러 개 있습니다")
    return entries[0] if entries else ""


def write_description(directory: Path, name: str, description: str) -> None:
    """Update one entry, preserving all other lines and the original encoding.

    Empty description removes the entry. An unencodable description or duplicate
    entry raises without changing the existing file.
    """
    _name(name)
    if any(c in description for c in "\r\n\0"):
        raise ValueError("설명은 한 줄이어야 합니다")
    path = Path(directory) / "descript.ion"
    existed = path.exists()
    original, encoding, newline, content = _read(path)
    lines = content.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        match = _ENTRY.match(line.rstrip("\r\n"))
        if match and (match.group(1) or match.group(2)) == name:
            matches.append(index)
    if len(matches) > 1:
        raise ValueError("같은 이름의 설명이 여러 개 있습니다")
    quoted = f'"{name}"' if any(c.isspace() for c in name) else name
    replacement = f"{quoted} {description}{newline}" if description else ""
    if matches:
        lines[matches[0]] = replacement
    elif replacement:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(replacement)
    try:
        result = "".join(lines).encode(encoding)
    except UnicodeEncodeError as exc:
        raise ValueError("기존 설명 파일의 인코딩으로 저장할 수 없습니다") from exc
    with _temporary_sibling(path) as (temporary, output):
        output.write(result)
        output.flush()
        output.close()
        if path.is_symlink():
            raise ValueError("설명 파일이 심볼릭 링크입니다")
        if path.exists():
            if not existed or path.read_bytes() != original:
                raise RuntimeError("설명 파일이 다른 곳에서 변경되었습니다")
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        else:
            if existed:
                raise RuntimeError("설명 파일이 다른 곳에서 삭제되었습니다")
            os.link(temporary, path)
