# legacy 디렉터리 정리 기록

`legacy/`는 이전 Flask 단일 구조에서 사용하던 코드와 정적 리소스 보관용 디렉터리입니다.

## 현재 판단

- 현재 애플리케이션 진입점은 `app/`, `run.py`, `wsgi.py`입니다.
- `app/` 및 테스트 코드에서 `legacy` 모듈을 import하지 않습니다.
- `legacy/README.md`에도 현재 서비스는 `app/` 하위 코드만 사용한다고 명시되어 있습니다.
- 따라서 `legacy/`는 현재 런타임에는 필요하지 않습니다.

## 보관 파일 범위

대표 파일:

- `legacy/core.py`
- `legacy/dc_api.py`
- `legacy/tools.py`
- `legacy/templates/`
- `legacy/static/`
- `legacy/gallerys*.json`

## 정리 결정

2026-06-10 P3-3 정리 작업에서 `legacy/`를 저장소에서 제거했습니다.

삭제 전 판단 근거:

- 현재 런타임과 테스트 코드에서 `legacy` 경로를 사용하지 않았습니다.
- 보관 파일 범위는 위 목록에 기록되어 있습니다.
- 런타임 코드와 섞이지 않도록 별도 정리 PR에서 삭제했습니다.

## 제거 절차 기록

1. `git rm -r legacy`로 추적 파일을 제거했습니다.
2. stale `.claude/worktrees` 사본은 Git worktree에서 제거한 뒤 prune했습니다.
3. 로컬 `__pycache__` 산출물을 정리했습니다.
