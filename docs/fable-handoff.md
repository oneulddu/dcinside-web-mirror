# Fable Handoff - 숨터 기본 페이지 리디자인

## Repo and Framework

- Flask 앱, Jinja 템플릿, 순수 CSS/JavaScript.
- 기본 화면은 `app/templates/`와 `app/static/`가 담당한다.
- 루트 경로(`/`, `/board`, `/read`, `/recent`)가 기본 화면을 렌더링한다.

## Files to Edit

- `DESIGN.md`
- `docs/ux-flow.md`
- `app/templates/base.html`
- `app/templates/index.html`
- `app/templates/board.html`
- `app/templates/read.html`
- `app/templates/recent.html`
- `app/static/css/main.css`
- `app/static/javascript/read_related_loader.js`
- `app/static/javascript/read_state.js`

## UX Flow Summary

- 사용자는 홈에서 갤러리를 찾고, 목록에서 글을 고르고, 읽기 화면에서 본문과 댓글을 본 뒤 관련 글이나 목록으로 이동한다.
- 화면은 고밀도 읽기 도구여야 하며, 장식보다 제목, 메타, 검색, 페이저의 가독성이 우선이다.
- 390px 모바일에서도 검색 폼, 페이저, 긴 제목, 댓글 메타가 겹치지 않아야 한다.

## Implementation Tasks

1. `DESIGN.md` 토큰 계약을 유지하며 기본 화면 전체를 같은 시각 언어로 정리한다.
2. `base.html` 마스트헤드를 더 선명한 앱 헤더로 바꾸고 실제 `favicon.svg` 브랜드 표식을 사용한다.
3. 홈 검색, 갤러리 목록, 게시판 목록, 최근 방문 목록의 행 밀도와 배지를 정리한다.
4. 읽기 화면의 크럼, 제목, 메타, 본문, 댓글, 관련 글 간격을 안정화한다.
5. 모바일에서 검색 폼과 페이저가 자연스럽게 줄바꿈되게 한다.
6. hover, focus-visible, disabled, active, empty 상태를 모두 보이게 한다.
7. 목록 행 제목은 `h2` 대신 일반 텍스트 요소로 바꿔 헤딩 구조를 정리한다.
8. 테마 토글 초기 HTML과 JS 갱신 상태가 서로 충돌하지 않게 한다.
9. 관련 글 더보기 JS가 만드는 행 구조를 서버 템플릿 행과 맞춘다.

## Constraints

- 사용자는 Fable 사용을 명시했다. critique와 implement 모드를 실행해야 한다.
- Superloopy frontend 규칙을 따라 `DESIGN.md` 토큰이 먼저 있어야 한다.
- raw hex는 CSS 변수 선언 안에서만 허용한다.
- 보라 그라디언트, 그림자, 가짜 스크린샷, 장식용 섹션은 쓰지 않는다.
- em dash 문자는 visible copy에 쓰지 않는다.
- 기존 Flask 라우트와 데이터 모델을 바꾸지 않는다.
- 레거시 화면은 건드리지 않는다.
- 게시판은 전체 페이지 수를 알 수 없으므로 다음 링크는 유지하되, 빈 목록과 비활성 이전 상태가 깨지지 않게 한다.

## Browser Checks

- `make test`
- 로컬 서버 실행 후 `/`, `/board?board=airforce&page=1`, `/read?board=airforce&pid=<실제글>`, `/recent` 확인
- 390px, 768px, 1280px 스크린샷 저장
- 테마 전환 버튼, 홈 검색 폼 focus 상태, 관련 글 더보기 상태 확인
- `.superloopy/evidence/frontend/<timestamp>-mirror-redesign/VISUAL_QA.md` 작성
