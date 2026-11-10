# TASKS

작업 진행 상황의 단일 출처. `/exit` 후 재접속해도 바로 이어서 작업할 수 있도록, **모든 작업을 시작·완료할 때마다 이 파일을 갱신하고 커밋에 함께 포함한다.**
상태: `[ ]` 대기 / `[~]` 진행 중 / `[x]` 완료

## 현재 상태 요약 (세션 복원용)
- **버전**: `v0.2.1` (`pyproject.toml`)
- **원격 저장소**: https://github.com/sungback/commander (`origin/main`)
- **릴리스**:
  - `v0.1.0`: macOS 초기 릴리스 (`commander.dmg`, `commander.zip` 수동 업로드 완료)
  - `v0.2.0`: Windows 지원 + 드라이브 선택(`QComboBox`) + GitHub Actions 멀티 플랫폼(`windows-latest`, `macos-latest`) 자동 빌드·릴리스 워크플로 적용
  - `v0.2.1`: 하단 펑션키 바(F2~F8), 숨김 파일 토글(`Ctrl+H`/`Ctrl+.`), 자동 업데이트(`updater.py` / 메뉴 & 백그라운드 확인) 기능 배포
- **즉시 실행/검증 명령**:
  - `./scripts/check.sh` — `ruff` + `pytest` + 실제 홈 디렉터리 오프스크린 스모크/성능 체크
  - `./scripts/run.sh` — 소스 직접 실행 (`--app` 옵션 시 `/Applications/commander.app` 재빌드·설치·실행)
- **진행 중인 작업**: 아래 "로드맵 (Total Commander / ForkLift 수준)"의 **1단계**. 브랜치 `claude/vigilant-davinci-dj4vyo` (미릴리스, 버전은 아직 `v0.2.1`)
- **다음 예정 작업**: 1단계의 남은 `[ ]` 항목을 위에서부터 순서대로

## 로드맵 (Total Commander / ForkLift 수준)
사용자 확정 순서: 1 → 2 → 3 → 4 → 5 → 6. **원격(FTP/SFTP) 보강은 가장 마지막(6단계)**.
단축키는 기존 배치를 유지하고 새 기능만 추가한다 (F2 = 일괄 이름 변경 그대로).

### 1단계 — 일상 조작 기본기
- [x] 휴지통 삭제: `F8`/`Del`/`Ctrl+Backspace`는 휴지통(`ops.trash`, `QFile.moveToTrash`), `Shift+F8`/`Shift+Del`은 영구 삭제
- [x] 충돌 대화상자: 이미 있는 항목마다 Overwrite / Skip / Keep both(로컬만, `이름 (2).ext`) / Cancel + "Apply to all" (`MainWindow._resolve_conflicts`, `ops` `on_conflict`)
- [x] 오류 시 계속 진행: 로컬 복사/이동/삭제는 실패 항목을 건너뛰고 끝에 요약 표시 (`ops` `on_error`)
- [ ] 빠른 필터 (`Ctrl+S`), 뒤로/앞으로 히스토리 (`Alt+←`/`Alt+→`), 폴더 즐겨찾기 (`Ctrl+D`)
- [ ] 패턴 선택 (`+` 선택 / `-` 해제 / `*` 반전)
- [ ] 우클릭 메뉴 (열기·경로 복사·Finder/탐색기에서 보기·터미널 열기), Enter로 파일을 기본 앱에서 열기
- [ ] 패널 간 / 외부 앱과의 드래그 앤 드롭

### 2단계 — 전송 큐
- [ ] 여러 작업 동시 실행, 바이트 기준 진행률·속도·남은 시간, 일시정지/재개, 큐 패널
- [ ] 폴더 덮어쓰기를 "통째 교체"가 아닌 병합으로 (파일 단위 충돌 처리)

### 3단계 — 비교·동기화
- [ ] 디렉터리 비교, 폴더 동기화(미리보기 후 실행), 파일 diff 뷰, 파일 내용 검색, 체크섬

