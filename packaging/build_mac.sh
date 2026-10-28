#!/bin/sh
# macOS .app 빌드 후 .zip/.dmg 생성. 사용: packaging/build_mac.sh (venv 활성화 + pip install pyinstaller)
set -e
cd "$(dirname "$0")/.."
rm -rf build/Commander.iconset build/Commander.icns
mkdir -p build/Commander.iconset
QT_QPA_PLATFORM=offscreen python3 - <<'EOF'
from PySide6.QtWidgets import QApplication
from commander.app import app_icon
app = QApplication([])
for sz in (16, 32, 64, 128, 256, 512):
    app_icon(sz).pixmap(sz, sz).save(f"build/Commander.iconset/icon_{sz}x{sz}.png")
    app_icon(sz * 2).pixmap(sz * 2, sz * 2).save(f"build/Commander.iconset/icon_{sz}x{sz}@2x.png")
EOF
iconutil -c icns build/Commander.iconset -o build/Commander.icns
SIGN_ID="${CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Apple Development:[^"]*\)".*/\1/p' | head -n 1)}"
pyinstaller --noconfirm --windowed --name commander \
  --icon build/Commander.icns \
  --osx-bundle-identifier com.sungback.commander \
  ${SIGN_ID:+--codesign-identity "$SIGN_ID"} \
  --paths src --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick \
  packaging/launcher.py
ditto -c -k --keepParent dist/commander.app dist/commander.zip
rm -rf dist/dmg && mkdir dist/dmg && cp -R dist/commander.app dist/dmg/
ln -s /Applications dist/dmg/Applications
rm -f dist/commander.dmg
hdiutil create -quiet -volname commander -srcfolder dist/dmg -ov -format UDZO dist/commander.dmg
rm -rf dist/dmg
rm -rf /Applications/commander.app
ditto dist/commander.app /Applications/commander.app
ls -lh dist/commander.zip dist/commander.dmg /Applications/commander.app
