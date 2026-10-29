# commander

Total Commander 스타일의 키보드 중심 듀얼 패널 파일 관리자 (macOS & Windows 지원, Python + PySide6 기반).

## 주요 기능

- **듀얼 패널 & 멀티 탭**
  - 좌우 독립 패널(`Tab` 전환) 및 패널별 멀티 탭(`Ctrl+T` / `Ctrl+W`)
  - 경로 입력창 좌측 드라이브 선택 드롭다운(`C:\`, `D:\`, `/` 등 즉시 전환)
  - 모든 정렬 기준과 방향에서 `..` 상위 폴더 최상단 고정 및 폴더 → 파일 순 정렬
  - 패널 하단 상태 표시줄에 선택 항목 및 전체 항목 개수·용량 실시간 표시
- **키보드 중심 파일 작업**
  - 복사(`F5`), 이동(`F6`), 새 폴더(`F7`), 삭제(`F8`) — 백그라운드 스레드 실행 및 진행률·취소 지원
  - 일괄 이름 변경(`F2`) — 일반 치환 및 정규식(`re:` 접두사) 지원, 미리보기 및 충돌 방지
  - 내장 뷰어(`F3`) 및 에디터(`F4`) — 1MB 이하 UTF-8 텍스트 즉시 편집·저장
  - 스트리밍 파일 검색(`Ctrl+F`) — 글롭/부분 문자열 재귀 검색 및 선택한 결과 폴더로 즉시 이동
- **압축 파일 탐색**
  - `zip`, `tar`, `tar.gz`(`tgz`), `tar.bz2`(`tbz2`), `tar.xz`(`txz`) 아카이브를 풀지 않고 폴더처럼 탐색(`Enter` / `Backspace`)
  - 반대편 로컬 패널로 선택한 항목만 추출(`F5`, 경로 탈출 방지 포함)
- **원격 파일 시스템 (FTP / SFTP)**
  - `Ctrl+N`으로 `ftp://[user[:pw]@]host[:port][/path]` 또는 `sftp://[user[:pw]@]host[:port][/path]` 연결
  - 로컬 패널과 동일한 방식으로 탐색 및 업로드·다운로드(`F5`, 재귀 폴더 전송 지원)
- **빠른 폴더 용량 계산 & 영구 캐시**
  - 백그라운드에서 Bottom-up 방식으로 하위 트리 폴더 용량을 한 번의 순회로 동시 계산
  - 계산된 용량을 `~/.config/commander/dir_sizes.json`에 캐싱하여 재방문·재시작 시 0ms 즉시 표시 (파일 복사/이동/삭제 시 관련 경로 자동 무효화)
- **세션 상태 자동 저장·복원**
  - 창 크기·위치, 스플리터 비율, 패널별 탭 목록·경로, 정렬 컬럼·방향, 커서 및 선택 항목을 `~/.config/commander/state.json`에 자동 보존

---

## 단축키

| 단축키 | 동작 |
| --- | --- |
| `Tab` | 좌 / 우 패널 전환 |
| `Enter` / 더블클릭 | 폴더, 압축 파일 진입 또는 파일 실행 |
| `Backspace` | 상위 폴더로 이동 (압축·원격 루트에서는 로컬로 복귀) |
| `Space` / `Insert` | 현재 항목 선택 토글 후 다음 행으로 이동 |
| `Ctrl+T` (`Cmd+T`) | 현재 패널에 새 탭 열기 |
| `Ctrl+W` (`Cmd+W`) | 현재 탭 닫기 |
| `Ctrl+F` (`Cmd+F`) | 하위 폴더 재귀 파일 검색 |
| `Ctrl+N` (`Cmd+N`) | FTP / SFTP 원격 서버 연결 |
| `Ctrl+H` / `Ctrl+.` | 숨김 파일(`.*`) 표시 토글 |
| `F2` | 선택 항목 일괄 이름 변경 (`re:` 접두사로 정규식 사용 가능) |
| `F3` | 내장 파일 뷰어 (읽기 전용) |
| `F4` | 내장 텍스트 에디터 (1MB 이하 UTF-8 편집·저장) |
| `F5` | 반대편 패널로 복사 / 압축 추출 / 원격 업로드·다운로드 |
| `F6` | 반대편 패널로 이동 |
| `F7` | 새 폴더 만들기 |
| `F8` | 선택 항목 삭제 |

---

## 설치 및 실행

### 1. 릴리스 설치 (macOS / Windows)
[Releases](https://github.com/sungback/commander/releases) 페이지에서 운영체제에 맞는 최신 패키지를 다운로드합니다:
- **macOS**: `commander.dmg` 또는 `commander.zip` 다운로드 후 `commander.app`을 `/Applications`에 복사해 실행
- **Windows**: `commander-win64.zip` 다운로드 및 압축 해제 후 `commander/commander.exe` 실행

### 2. 소스에서 실행
Python 3.11 이상이 필요합니다.

```sh
git clone https://github.com/sungback/commander.git
cd commander
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

# 소스 직접 실행
./scripts/run.sh
```

### 3. 패키징 빌드 (macOS / Windows)
```sh
pip install pyinstaller

# macOS (.app / .dmg / .zip 생성 및 /Applications 설치)
./packaging/build_mac.sh

# Windows (dist/commander-win64.zip 생성, PowerShell)
pwsh ./packaging/build_win.ps1
```

---

## 개발 및 검증

커밋 전 린트(`ruff`), 단위·오프스크린 UI 테스트(`pytest`), 실제 디렉터리 헤드리스 스모크 체크를 한 번에 실행합니다:

```sh
./scripts/check.sh
```
