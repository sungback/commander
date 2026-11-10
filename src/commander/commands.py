"""The command table: ids, menu labels and default shortcuts (UI-independent)."""
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    menu: str  # menu title it is listed under
    shortcuts: tuple[str, ...] = ()


def default_commands(platform: str = sys.platform) -> list[Command]:
    """Every rebindable command. On macOS Qt's 'Ctrl' is the Command key and 'Meta' is Control,
    so combinations the system owns there (Cmd+Q quit, Cmd+M minimize, Cmd+H hide) are avoided."""
    mac = platform == "darwin"
    c = Command
    return [
        c("view", "보기", "파일", ("F3",)),
        c("edit", "편집", "파일", ("F4",)),
        c("copy", "복사 / 추출 / 전송", "파일", ("F5",)),
        c("move", "이동", "파일", ("F6",)),
        c("mkdir", "새 폴더", "파일", ("F7",)),
        c("rename", "이름 변경...", "파일", ("F2",) if mac else ("F2", "Ctrl+M")),
        c("delete", "휴지통으로 이동", "파일", ("F8", "Del", "Ctrl+Backspace")),
        c("delete_permanent", "영구 삭제", "파일", ("Shift+F8", "Shift+Del")),
        c("pack", "압축 파일 만들기...", "파일", ("Alt+F5",)),
        c("copy_path", "경로 복사", "파일", ("Ctrl+Shift+C",)),
        c("switch_panel", "반대편 패널로", "탐색", ("Tab",)),
        c("back", "뒤로", "탐색", ("Alt+Left", "Ctrl+[")),
        c("forward", "앞으로", "탐색", ("Alt+Right", "Ctrl+]")),
        c("favorites", "즐겨찾기...", "탐색", ("Ctrl+D",)),
        c("filter", "빠른 필터", "탐색", ("Ctrl+S",)),
        c("search", "파일 검색...", "탐색", ("Ctrl+F",)),
        c("toggle_hidden", "숨김 파일 표시", "탐색", ("Ctrl+.",) if mac else ("Ctrl+H", "Ctrl+.")),
        c("flat", "평면 보기 (하위 폴더 파일 모두 표시)", "탐색", ("Ctrl+B",)),
        c("quick_view", "빠른 보기 (Quick View)", "탐색", ("Meta+Q",) if mac else ("Ctrl+Q",)),
        c("new_tab", "새 탭", "탐색", ("Ctrl+T",)),
        c("close_tab", "탭 닫기", "탐색", ("Ctrl+W",)),
        c("command_line", "명령줄로 이동", "탐색", ("Ctrl+L",)),
        c("connect", "원격 서버 연결...", "탐색", ("Ctrl+N",)),
        c("compare_dirs", "폴더 비교 (다른 항목 선택)", "도구", ("Shift+F2",)),
        c("sync", "폴더 동기화...", "도구", ("Ctrl+Shift+S",)),
        c("diff", "파일 내용 비교", "도구", ("Ctrl+Shift+D",)),
        c("checksum", "체크섬 계산...", "도구"),
        c("verify", "체크섬 검증", "도구"),
        c("settings", "설정...", "도구", ("Ctrl+,",)),
    ]


def effective_shortcuts(commands: list[Command],
                        overrides: dict[str, list[str]]) -> dict[str, list[str]]:
    """Shortcut list per command id: the override when present, the default otherwise."""
    return {cmd.id: list(overrides[cmd.id]) if cmd.id in overrides else list(cmd.shortcuts)
            for cmd in commands}


def find_conflicts(mapping: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """(key, first id, second id) for every key bound to more than one command."""
    seen: dict[str, str] = {}
    out = []
    for cmd_id, keys in mapping.items():
        for key in keys:
            norm = key.strip().lower()
            if not norm:
                continue
            if norm in seen and seen[norm] != cmd_id:
                out.append((key, seen[norm], cmd_id))
            seen.setdefault(norm, cmd_id)
    return out
