#!/bin/sh
# 기존 실행 중인 commander 프로세스를 정리하고 최신 코드를 실행한다.
# 사용법:
#   ./scripts/run.sh         -> 소스 코드(.venv/bin/python -m commander) 직접 실행
#   ./scripts/run.sh --app   -> dist/commander.app 재빌드 후 실행
set -e
cd "$(dirname "$0")/.."

pkill -f "dist/Commander.app/Contents/MacOS/Commander" 2>/dev/null || true
pkill -f "dist/commander.app/Contents/MacOS/commander" 2>/dev/null || true
pkill -f "/Applications/commander.app/Contents/MacOS/commander" 2>/dev/null || true
pkill -f "python -m commander" 2>/dev/null || true

if [ "${1:-}" = "--app" ]; then
    PATH="$(pwd)/.venv/bin:$PATH" packaging/build_mac.sh
    open /Applications/commander.app
else
    exec .venv/bin/python -m commander
fi
