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

현재 시점에서는 `legacy/`를 즉시 삭제하지 않고 보관합니다.

이유:

- 디렉터리가 이미 Git에 추적 중이라 `.gitignore`만 추가해도 효과가 없습니다.
- `git rm --cached -r legacy` 또는 삭제는 저장소에서 대량 파일 제거로 나타납니다.
- 지금 진행 중인 리팩터링 변경과 섞으면 검토 범위가 불필요하게 커집니다.

## 나중에 완전히 제거할 때 권장 절차

1. 현재 브랜치가 안정화된 뒤 별도 PR 또는 별도 커밋으로 처리합니다.
2. 필요한 경우 삭제 전 보관 태그를 만듭니다.
3. 다음 중 하나를 선택합니다.
   - 저장소에서 제거: `git rm -r legacy`
   - 로컬 보관만 유지: `.gitignore`에 `legacy/` 추가 후 `git rm --cached -r legacy`
4. README의 프로젝트 구조에서 `legacy/` 항목을 제거하거나 아카이브 설명으로 바꿉니다.
