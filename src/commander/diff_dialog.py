"""Side-by-side text comparison of two files."""
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from . import compare

_CHANGED = QColor(255, 214, 102, 90)
_MISSING = QColor(128, 128, 128, 60)


class DiffDialog(QDialog):
    def __init__(self, left: Path, right: Path, left_lines: list[str], right_lines: list[str],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"비교 - {left.name} ↔ {right.name}")
        self.resize(1100, 650)
        self.rows = compare.side_by_side(left_lines, right_lines)
        self.blocks = compare.change_blocks(self.rows)
        self._at = -1
        self.panes = [self._pane(side) for side in (0, 1)]
        a, b = self.panes
        for bar_a, bar_b in ((a.verticalScrollBar(), b.verticalScrollBar()),
                             (a.horizontalScrollBar(), b.horizontalScrollBar())):
            bar_a.valueChanged.connect(bar_b.setValue)
            bar_b.valueChanged.connect(bar_a.setValue)
        self.summary = QLabel(f"차이 {len(self.blocks)}곳")
        self.prev_btn = QPushButton("이전 차이 (Shift+F3)")
        self.next_btn = QPushButton("다음 차이 (F3)")
        self.prev_btn.clicked.connect(lambda: self.go(-1))
        self.next_btn.clicked.connect(lambda: self.go(1))
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.go(1))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self.go(-1))
        top = QHBoxLayout()
        top.addWidget(QLabel(str(left)), 1)
        top.addWidget(QLabel(str(right)), 1)
        body = QHBoxLayout()
        body.setSpacing(2)
        body.addWidget(a)
        body.addWidget(b)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.prev_btn)
        bottom.addWidget(self.next_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(body, 1)
        layout.addLayout(bottom)
        if self.blocks:
            self.go(1)

    def _pane(self, side: int) -> QPlainTextEdit:
        pane = QPlainTextEdit()
        pane.setReadOnly(True)
        pane.setLineWrapMode(QPlainTextEdit.NoWrap)
        pane.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        width = len(str(len(self.rows)))
        lines = []
        for row in self.rows:
            text, no = (row.left, row.left_no) if side == 0 else (row.right, row.right_no)
            lines.append(f"{no:>{width}}  {text}" if text is not None else "")
        pane.setPlainText("\n".join(lines))
        marks = []
        doc = pane.document()
        for i, row in enumerate(self.rows):
            if not row.changed:
                continue
            sel = QTextEdit.ExtraSelection()
            missing = (row.left if side == 0 else row.right) is None
            sel.format.setBackground(_MISSING if missing else _CHANGED)
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = QTextCursor(doc.findBlockByNumber(i))
            marks.append(sel)
        pane.setExtraSelections(marks)
        return pane

    def go(self, step: int) -> None:
        """Jump to the next / previous block of differences (wraps around)."""
        if not self.blocks:
            return
        self._at = (self._at + step) % len(self.blocks)
        line = self.blocks[self._at]
        for pane in self.panes:
            cursor = QTextCursor(pane.document().findBlockByNumber(line))
            pane.setTextCursor(cursor)
            pane.centerCursor()
        self.summary.setText(f"차이 {self._at + 1} / {len(self.blocks)}")
