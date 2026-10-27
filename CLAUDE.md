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
- 파괴적 파일 작업(삭제/덮어쓰기)은 확인 대화상자를 거치고, 테스트는 임시 디렉터리에서만 수행한다.
- 커밋은 기능 단위, 메시지는 `type: summary` (feat/fix/chore/test/docs).

## MVP 범위 / 진행
- [x] 프로젝트 스캐폴딩 (pyproject, 진입점)
- [ ] 듀얼 패널 + 탭 (듀얼 패널 완료, 탭 미구현)
- [ ] 키보드 조작: F5 복사, F6 이동, F7 폴더 생성, F8 삭제, Tab 패널 전환
- [ ] 다중 선택, 정렬, 경로 바
- [ ] 복사/이동/삭제 백그라운드 처리 + 진행률

## 이후 단계 (MVP 이후)
내장 뷰어/에디터, 압축 파일 탐색, 검색, 일괄 이름 변경, FTP/SFTP