### 4단계 — 보기·압축
- [ ] Quick View 패널(`Ctrl+Q`: 이미지/텍스트/hex), 대용량 파일 뷰어, 압축 만들기(`Alt+F5`), 평면 보기(`Ctrl+B`), 다중 이름 변경 강화(카운터·마스크·대소문자)

### 5단계 — 커스터마이징
- [ ] 설정 대화상자, 단축키 재정의, 하단 명령줄, 사용자 정의 도구/버튼, 다크 모드, 컬럼 구성, UI 문구 언어 통일

### 6단계 — 원격 (마지막)
- [ ] 파일시스템 추상화(로컬/압축/원격 공급자 통일) 후: 원격 삭제·이름 변경·폴더 생성, SSH 키/agent 인증, 연결 즐겨찾기 + OS 키체인, 비동기 연결, FTPS, 원격 파일 편집 후 자동 업로드, 원격↔원격 전송
- [ ] 추가 프로토콜(S3 / WebDAV / SMB) 검토

## MVP
- [x] 스캐폴딩 (pyproject, venv, 진입점) — 19bb4eb
- [x] 듀얼 패널, 경로 바, 정렬, 다중 선택, Tab 전환 — 19bb4eb
- [x] ops.py: 복사/이동/삭제/폴더 생성 (UI 비의존) + pytest (tmp_path)
- [x] F5 복사 / F6 이동 / F7 폴더 생성 / F8 삭제 단축키 + 확인 대화상자 (동기 실행)
- [x] 백그라운드 작업(QThread) + 진행률 대화상자 (취소는 항목 사이에서 반영)
- [x] 탭 (패널별 QTabWidget, Ctrl+T 새 탭 / Ctrl+W 닫기, 마지막 탭은 유지)
- [x] 키보드 내비게이션 보강 (Enter 진입, Backspace 상위, Space/Insert 선택 토글 후 아래로 이동)

## 이후 단계
- [x] 내장 뷰어/에디터 (F3 보기 / F4 편집·저장, 1MB 초과·비UTF-8은 편집 거부)
- [x] 압축 파일 탐색 (archive.py: zip/tar(.gz/.bz2/.xz) 읽기 전용, Enter로 진입·Backspace로 상위, F5로 추출, 경로 탈출 차단)
- [x] 검색 (search.py + SearchWorker, Ctrl+F: 글롭/부분 문자열 재귀 검색, 결과 스트리밍·중지, 5000건 제한, 결과 선택 시 해당 폴더로 이동)
- [x] 일괄 이름 변경 (rename.py, F2: 찾기/바꾸기, `re:` 접두사는 정규식, 미리보기 확인, 충돌·중복 거부, 임시 이름 경유로 교환 가능)
- [x] FTP (remote.py: ftplib, MLSD 필요. Ctrl+N으로 `ftp://[user[:pw]@]host[:port][/path]` 연결, 패널 탭이 원격 모드로 전환, Enter/Backspace 탐색, F5로 다운로드·업로드(폴더 재귀))
- [x] SFTP (remote.py SftpClient: paramiko, FtpClient와 같은 메서드. Ctrl+N에 `sftp://[user[:pw]@]host[:port][/path]`, 패널 로직 공유)
- [x] macOS 패키징 (packaging/build_mac.sh + launcher.py: PyInstaller로 Commander.app, .zip, .dmg 생성)
- [x] 폴더 용량 및 하단 상태줄 (DirSizeWorker로 백그라운드 자동 폴더 용량 계산·Size 표시, 압축 내부 폴더 용량 합산 표시, 패널 하단에 선택/전체 개수·용량 표시)
- [x] `..` 상위 폴더 항상 최상단 고정 (`_SortProxy`) 및 앱 이름(`commander`)·전용 아이콘 적용
- [x] 하네스 엔지니어링 구축 (`CLAUDE.md` 불변식 명문화, `scripts/check.sh`, `scripts/run.sh`, `.git/hooks/pre-commit`)
- [x] 세션 상태 영구 저장·복원 (`~/.config/commander/state.json`: 패널별 정렬 컬럼·방향, 탭 목록·경로, 커서·선택 항목, 창 크기·위치 및 스플리터 비율)
- [x] 폴더 용량 Bottom-up 하위 트리 동시 캐싱 및 디스크 영구 캐시 (`~/.config/commander/dir_sizes.json`: 한 번 계산된 폴더·하위 폴더는 재진입/재시작 시 0ms 즉시 표시, 복사/이동/삭제 시 경로 및 상위 폴더 캐시 자동 무효화)
- [x] Windows 지원 및 멀티 플랫폼 CI/릴리스 (경로/캐시 키 정규화 `ops.norm_key`, Windows 예약어·금지 문자 검증 및 대소문자 이름 변경 허용, 경로 입력창 옆 드라이브 선택 `QComboBox`, Windows `AppUserModelID`, `packaging/build_win.ps1`, `.github/workflows/release.yml`)
- [x] UI 편의 기능 (하단 펑션키 버튼 바 `F2 Rename`~`F8 Delete` 표시 및 클릭 동작, 숨김 파일 `.*` 표시 토글 단축키 `Ctrl+H`/`Ctrl+.` 및 세션 상태 저장)
- [x] 자동 업데이트 (`updater.py`: GitHub Release 최신 버전 확인, `도움말 > 업데이트 확인` 메뉴 및 앱 시작 시 백그라운드 확인, OS별 에셋 자동 다운로드·기존 앱 삭제·새 버전 설치 및 재실행)

