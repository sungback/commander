"""Streaming MIME Base64 file conversion; output is never overwritten."""
import base64
import binascii
import os
from pathlib import Path

from .transfer import Control, Stats, _temporary_sibling


def convert(source: Path, destination: Path, *, decode: bool = False,
            stats: Stats | None = None, control: Control | None = None) -> None:
    """Convert MIME Base64 data, failing safely on invalid input or destination collision.

    The decoder accepts ASCII whitespace between Base64 characters and requires valid
    padding. No filename embedded in input is interpreted as an output path.
    """
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError("입력은 일반 파일이어야 합니다")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    stats = stats or Stats()
    control = control or Control()
    stats.total_bytes = source.stat().st_size
    stats.total_items = 1
    stats.current = source.name
    with _temporary_sibling(destination) as (temporary, output), source.open("rb") as inp:
        if decode:
            _decode(inp, output, stats, control)
        else:
            while chunk := inp.read(57):
                control.checkpoint()
                output.write(base64.b64encode(chunk) + b"\n")
                stats.done_bytes += len(chunk)
        control.checkpoint()
        output.flush()
        output.close()
        # Linking an owned temporary file gives exclusive creation even if another
        # process creates the destination after the preliminary check.
        os.link(temporary, destination)
    stats.done_items = 1


def _decode(inp, output, stats: Stats, control: Control) -> None:
    pending = b""
    ended = False
    while raw := inp.read(65536):
        control.checkpoint()
        stats.done_bytes += len(raw)
        data = b"".join(raw.split())
        if ended and data:
            raise ValueError("Base64 패딩 뒤에 데이터가 있습니다")
        pending += data
        complete = len(pending) // 4 * 4
        if complete:
            group = pending[:complete]
            try:
                output.write(base64.b64decode(group, validate=True))
            except binascii.Error as exc:
                raise ValueError("잘못된 Base64 데이터입니다") from exc
            if b"=" in group:
                ended = True
                if complete < len(pending):
                    raise ValueError("Base64 패딩 뒤에 데이터가 있습니다")
        pending = pending[complete:]
    if pending:
        raise ValueError("불완전한 Base64 데이터입니다")
