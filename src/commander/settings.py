"""User preferences stored as JSON (UI-independent)."""
import json
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path

SETTINGS_FILE = Path.home() / ".config" / "commander" / "settings.json"
THEMES = ("system", "light", "dark")
COLUMNS = ("size", "type", "date")  # the name column is always shown


@dataclass
class Settings:
    theme: str = "system"
    show_command_line: bool = True
    show_fn_bar: bool = True
    columns: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(COLUMNS, True))
    shortcuts: dict[str, list[str]] = field(default_factory=dict)  # command id -> keys (overrides)
    tools: list[dict[str, str]] = field(default_factory=list)  # {"name", "command", "shortcut"}
    associations: dict[str, str] = field(default_factory=dict)  # lowercase extension -> application


def _clean(data: dict) -> Settings:
    s = Settings()
    if data.get("theme") in THEMES:
        s.theme = data["theme"]
    for key in ("show_command_line", "show_fn_bar"):
        if isinstance(data.get(key), bool):
            setattr(s, key, data[key])
    cols = data.get("columns")
    if isinstance(cols, dict):
        s.columns = {c: bool(cols.get(c, True)) for c in COLUMNS}
    keys = data.get("shortcuts")
    if isinstance(keys, dict):
        s.shortcuts = {str(k): [str(x) for x in v if isinstance(x, str)]
                       for k, v in keys.items() if isinstance(v, list)}
    tools = data.get("tools")
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict) and str(t.get("name", "")).strip() and str(t.get("command", "")).strip():
                s.tools.append({"name": str(t["name"]).strip(), "command": str(t["command"]).strip(),
                                "shortcut": str(t.get("shortcut", "")).strip()})
    associations = data.get("associations")
    if isinstance(associations, dict):
        s.associations = {k.lower(): v for k, v in associations.items()
                          if isinstance(k, str) and k.startswith(".") and len(k) > 1
                          and not any(c in k for c in "/\\\x00")
                          and isinstance(v, str) and v.strip() and "\x00" not in v}
    return s


def load(path: Path | None) -> Settings:
    """Settings from `path`; defaults when the file is missing, unreadable or malformed."""
    if path is None or not path.is_file():
        return Settings()
    with suppress(OSError, ValueError, TypeError):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _clean(data)
    return Settings()


def save(settings: Settings, path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")
