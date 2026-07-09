# Fable Handoff - 읽기 본문 임베드 높이 개선

## Repo and Framework

- Flask 앱, Jinja 템플릿, 순수 CSS/JavaScript.
- 기본 읽기 화면은 `app/templates/read.html`와 `app/static/css/main.css`가 렌더링한다.
- 본문 HTML은 `app/services/html_sanitizer.py`의 `prepare_read_html()`을 거쳐 `<article class="article-body">` 안에 들어간다.

## User Report

- 사용자가 "유튜브 같은 임베디드 세로축이 너무 작은 것 같다"고 했다.
- 읽기 화면 본문에서 YouTube, DCInside 동영상, 투표 같은 `iframe` 임베드가 지나치게 낮게 보이지 않도록 개선한다.

## Current Findings

- 새 CSS의 `.article-body iframe`은 `width: 100%`와 `height: auto`만 받는다.
- iframe에 원본 `height`가 없거나 작으면 브라우저 기본 높이 또는 작은 속성값에 묶여 영상 영역이 납작해질 수 있다.
- sanitizer 통과 후 DCInside 동영상 iframe의 실제 `src`는 `app/services/html_sanitizer.py`에서 로컬 `/movie?no=...` 형태로 바뀐다. 외부 원본 주소 셀렉터만 사용하면 현재 화면의 DC 동영상에는 적용되지 않는다.
- sanitizer 통과 후 투표 iframe은 상대 `/poll?...` 또는 `https://m.dcinside.com/poll?...` 형태가 될 수 있다.
- sanitizer 통과 후 YouTube iframe은 `https://www.youtube.com/embed/...` 또는 `https://www.youtube-nocookie.com/embed/...` 형태다.
- 구현 시 참고한 목적별 규칙은 아래와 같다.
  - poll iframe: `min-height: 400px`
  - DCInside movie iframe: `aspect-ratio: 9 / 16`, `width: min(100%, 360px)`, `max-height`
  - YouTube iframe: `aspect-ratio: 16 / 9`
- 새 기본 CSS에는 위 목적별 iframe 규칙이 없다.

## UX Flow Summary

- 사용자는 `/read` 화면에서 글 본문을 위에서 아래로 읽는다.
- 본문 중간에 영상이나 투표가 있으면, 사용자는 추가 조작 없이 콘텐츠 전체 비율을 자연스럽게 확인할 수 있어야 한다.
- 영상은 읽기 컬럼을 넘치지 않아야 하고, 모바일에서도 가로 스크롤이 없어야 한다.
- 세로형 DCInside 동영상은 본문 전체 폭으로 과하게 커지지 않고 중앙에 놓여야 한다.
- YouTube 임베드는 일반 16:9 영상으로 충분한 높이를 확보해야 한다.

## Files to Edit

- Primary: `app/static/css/main.css`
- Optional only if truly needed: `app/services/html_sanitizer.py`, `tests/test_routes_media_and_images.py`

## Implementation Tasks

1. Add targeted `.article-body iframe` layout rules in `app/static/css/main.css`.
2. Preserve the existing article media outline, radius, dark-mode outline behavior, and max-width behavior.
3. Give YouTube embeds a stable 16:9 frame with `height: auto` and a dark media background.
4. Give same-origin DCInside movie iframes a portrait-friendly default frame, centered in the article column, without exceeding mobile width. Select the sanitizer output, especially `/movie?no=...`; use a selector that still works if query parameters include board/pid/kind.
5. Give poll iframes enough minimum height to be usable. Avoid making content unreachable when the source sets restrictive scrolling attributes; if CSS alone cannot fully fix remote poll internals, note the remaining limitation.
6. Set iframe display and margin so embedded media sits as a block in article flow instead of inline baseline content.
7. Keep keyboard focus visible on iframes. Browser verification showed Tab focus on cross-origin iframes may match `:focus-within` rather than `:focus-visible`, so include an iframe focus rule that works for actual keyboard Tab entry and is not hidden by the dark-mode decorative outline.
8. Keep images and native `<video>` behavior unchanged unless a shared selector must be split for correctness.
9. Keep the design system constraints from `DESIGN.md`: token-based colors, no shadows, no decorative cards.

## Constraints

- Scope the change to default `/read` styling. Do not redesign the page.
- Do not change Flask routes or scraping behavior unless CSS alone cannot fix the issue.
- Do not broaden the sanitizer allowlist unnecessarily.
- Avoid JavaScript for sizing unless CSS cannot represent the layout safely.
- Do not use raw hex in new non-token CSS. Prefer an existing dark surface token such as `--fg`/`--surface-strong`, or add a token only if needed.
- Make the purpose-specific iframe selectors appear after the general article iframe rule so the aspect-ratio declarations win.
- Preserve the actual 9:16 movie iframe box ratio even on short landscape viewports. Avoid clamping height in a way that leaves `width: 360px` but caps height below the ratio-derived value. Prefer reducing width based on viewport height if a height cap is needed.

## Lightweight Checks

- Inspect CSS selectors so YouTube, DCInside movie, and poll iframes each receive the intended sizing.
- Run the relevant test subset if Python dependencies are available.
- If a local browser check is practical, render a small test page or route containing sample iframes and verify the frames are not vertically collapsed.
- Verify focus-visible styling is not hidden by the media outline.
- Verify 390x500 and similar landscape mobile viewports keep movie iframe ratio intact without horizontal overflow.
