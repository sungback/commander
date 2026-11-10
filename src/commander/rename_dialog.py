"""Multi-rename dialog with live preview."""
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import rename

_CASES = (("그대로", ""), ("소문자", "lower"), ("대문자", "upper"), ("단어 첫 글자 대문자", "title"))


class RenameDialog(QDialog):
    """After `exec()` returns Accepted, `pairs` holds the validated (src, dst) list."""

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.pairs: list[tuple[Path, Path]] = []
        self.setWindowTitle(f"이름 변경 - {len(self.paths)}개 항목")
        self.resize(760, 560)

        self.name_mask = QLineEdit("[N]")
        self.ext_mask = QLineEdit("[E]")
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.regex = QCheckBox("정규식")
        self.case = QComboBox()
        for label, value in _CASES:
            self.case.addItem(label, value)
        self.start, self.step, self.digits = QSpinBox(), QSpinBox(), QSpinBox()
        self.start.setRange(0, 999_999)
        self.start.setValue(1)
        self.step.setRange(1, 9999)
        self.digits.setRange(1, 9)

        masks = QHBoxLayout()
        masks.addWidget(self.name_mask, 3)
        masks.addWidget(QLabel("."))
        masks.addWidget(self.ext_mask, 1)
        finder = QHBoxLayout()
        finder.addWidget(self.find)
        finder.addWidget(QLabel("→"))
        finder.addWidget(self.replace)
        finder.addWidget(self.regex)
        counter = QHBoxLayout()
        for label, box in (("시작", self.start), ("증가", self.step), ("자릿수", self.digits)):
            counter.addWidget(QLabel(label))
            counter.addWidget(box)
        counter.addStretch(1)
        form = QFormLayout()
        form.addRow("이름 . 확장자 마스크", masks)
        form.addRow("", QLabel("[N] 이름  [E] 확장자  [C] 카운터  [P] 상위 폴더  [YMD] 날짜  [hms] 시각"))
        form.addRow("찾기 → 바꾸기", finder)
        form.addRow("대소문자", self.case)
        form.addRow("카운터 [C]", counter)

        self.preview = QTreeWidget()
        self.preview.setRootIsDecorated(False)
        self.preview.setHeaderLabels(["기존 이름", "새 이름"])
        self.preview.setColumnWidth(0, 350)
        self.status = QLabel()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        for edit in (self.name_mask, self.ext_mask, self.find, self.replace):
            edit.textChanged.connect(self.refresh)
        for box in (self.start, self.step, self.digits):
            box.valueChanged.connect(self.refresh)
        self.regex.toggled.connect(self.refresh)
        self.case.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def options(self) -> dict:
        return {
            "name_mask": self.name_mask.text(), "ext_mask": self.ext_mask.text(),
            "find": self.find.text(), "replace": self.replace.text(),
            "regex": self.regex.isChecked(), "case": self.case.currentData(),
            "start": self.start.value(), "step": self.step.value(), "digits": self.digits.value(),
        }

    def refresh(self, *_) -> None:
        error = ""
        try:
            self.pairs = rename.plan_names(self.paths, **self.options())
        except FileExistsError as e:
            self.pairs, error = [], f"이미 있는 이름입니다: {Path(str(e)).name}"
        except (OSError, ValueError) as e:
            self.pairs, error = [], str(e)
        new_names = {src: dst.name for src, dst in self.pairs}
        self.preview.clear()
        for src in self.paths:
            self.preview.addTopLevelItem(QTreeWidgetItem([src.name, new_names.get(src, "")]))
        self.status.setText(error or f"{len(self.pairs)}개 항목의 이름이 바뀝니다.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.pairs))
