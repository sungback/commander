# Project: commander (Total Commander 스타일 듀얼 패널 파일 관리자)

## 개요
키보드 중심 조작의 듀얼 패널 파일 관리자 (macOS 우선, 크로스 플랫폼 지향).

## 스택
- Python 3.13 + PySide6 (Qt)
- 원격: `ftplib` (FTP), `paramiko` (SFTP)
- 패키징: PyInstaller (`packaging/build_mac.sh`)
- 테스트 / 린트: `pytest`, `ruff`

## 실제 디렉터리 구조
```
src/commander/
  __main__.py       # 진입점 (`python -m commander`)
  app.py            # MainWindow, 단축키(Tab, Ctrl+T/W/F/N, F2~F8), 앱 아이콘, macOS 번들명 설정
  panel.py          # FilePanel, _Tab, _View, _LocalModel, _SortProxy, 하단 상태 표시줄
  ops.py            # 로컬 복사/이동/삭제/폴더 생성/폴더 용량 계산(dir_size)/크기 포맷 (UI 비의존)
  archive.py        # zip/tar(.gz/.bz2/.xz) 읽기 전용 탐색 및 추출 (UI 비의존)
  remote.py         # FtpClient, SftpClient (동일 메서드 인터페이스, UI 비의존)
  rename.py         # 일괄 이름 변경 계획(plan) 및 임시 이름 경유 적용(apply)
  search.py         # 글롭/부분 문자열 재귀 파일 검색 제너레이터
  search_dialog.py  # Ctrl+F 검색 대화상자 (스트리밍 결과 + 선택 폴더 이동)
  viewer.py         # F3 뷰어 / F4 에디터 (1MB 이하 UTF-8만 편집 허용)
  workers.py        # Worker(파일 작업), SearchWorker(검색), DirSizeWorker(백그라운드 폴더 용량)
packaging/
  launcher.py       # PyInstaller 진입점
  build_mac.sh      # commander.app / commander.zip / commander.dmg 빌드 스크립트
scripts/
  check.sh          # 커밋 전 필수 통과 하네스 (ruff + pytest + 실제 홈 디렉터리 헤드리스 검증)
  run.sh            # 기존 프로세스 정리 후 최신 코드 실행 (--app 옵션 시 .app 재빌드 후 실행)
tests/              # 단위 및 오프스크린 UI 통합 테스트
```

## 핵심 명령 (하네스)
```sh
./scripts/check.sh           # 검증 게이트 (ruff + pytest + 실제 홈 디렉터리 오프스크린 스모크/성능 체크)
./scripts/run.sh             # 기존 실행 중인 앱 종료 후 소스 직접 실행
./scripts/run.sh --app       # 기존 앱 종료 후 재빌드 및 /Applications/commander.app 설치·실행
```

## 아키텍처 불변식 (Invariants) & 주의사항 (Gotchas)

1. **패널 3모드 일관성 (`로컬` / `in_archive` / `in_remote`)**
   - 모든 패널·윈도우 기능 추가/수정 시 반드시 3가지 모드에서의 동작을 확인한다:
     - **로컬**: `_SortProxy(_LocalModel)` 사용, 모든 파일 작업(F2~F8, Ctrl+F) 가능.
     - **압축 (`in_archive`)**: `arch_model`(`QStandardItemModel`) 사용, 읽기/F5 추출만 가능(수정 작업 거부), 폴더 용량은 압축 엔트리 합산으로 즉시 표시.
     - **원격 (`in_remote`)**: `arch_model` 사용, 탐색 및 F5 업로드/다운로드 가능.
2. **목록 정렬 및 `..` 최상단 고정**
   - 로컬(`_SortProxy`), 압축, 원격 모든 모드와 모든 컬럼/정렬 방향(오름·내림차순)에서 첫 번째 행(`row 0`)은 항상 `..`이어야 하며, 그 아래로 폴더 → 파일 순서를 유지한다.
   - `panel.model`은 외부에 프록시 인덱스 기준으로 `index()`, `filePath()`, `fileName()`, `isDir()`, `size()`를 일관되게 노출해야 한다 (`_fs_model` 소스 인덱스와 혼용 금지).
3. **Qt / PySide6 스레딩 & 시그널 규칙**
   - `QFileSystemModel`은 비동기이므로 `rowsInserted`/`directoryLoaded`가 여러 번 발생한다. 이미 실행 중인 `DirSizeWorker`를 불필요하게 재시작하지 않는다.
   - `QThread`가 실행 중일 때 프로세스가 종료되면 `QThread: Destroyed while thread is still running (SIGABRT, exit 134)`가 발생하므로 백그라운드 워커는 `atexit` 또는 탭 닫기 시 안전하게 정리한다.
   - 64비트 바이트 크기를 Qt `Signal`로 전달할 때는 32비트 C++ `int` 오버플로를 피하기 위해 `Signal(str, object)`를 사용한다.
   - 백그라운드 폴더 용량 스캔(`DirSizeWorker`)은 숨김 폴더(`.*`)를 건너뛰고, 1차 빠른 스캔(`max_dirs=60`) 후 대용량 폴더만 2차 정밀 스캔하여 체감 지연을 막는다.
4. **보안 및 안전성**
   - 파괴적 파일 작업(삭제/덮어쓰기)은 확인 대화상자를 거치고, 테스트는 반드시 `tmp_path`에서만 수행한다.
   - 압축 해제 및 원격 파일명 처리 시 경로 탈출(`..`, `/`, `\`, `\0`)과 로컬 심볼릭 링크 덮어쓰기 공격을 차단한다.

## 작업 및 커밋 규칙
- 코드 작성 전 관련 파일을 먼저 읽고, 전체 재작성보다 부분 수정(Edit)을 선호한다.
- UX 동작 방식(자동 실행 vs 단축키 트리거 등)이 모호하면 구현 전에 짧게 질문해 확정한다.
- 완료 선언 및 커밋 전 반드시 `./scripts/check.sh`를 실행해 통과를 확인한다 (`pre-commit` 훅으로 자동 강제됨).
- 작업 상태는 `TASKS.md`가 단일 출처다. 기능 완료 시 `TASKS.md` 갱신을 같은 커밋에 포함한다.
- 커밋 메시지는 `type: summary` (`feat`/`fix`/`chore`/`test`/`docs`).
