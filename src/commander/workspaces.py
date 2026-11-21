"""Named panel layouts stored independently of the current session."""

import json
import os
import tempfile
from pathlib import Path


class InvalidWorkspaceFile(ValueError):
    """The workspace file is malformed and must not be overwritten."""


def valid_state(state: object) -> bool:
    if not isinstance(state, dict) or type(state.get("active_panel")) is not int or state["active_panel"] not in (0, 1):
        return False
    panels = state.get("panels")
    if not isinstance(panels, list) or len(panels) != 2:
        return False
    for panel in panels:
        if not isinstance(panel, dict):
            return False
        tabs = panel.get("tabs")
        active = panel.get("active")
        if not isinstance(tabs, list) or not tabs or type(active) is not int:
            return False
        if not 0 <= active < len(tabs):
            return False
        for tab in tabs:
            if not isinstance(tab, dict) or not isinstance(tab.get("path"), str):
                return False
            if any(not isinstance(tab.get(key, ""), str) for key in ("archive", "inner", "current")):
                return False
            col, order = tab.get("sort_col", 0), tab.get("sort_order", 0)
            if type(col) is not int or not 0 <= col <= 3 or type(order) is not int or order not in (0, 1):
                return False
            if not isinstance(tab.get("show_hidden", False), bool) or not isinstance(tab.get("locked", False), bool):
                return False
            selected = tab.get("selected", [])
            if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
                return False
    return True


def load(path: Path | None) -> dict[str, dict]:
    """Load layouts, rejecting corrupt files rather than overwriting them."""
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise InvalidWorkspaceFile("작업 공간 파일을 읽을 수 없습니다") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise InvalidWorkspaceFile("지원하지 않는 작업 공간 파일입니다")
    layouts = data.get("workspaces")
    if not isinstance(layouts, dict) or any(
        not isinstance(name, str) or not name.strip() or not valid_state(state)
        for name, state in layouts.items()
    ):
        raise InvalidWorkspaceFile("작업 공간 데이터가 올바르지 않습니다")
    return layouts


def save(path: Path, layouts: dict[str, dict]) -> None:
    """Atomically replace a complete, validated collection of layouts."""
    if any(not isinstance(name, str) or not name.strip() or not valid_state(state)
           for name, state in layouts.items()):
        raise ValueError("작업 공간 데이터가 올바르지 않습니다")
    encoded = json.dumps({"version": 1, "workspaces": layouts}, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".workspaces-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
