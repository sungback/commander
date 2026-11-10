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
- **최근 완료**: 로드맵 **1단계·2단계 전체** (브랜치 `claude/vigilant-davinci-dj4vyo`, 미릴리스 — 버전은 아직 `v0.2.1`)
- **다음 예정 작업**: 3단계의 남은 `[ ]` → 4단계 → 5단계 (사용자가 자리를 비운 동안 끝까지 진행하라고 지시). 실기기(macOS/Windows)에서 드래그 앤 드롭·휴지통·큐 패널 동작 확인은 아직 안 됨

## 로드맵 (Total Commander / ForkLift 수준)
사용자 확정: 1 → 2 → 3 → 4 → 5 순서로 끝까지 진행. **원격(FTP/SFTP) 보강(6단계)은 만들지 않는다.**
단축키는 기존 배치를 유지하고 새 기능만 추가한다 (F2 = 일괄 이름 변경 그대로).

### 1단계 — 일상 조작 기본기
- [x] 휴지통 삭제: `F8`/`Del`/`Ctrl+Backspace`는 휴지통(`ops.trash`, `QFile.moveToTrash`), `Shift+F8`/`Shift+Del`은 영구 삭제
- [x] 충돌 대화상자: 이미 있는 항목마다 Overwrite / Skip / Keep both(로컬만, `이름 (2).ext`) / Cancel + "Apply to all" (`MainWindow._resolve_conflicts`, `ops` `on_conflict`)
- [x] 오류 시 계속 진행: 로컬 복사/이동/삭제는 실패 항목을 건너뛰고 끝에 요약 표시 (`ops` `on_error`)
- [x] 빠른 필터 (`Ctrl+S`, `Esc`로 해제): 텍스트 부분 일치 또는 `;` 구분 글롭, 3모드 공통(`name_matches`, 로컬은 `_SortProxy.name_filter`, 압축/원격은 `_show_rows`), 폴더 이동 시 자동 해제
- [x] 뒤로/앞으로 히스토리 (`Alt+←`/`Alt+→`, `Ctrl+[`/`Ctrl+]`): 탭별 로컬 폴더 50개, 사라진 폴더는 건너뜀
- [x] 폴더 즐겨찾기 (`Ctrl+D` 메뉴에서 이동·추가·제거, `state.json`의 `favorites`에 즉시 저장)
- [x] 패턴 선택 (`+` 선택 / `-` 해제 / `*` 반전, 3모드 공통, `..` 제외)
- [x] 활성 패널 추적을 `focusChanged` 기반으로 변경 (경로창·필터창에 포커스가 있어도 해당 패널이 활성)
- [x] Enter/더블클릭으로 로컬 파일을 기본 앱에서 열기 (`MainWindow.open_default`, `QDesktopServices`)
- [x] 우클릭 메뉴 (모드별 항목: 열기·보기·편집·복사/추출/다운로드·이동·이름 변경·휴지통·영구 삭제·경로 복사·Finder/탐색기에서 보기·터미널 열기·새 폴더·즐겨찾기 추가), 경로 복사 `Ctrl+Shift+C` (3모드 공통), `shell.py`
- [x] 드래그 앤 드롭: 패널 간 및 외부 앱 → 로컬 패널로 드롭 시 복사(Shift는 이동), 폴더 행 위에 놓으면 그 폴더로. F5/F6과 같은 확인·충돌 흐름(`_transfer_local`). 압축/원격 패널은 드롭 거부, `..`는 드래그 불가
- **1단계 완료.** 남은 확인 사항: 실제 마우스 드래그(패널 간, Finder ↔ 앱)와 휴지통 이동은 헤드리스 테스트로는 이벤트/스텁 수준까지만 검증됨 → macOS/Windows 실기기에서 한 번 확인 필요

