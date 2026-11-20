"""Dialogs for local file information and explicit attribute changes."""
import datetime
import stat
from pathlib import Path

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)


class PropertiesDialog(QDialog):
    """Read-only metadata; potentially expensive size calculation is caller-owned."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle('속성')
        self.resize(500, 250)
        info = path.lstat()
        kind = ('심볼릭 링크' if stat.S_ISLNK(info.st_mode) else
                '폴더' if stat.S_ISDIR(info.st_mode) else '파일')
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('이름:', QLabel(path.name))
        label = QLabel(str(path.absolute()))
        label.setTextInteractionFlags(label.textInteractionFlags() | label.textInteractionFlags().TextSelectableByMouse)
        label.setWordWrap(True)
        form.addRow('경로:', label)
        form.addRow('종류:', QLabel(kind))
        form.addRow('크기:', QLabel(f'{info.st_size:,} 바이트' if kind == '파일' else '폴더 용량 계산 사용'))
        form.addRow('수정:', QLabel(datetime.datetime.fromtimestamp(info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')))
        form.addRow('권한:', QLabel(stat.filemode(info.st_mode)))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ChangeAttributesDialog(QDialog):
    """Collect changes without altering files; caller executes via job queue."""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle('속성 변경')
        self.resize(450, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'{count}개 선택 항목에 적용할 속성만 선택하세요.'))
        self.readonly = QComboBox()
        self.readonly.addItems(['변경 안 함', '읽기 전용으로 설정', '쓰기 허용'])
        self.change_date = QCheckBox('수정 날짜 변경')
        self.date = QDateTimeEdit(QDateTime.currentDateTime())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.date.setEnabled(False)
        self.change_date.toggled.connect(self.date.setEnabled)
        self.recursive = QCheckBox('선택한 폴더의 하위 항목에도 적용')
        form = QFormLayout()
        form.addRow('쓰기 권한:', self.readonly)
        form.addRow(self.change_date, self.date)
        layout.addLayout(form)
        layout.addWidget(self.recursive)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if self.readonly.currentIndex() == 0 and not self.change_date.isChecked():
            QMessageBox.information(self, '속성 변경', '변경할 속성을 선택하세요.')
            return
        if self.recursive.isChecked() and QMessageBox.question(
                self, '속성 변경', '하위 폴더와 파일에도 선택한 변경을 적용할까요?') != QMessageBox.Yes:
            return
        self.accept()

    def values(self) -> dict:
        index = self.readonly.currentIndex()
        return {'readonly': None if index == 0 else index == 1,
                'modified': self.date.dateTime().toSecsSinceEpoch() if self.change_date.isChecked() else None,
                'recursive': self.recursive.isChecked()}
