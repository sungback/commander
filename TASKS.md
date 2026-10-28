# TASKS

작업 진행 상황의 단일 출처. 작업을 시작·완료할 때마다 갱신하고, 커밋에 함께 포함한다.
상태: `[ ]` 대기 / `[~]` 진행 중 / `[x]` 완료

## 현재 작업
- 다음: (미정)

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

## 결정 / 메모
- FTP: 평문(FTPS 미지원), 이동·삭제·폴더 생성·검색·이름 변경은 원격에서 거부, 원격↔원격 전송 불가, 연결은 UI 스레드에서 수행(최대 15초 블록), 취소는 파일 사이에서만 반영
- SFTP: 미등록 호스트 키는 거부(RejectPolicy, known_hosts 사용 → 먼저 ssh로 접속해 등록), 비밀번호 인증만(키 파일 미지원), 심볼릭 링크는 목록 제외, FTP와 같은 제약(이동·삭제 등 원격 거부)
- 다운로드는 로컬 심볼릭 링크를 따라 쓰지 않고 링크 자체를 교체. MLSD size 파싱 오류는 0으로 처리
- 테스트용 SFTP 서버는 test_sftp.py의 인프로세스 paramiko 서버
- 테스트용 FTP 서버는 dev 의존성 pyftpdlib (127.0.0.1 임의 포트)
- 스택: Python 3.13 + PySide6, pytest, ruff
- `.claude/`는 전역 gitignore 대상이라 커밋되지 않음
- 알려진 한계: ops는 첫 오류에서 중단(남은 항목 미처리), 삭제는 영구 삭제(휴지통 아님), 취소 시 이미 처리된 항목은 롤백되지 않음, 작업은 동시에 하나만 실행
- 압축: 링크/특수 멤버는 목록·추출에서 제외, 압축 안에서 F6/F8 및 압축 패널로의 복사는 거부
- 테스트: QFileSystemModel이 비동기라 `_wait(cond)`로 상태를 기다릴 것 (processEvents 횟수 의존 금지)
