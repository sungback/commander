"""Split and reassemble files using a checksummed Commander JSON manifest.

The manifest is deliberately explicit: no globbing or guessed part order. Outputs are
created exclusively, so an unrelated existing file is never replaced.
"""
import hashlib
import json
import os
import re
from contextlib import suppress
from pathlib import Path

from . import ops
from .transfer import CHUNK, Control, Stats, _temporary_sibling

FORMAT = "commander-parts-v1"
MANIFEST_SUFFIX = ".commander-parts.json"
MAX_PARTS = 100_000


def _publish(temp: Path, target: Path) -> None:
    # Hard link is an atomic, exclusive creation, unlike exists() + os.replace().
    os.link(temp, target)


def _remove_owned(outputs: list[tuple[Path, tuple[int, int]]]) -> None:
    for path, identity in reversed(outputs):
        with suppress(OSError):
            st = path.lstat()
            if (st.st_dev, st.st_ino) == identity:
                path.unlink()


def _record(path: Path) -> tuple[Path, tuple[int, int]]:
    st = path.lstat()
    return path, (st.st_dev, st.st_ino)


def split_file(src: Path, dst_dir: Path, part_size: int, *, stats: Stats | None = None,
               control: Control | None = None) -> Path:
    """Write numbered parts and return the manifest path; fail on any collision.

    Each part and the manifest are exclusively published. A failed/cancelled run
    cleans up only output files still matching the inode it created.
    """
    src, dst_dir = Path(src), Path(dst_dir)
    if part_size <= 0:
        raise ValueError("part size must be positive")
    if src.is_symlink() or not src.is_file():
        raise ValueError("source must be a regular file")
    size = src.stat().st_size
    count = max(1, (size + part_size - 1) // part_size)
    if count > MAX_PARTS:
        raise ValueError("too many parts")
    stats, control = stats or Stats(), control or Control()
    stats.total_bytes, stats.total_items = size, count
    manifest = dst_dir / (src.name + MANIFEST_SUFFIX)
    outputs: list[tuple[Path, tuple[int, int]]] = []
    parts = []
    try:
        with src.open("rb") as fin:
            for number in range(1, count + 1):
                control.checkpoint()
                name = f"{src.name}.{number:06d}.part"
                target = dst_dir / name
                stats.current = name
                digest = hashlib.sha256()
                remaining = min(part_size, size - stats.done_bytes)
                part_bytes = remaining
                with _temporary_sibling(target) as (temp, fout):
                    while remaining:
                        control.checkpoint()
                        block = fin.read(min(CHUNK, remaining))
                        if not block:
                            raise OSError("source changed during split")
                        fout.write(block)
                        digest.update(block)
                        remaining -= len(block)
                        stats.done_bytes += len(block)
                    fout.close()
                    control.checkpoint()
                    _publish(temp, target)
                    outputs.append(_record(target))
                stats.done_items += 1
                parts.append({"name": name, "size": part_bytes, "sha256": digest.hexdigest()})
            if fin.read(1):
                raise OSError("source changed during split")
        payload = {"format": FORMAT, "name": src.name, "size": size, "parts": parts}
        with _temporary_sibling(manifest) as (temp, fout):
            fout.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            fout.close()
            control.checkpoint()
            _publish(temp, manifest)
            outputs.append(_record(manifest))
        return manifest
    except BaseException:
        _remove_owned(outputs)
        raise


def _safe_name(name: object) -> bool:
    return isinstance(name, str) and ops.is_valid_name(name)


def read_manifest(manifest: Path) -> dict:
    """Read and validate the format and part order before creating output."""
    manifest = Path(manifest)
    if manifest.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("manifest too large")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid parts manifest") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT or not _safe_name(data.get("name")):
        raise ValueError("invalid parts manifest")
    size, parts = data.get("size"), data.get("parts")
    if type(size) is not int or size < 0 or not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError("invalid parts manifest")
    total = 0
    for number, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            raise ValueError("invalid part")  # noqa: TRY004 - malformed file, not an API type error
        expected = f"{data['name']}.{number:06d}.part"
        part_size, digest = part.get("size"), part.get("sha256")
        if (part.get("name") != expected or type(part_size) is not int or part_size < 0
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid part order or metadata")
        total += part_size
    if total != size:
        raise ValueError("invalid total size")
    return data


def join_file(manifest: Path, dst_dir: Path, *, stats: Stats | None = None,
              control: Control | None = None) -> Path:
    """Verify all numbered parts and exclusively publish the reconstructed file."""
    manifest, dst_dir = Path(manifest), Path(dst_dir)
    data = read_manifest(manifest)
    target = dst_dir / data["name"]
    stats, control = stats or Stats(), control or Control()
    stats.total_bytes, stats.total_items = data["size"], len(data["parts"])
    with _temporary_sibling(target) as (temp, fout):
        for item in data["parts"]:
            control.checkpoint()
            path = manifest.parent / item["name"]
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"missing or invalid part: {path.name}")
            stats.current = path.name
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as fin:
                while block := fin.read(CHUNK):
                    control.checkpoint()
                    fout.write(block)
                    digest.update(block)
                    size += len(block)
                    stats.done_bytes += len(block)
            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError(f"part failed verification: {path.name}")
            stats.done_items += 1
        fout.close()
        control.checkpoint()
        _publish(temp, target)
    return target
