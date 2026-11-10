"""Preferences: appearance, columns, keyboard shortcuts and user-defined tools."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .commands import Command, effective_shortcuts, find_conflicts
from .settings import COLUMNS, Settings

_THEMES = (("시스템 설정 따름", "system"), ("밝게", "light"), ("어둡게", "dark"))
_COLUMN_LABELS = {"size": "크기", "type": "종류", "date": "수정한 날짜"}


def normalize_keys(text: str) -> list[str]:
    """Space-separated key names -> canonical Qt names. Raises ValueError for an unknown key.

    A comma after a key is tolerated ("F8, Del"), while "Ctrl+," itself stays a valid key.
    """
    keys = []
    for part in text.split():
        if len(part) > 1 and part.endswith(",") and not part.endswith("+,"):
            part = part[:-1]
        name = QKeySequence(part).toString(QKeySequence.PortableText)
        if not name:
            raise ValueError(f"알 수 없는 키: {part}")
        keys.append(name)
    return keys


class SettingsDialog(QDialog):
    """After `exec()` returns Accepted, `result_settings` holds the edited copy."""

    def __init__(self, settings: Settings, commands: list[Command], parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.resize(720, 560)
        self._commands = commands
        self._settings = settings
        self.result_settings = settings

        # General
        self.theme = QComboBox()
        for label, value in _THEMES:
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        self.command_line = QCheckBox("하단 명령줄 표시")
        self.command_line.setChecked(settings.show_command_line)
        self.fn_bar = QCheckBox("하단 펑션키 버튼 표시")
        self.fn_bar.setChecked(settings.show_fn_bar)
        self.columns = {}
        cols = QHBoxLayout()
        for key in COLUMNS:
            box = QCheckBox(_COLUMN_LABELS[key])
            box.setChecked(settings.columns.get(key, True))
            self.columns[key] = box
            cols.addWidget(box)
        cols.addStretch(1)
        general = QWidget()
        form = QFormLayout(general)
        form.addRow("테마", self.theme)
        form.addRow("표시할 컬럼", cols)
        form.addRow("", self.command_line)
        form.addRow("", self.fn_bar)

        # Shortcuts
        current = effective_shortcuts(commands, settings.shortcuts)
        self.keys = QTableWidget(len(commands), 2)
        self.keys.setHorizontalHeaderLabels(["명령", "단축키 (공백으로 여러 개)"])
        self.keys.verticalHeader().hide()
        self.keys.setColumnWidth(0, 330)
        self.keys.horizontalHeader().setStretchLastSection(True)
        for row, cmd in enumerate(commands):
            name = QTableWidgetItem(f"{cmd.menu} › {cmd.label}")
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self.keys.setItem(row, 0, name)
            self.keys.setItem(row, 1, QTableWidgetItem(" ".join(current[cmd.id])))
        self.capture = QKeySequenceEdit()
        assign = QPushButton("선택한 명령에 지정")
        assign.clicked.connect(self.assign_captured)
        defaults = QPushButton("모두 기본값으로")
        defaults.clicked.connect(self.reset_shortcuts)
        capture_row = QHBoxLayout()
        capture_row.addWidget(QLabel("키 입력:"))
        capture_row.addWidget(self.capture, 1)
        capture_row.addWidget(assign)
        capture_row.addWidget(defaults)
        keys_tab = QWidget()
        keys_layout = QVBoxLayout(keys_tab)
        keys_layout.addWidget(self.keys, 1)
        keys_layout.addLayout(capture_row)

        # Tools
        self.tools = QTableWidget(0, 3)
        self.tools.setHorizontalHeaderLabels(["이름", "명령", "단축키"])
        self.tools.verticalHeader().hide()
        self.tools.setColumnWidth(0, 150)
        self.tools.setColumnWidth(1, 380)
        self.tools.horizontalHeader().setStretchLastSection(True)
        for tool in settings.tools:
            self.add_tool(tool["name"], tool["command"], tool.get("shortcut", ""))
        add = QPushButton("추가")
        add.clicked.connect(lambda: self.add_tool("새 도구", "", ""))
        remove = QPushButton("삭제")
        remove.clicked.connect(self.remove_tool)
        tool_buttons = QHBoxLayout()
        tool_buttons.addWidget(add)
        tool_buttons.addWidget(remove)
        tool_buttons.addStretch(1)
        tools_tab = QWidget()
        tools_layout = QVBoxLayout(tools_tab)
        tools_layout.addWidget(QLabel(
            "선택한 파일로 외부 프로그램을 실행합니다. 예: code {dir}   또는   open -a Preview {files}\n"
            "{file} 첫 번째 선택 파일   {files} 선택한 모든 파일   {name} 파일 이름   {dir} 현재 폴더"))
        tools_layout.addWidget(self.tools, 1)
        tools_layout.addLayout(tool_buttons)

        self.tabs = QTabWidget()
        self.tabs.addTab(general, "일반")
        self.tabs.addTab(keys_tab, "단축키")
        self.tabs.addTab(tools_tab, "사용자 도구")
        self.error = QLabel()
        self.error.setStyleSheet("color: #d33;")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def add_tool(self, name: str, command: str, shortcut: str) -> None:
        row = self.tools.rowCount()
        self.tools.insertRow(row)
        for col, text in enumerate((name, command, shortcut)):
            self.tools.setItem(row, col, QTableWidgetItem(text))

    def remove_tool(self) -> None:
        row = self.tools.currentRow()
        if row >= 0:
            self.tools.removeRow(row)

    def assign_captured(self) -> None:
        row = self.keys.currentRow()
        seq = self.capture.keySequence().toString(QKeySequence.PortableText)
        if row >= 0 and seq:
            self.keys.item(row, 1).setText(seq)
            self.capture.clear()

    def reset_shortcuts(self) -> None:
        for row, cmd in enumerate(self._commands):
            self.keys.item(row, 1).setText(" ".join(cmd.shortcuts))

    def collect(self) -> Settings:
        """Build the edited settings. Raises ValueError (with a user-facing message) when invalid."""
        mapping: dict[str, list[str]] = {}
        labels = {}
        for row, cmd in enumerate(self._commands):
            mapping[cmd.id] = normalize_keys(self.keys.item(row, 1).text())
            labels[cmd.id] = cmd.label
        tools = []
        for row in range(self.tools.rowCount()):
            name, command, shortcut = (self.tools.item(row, c).text().strip() for c in range(3))
            if not name and not command:
                continue
            if not name or not command:
                raise ValueError("사용자 도구에는 이름과 명령이 모두 필요합니다.")
            keys = normalize_keys(shortcut)
            if len(keys) > 1:
                raise ValueError(f"도구 '{name}'의 단축키는 하나만 지정할 수 있습니다.")
            tool_id = f"tool:{row}"
            mapping[tool_id] = keys
            labels[tool_id] = name
            tools.append({"name": name, "command": command, "shortcut": keys[0] if keys else ""})
        conflicts = find_conflicts(mapping)
        if conflicts:
            key, first, second = conflicts[0]
            raise ValueError(f"단축키 {key}가 '{labels[first]}'와(과) '{labels[second]}'에 중복 지정되었습니다.")
        overrides = {cmd.id: mapping[cmd.id] for cmd in self._commands
                     if mapping[cmd.id] != normalize_keys(" ".join(cmd.shortcuts))}
        return replace(
            self._settings, theme=self.theme.currentData(),
            show_command_line=self.command_line.isChecked(), show_fn_bar=self.fn_bar.isChecked(),
            columns={key: box.isChecked() for key, box in self.columns.items()},
            shortcuts=overrides, tools=tools)

    def accept(self) -> None:
        try:
            self.result_settings = self.collect()
        except ValueError as e:
            self.error.setText(str(e))
            return
        super().accept()
