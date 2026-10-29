# Windows .exe 빌드 후 commander-win64.zip 생성. 사용: pwsh packaging/build_win.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

New-Item -ItemType Directory -Force -Path build | Out-Null
$env:QT_QPA_PLATFORM = "offscreen"
python -c "from PySide6.QtWidgets import QApplication; from commander.app import app_icon; app = QApplication([]); assert app_icon(256).pixmap(256, 256).save('build/Commander.ico', 'ICO')"

pyinstaller --noconfirm --windowed --name commander `
  --icon build/Commander.ico `
  --paths src `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick `
  packaging/launcher.py

if (Test-Path dist/commander-win64.zip) {
    Remove-Item -Force dist/commander-win64.zip
}
Compress-Archive -Path dist/commander -DestinationPath dist/commander-win64.zip -Force
Get-Item dist/commander-win64.zip
