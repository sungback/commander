# 프로젝트 작업 및 릴리스

- 작업 지침과 현황은 `CLAUDE.md`와 `TASKS.md`를 따른다.
- 사용자가 달리 요청하지 않은 한, 코드 변경 작업이 완료되면 macOS와 Windows 자동 업데이트 테스트를 위해 새 패치 버전 릴리스까지 진행한다.
- `pyproject.toml`과 `src/commander/__init__.py`의 버전을 함께 올리고 `TASKS.md`를 갱신한다. `./scripts/check.sh`를 통과시킨 뒤 변경 사항을 커밋하고 `main`과 `v<버전>` 태그를 `origin`에 푸시한다.
- 태그 푸시로 시작되는 GitHub Actions 빌드 및 릴리스 상태를 확인하고 결과를 보고한다. 실패하거나 완료 전이면 그 상태를 명확히 알린다.