### 2단계 — 전송 큐 (완료)
- [x] 전송 엔진 `transfer.py` (UI 비의존): 사전 스캔으로 총 바이트 계산, 1MB 청크 복사, 일시정지/재개/취소(`Control`), 진행 카운터(`Stats`), 임시 파일(`*.commander-part`)에 쓴 뒤 `os.replace`로 교체 → 취소·실패 시 잘린 파일이 남지 않고 기존 파일도 복사가 끝날 때까지 보존
- [x] 폴더 병합: 대상에 같은 폴더가 있으면 통째 교체하지 않고 합치며, 겹치는 파일만 파일 단위로 묻는다 (이동 시 건너뛴 항목은 원본 폴더에 남음)
- [x] 백그라운드 작업 `workers.Job` + 하단 큐 패널 `queue_panel.py`: 로컬 복사/이동, 삭제/휴지통, 압축 추출이 창을 막지 않고 **동시에** 실행. 작업별 진행 막대·`완료/전체 크기`·속도·남은 시간·현재 파일, Pause/Resume·Cancel 버튼. 패널은 작업이 없으면 숨김
- [x] 충돌 질문은 작업이 해당 파일에 도달했을 때 표시(양쪽 크기·수정 시각 안내, "Apply to all"은 그 작업에만 적용). 여러 작업이 동시에 물어도 대화상자는 한 번에 하나
- [x] 작업 실행 중 창을 닫으면 확인 후 취소·대기하고 종료
- 원격 업로드/다운로드는 기존 모달 진행 대화상자 유지 (FTP/SFTP 연결을 탐색과 동시에 쓸 수 없음) → 6단계에서 큐로 편입

### 3단계 — 비교·동기화 (완료)
- [x] 파일 내용 검색: `Ctrl+F`에서 이름 마스크 다음에 "포함 텍스트"를 물음 (`search.contains`: 청크 단위, ASCII 대소문자 무시, UTF-8/UTF-16)
- [x] 체크섬: `도구 > 체크섬 계산...`(SHA-256/SHA-1/MD5, 폴더 재귀, `sha256sum` 형식 결과 복사·저장), `도구 > 체크섬 검증`(선택한 `.sha256/.sha1/.md5` 목록 검증, 목록 폴더 밖 경로는 INVALID) — `checksum.py`, `dialogs.TextDialog`
- [x] 폴더 비교 (`Shift+F2`): 양쪽 패널에서 한쪽에만 있거나 더 최신인 항목을 선택 (`compare.differing_names`, 최상위만, 2초 이내 시각 차이는 같은 것으로 봄)
- [x] 파일 내용 비교 (`Ctrl+Shift+D`): 활성 패널의 두 파일, 또는 한 파일과 반대편 패널의 같은 이름/선택 파일. 좌우 나란히 보기, 차이 강조, `F3`/`Shift+F3`로 차이 이동 (`diff_dialog.py`). 바이너리·2MB 초과는 같음/다름만 알림. UTF-8/UTF-16/CP949 인식
- [x] 폴더 동기화 (`Ctrl+Shift+S`, `sync.py` + `sync_dialog.py`): 하위 폴더까지 비교 → 항목별 동작(→ ← 삭제 충돌)을 표로 검토·체크 해제·방향 수동 지정 → 확인 후 큐의 백그라운드 작업으로 실행. 모드: 양방향(최신 우선) / 한 방향. 옵션: 대상에만 있는 항목 삭제(휴지통), 내용으로 비교, 숨김 파일 포함. 심볼릭 링크는 무시, 자기 자신·하위 폴더와의 동기화는 거부
- **3단계 완료.**