## 결정 / 메모
- FTP: 평문(FTPS 미지원), 이동·삭제·폴더 생성·검색·이름 변경은 원격에서 거부, 원격↔원격 전송 불가, 연결은 UI 스레드에서 수행(최대 15초 블록), 취소는 파일 사이에서만 반영
- SFTP: 미등록 호스트 키는 거부(RejectPolicy, known_hosts 사용 → 먼저 ssh로 접속해 등록), 비밀번호 인증만(키 파일 미지원), 심볼릭 링크는 목록 제외, FTP와 같은 제약(이동·삭제 등 원격 거부)
- 다운로드는 로컬 심볼릭 링크를 따라 쓰지 않고 링크 자체를 교체. MLSD size 파싱 오류는 0으로 처리
- 테스트용 SFTP 서버는 test_sftp.py의 인프로세스 paramiko 서버
- 테스트용 FTP 서버는 dev 의존성 pyftpdlib (127.0.0.1 임의 포트)
- 스택: Python 3.13 + PySide6, pytest, ruff
- `.claude/`는 전역 gitignore 대상이라 커밋되지 않음
- 알려진 한계: 취소 시 이미 처리된 항목은 롤백되지 않음, 작업은 동시에 하나만 실행, 폴더 Overwrite는 기존 폴더를 통째로 교체(병합 아님), 원격 전송·압축 추출은 여전히 첫 오류에서 중단하고 충돌 시 Overwrite/Skip만 제공
- 같은 파일로의 덮어쓰기(양쪽 패널이 같은 폴더)는 `ops._target`이 거부한다 (이전에는 원본이 삭제될 수 있었음). 이때 Keep both는 복제본을 만든다
- 휴지통: 테스트는 `tests/conftest.py`의 autouse 픽스처가 `ops._move_to_trash`를 가짜로 바꿔 실제 휴지통을 건드리지 않는다. 휴지통 이동 실패(지원 안 되는 볼륨 등)는 오류 요약에 표시되며 `Shift+F8`로 영구 삭제 가능
- 개발 환경(Linux 컨테이너): PySide6 실행에 `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` apt 패키지 필요
- 압축: 링크/특수 멤버는 목록·추출에서 제외, 압축 안에서 F6/F8 및 압축 패널로의 복사는 거부
- 테스트: QFileSystemModel이 비동기라 `_wait(cond)`로 상태를 기다릴 것 (processEvents 횟수 의존 금지)
