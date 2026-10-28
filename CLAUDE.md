# Project: Total Commander 스타일 듀얼 패널 파일 관리자

## 개요
키보드 중심 조작의 듀얼 패널 파일 관리자 (macOS 우선, 크로스 플랫폼 지향).

## 스택
- Python 3.13 + PySide6 (Qt)
- 테스트: pytest
- 린트: ruff

## 디렉터리 구조 (계획)
```
src/commander/
  __main__.py      # 진입점
  app.py           # QApplication/메인 윈도우
  panel.py         # 파일 패널 위젯 (탭 포함)
  fs_model.py      # 파일 목록 모델/정렬
  ops.py           # 복사/이동/삭제 (UI 비의존 로직)
  workers.py       # 백그라운드 작업 + 진행률
tests/             # ops, fs_model 등 UI 비의존 로직 위주
```
UI와 파일 조작 로직을 분리해 로직을 pytest로 테스트할 수 있게 한다.

## 명령
```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # pyproject.toml 생성 후
python -m commander          # 실행
pytest                       # 테스트
ruff check .                 # 린트
```

## 규칙
- 코드 작성 전 기존 파일을 먼저 읽는다.
- 전체 재작성보다 부분 수정(Edit)을 선호한다.
- 해법은 단순하게 유지한다. 요청 범위를 넘는 추상화 금지.
- 완료 선언 전 반드시 테스트/실행으로 검증한다.
  - UI/파일시스템 동작 변경 시 `tmp_path` 단위 테스트뿐 아니라 실제 홈 디렉터리(`Path.home()`) 기준 오프스크린 실행으로 성능(숨김/대용량 폴더 포함)과 표시 결과를 먼저 검증한다.
  - 앱 실행 요청 시 기존 실행 중인 `commander` / `Commander.app` 프로세스를 먼저 확인·종료하고, `dist/commander.app` 번들도 최신 코드와 일치하도록 재빌드하거나 실행 경로를 명확히 맞춘다.
  - UX 동작 방식(자동 실행 vs 단축키 트리거 등)이 여러 가지로 해석될 수 있으면 구현 전에 먼저 짧게 질문해 확정한다.
- 파괴적 파일 작업(삭제/덮어쓰기)은 확인 대화상자를 거치고, 테스트는 임시 디렉터리에서만 수행한다.
- 커밋은 기능 단위, 메시지는 `type: summary` (feat/fix/chore/test/docs).

## 진행 관리
- 작업 상태는 `TASKS.md`가 단일 출처다. 세션 시작 시 먼저 읽고, 작업 시작·완료 때마다 갱신한다.
- 기능 하나가 끝나면 `TASKS.md` 갱신을 같은 커밋에 포함한다.
