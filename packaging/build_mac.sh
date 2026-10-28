#!/bin/sh
# macOS .app 빌드 후 .zip/.dmg 생성. 사용: packaging/build_mac.sh (venv 활성화 + pip install pyinstaller)
set -e
cd "$(dirname "$0")/.."
pyinstaller --noconfirm --windowed --name Commander \
  --osx-bundle-identifier com.sungback.commander \
  --paths src --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick \
  packaging/launcher.py
ditto -c -k --keepParent dist/Commander.app dist/Commander.zip
rm -rf dist/dmg && mkdir dist/dmg && cp -R dist/Commander.app dist/dmg/
ln -s /Applications dist/dmg/Applications
rm -f dist/Commander.dmg
hdiutil create -quiet -volname Commander -srcfolder dist/dmg -ov -format UDZO dist/Commander.dmg
rm -rf dist/dmg
ls -lh dist/Commander.zip dist/Commander.dmg