### 4단계 — 보기·압축 (완료)
- [x] 다중 이름 변경 강화 (`F2` / `Ctrl+M`, `rename_dialog.py`): 이름·확장자 마스크(`[N]` `[E]` `[C]` `[P]` `[YMD]` `[hms]`), 카운터(시작/증가/자릿수), 찾기→바꾸기(정규식), 대소문자, 실시간 미리보기와 충돌 검사 (`rename.plan_names`). 선택 항목은 화면 순서대로 처리
- [x] 압축 만들기 (`Alt+F5`, `archive.create`): 선택 항목을 반대편 패널 폴더에 zip / tar / tar.gz / tar.bz2 / tar.xz로. 큐 작업(진행률·일시정지·취소), 임시 파일에 쓴 뒤 교체, 심볼릭 링크 제외, 자기 자신을 포함하는 위치는 거부
- [x] 뷰어 확장 (`preview.py` + `preview_widget.py`): F3가 크기 제한 없이 조각 단위(텍스트 256KB / hex 64KB)로 스크롤에 맞춰 불러옴. 텍스트(UTF-8/UTF-16/CP949 자동 인식), 바이너리는 hex 덤프, 이미지는 화면에 맞춰 표시. 뷰어 안에서 `F3`로 hex 전환. F4 편집은 기존대로 1MB 이하 UTF-8만
- [x] Quick View (`Ctrl+Q`, macOS는 `Control+Q` — `Cmd+Q`는 종료라서): 비활성 패널 자리에 커서 아래 파일의 미리보기를 표시, 커서 이동 시 갱신. 압축·원격 항목은 안내 문구만 표시
- [x] 평면 보기 (`Ctrl+B`): 현재 로컬 폴더 아래 모든 파일을 한 목록에 표시(최대 20,000개, 숨김 설정 반영). 로컬 모드의 한 형태라 선택 항목이 실제 경로이며 F3~F8·필터·패턴 선택이 그대로 동작. `..`/Backspace/폴더 이동 시 해제, 작업 종료 시 목록 새로 고침
- **4단계 완료.**

### 5단계 — 커스터마이징
- [ ] 설정 대화상자, 단축키 재정의, 하단 명령줄, 사용자 정의 도구/버튼, 다크 모드, 컬럼 구성, UI 문구 언어 통일

### 6단계 — 원격: **제외** (사용자 결정: 원격 기능은 만들지 않는다. 기존 FTP/SFTP 탐색·전송은 현 상태 유지)

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
- 알려진 한계: 취소 시 이미 처리된 항목은 롤백되지 않음, 원격 전송·압축 추출은 여전히 첫 오류에서 중단하고 충돌 시 최상위 항목 기준 Overwrite/Skip만 제공(압축 추출의 폴더 Overwrite는 통째 교체), 동시 작업 수 제한 없음(같은 파일을 건드리는 작업을 동시에 돌리는 것은 사용자 책임)
- 진행률은 시그널이 아니라 UI 타이머(200ms)가 `Job.stats`를 폴링한다 → 64비트 크기 시그널 문제와 시그널 폭주를 피함. 워커 → UI 시그널은 충돌 질문(`Job.conflict`)과 `finished`뿐
- `transfer.py`는 진행률/일시정지를 위해 파이썬 청크 복사를 쓰므로 `shutil`의 커널 고속 복사(sendfile, APFS clone)보다 느리다(컨테이너 측정: 약 1.3GB/s vs 2.7GB/s). 같은 볼륨 이동은 `rename`이라 즉시 끝남
- `ops.copy`/`ops.move`는 라이브러리 함수로 남아 있으나 UI는 더 이상 쓰지 않는다(로컬 전송은 `transfer.transfer`)
- 같은 파일로의 덮어쓰기(양쪽 패널이 같은 폴더)는 `ops._target`이 거부한다 (이전에는 원본이 삭제될 수 있었음). 이때 Keep both는 복제본을 만든다
- 휴지통: 테스트는 `tests/conftest.py`의 autouse 픽스처가 `ops._move_to_trash`를 가짜로 바꿔 실제 휴지통을 건드리지 않는다. 휴지통 이동 실패(지원 안 되는 볼륨 등)는 오류 요약에 표시되며 `Shift+F8`로 영구 삭제 가능
- 개발 환경(Linux 컨테이너): PySide6 실행에 `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` apt 패키지 필요
- 압축: 링크/특수 멤버는 목록·추출에서 제외, 압축 안에서 F6/F8 및 압축 패널로의 복사는 거부
- 테스트 안전장치: `tests/conftest.py` autouse 픽스처가 휴지통(`ops._move_to_trash`), 외부 프로세스 실행(`shell.launch`), 기본 앱 열기(`QDesktopServices.openUrl`)를 모두 스텁 처리한다
- `.git/hooks/pre-commit`은 저장소에 포함되지 않으므로 새로 클론하면 직접 설치해야 한다 (`exec ./scripts/check.sh`)
- 테스트: QFileSystemModel이 비동기라 `_wait(cond)`로 상태를 기다릴 것 (processEvents 횟수 의존 금지)
