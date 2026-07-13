# Fable Handoff - 임베드 카드 폴리시와 링크 미리보기 (ux-first-fable)

토폴로지: Sol(gpt-5.6-sol/high) UX 플랜 → Fable 주체가 프론트엔드 직접 구현 +
백엔드는 Sol 워커 위임 → Sol(high) 통합 보안 리뷰 1회 → 수정 반영.

## 계약 요약

- DOM: `figure.embed-card.embed-card-twitter`(`.embed-card-head` > `.embed-card-label`
  + `.embed-card-source`), `a.link-preview`(`.link-preview-title/desc/host`),
  맨몸 링크 마킹 `a.link-preview-target`, 투표 `div.dc-poll-card` 계열(기존 DOM).
- API: `GET /embed/link-preview?url=` → 400(형식/비https) | 503+no-store(예산·동시
  상한) | 200 `{"ok":false}`+max-age=300 | 200 `{"ok":true,title,description,
  site_name,host}`+max-age=86400. 썸네일 필드 없음(제품 결정).
- X resize: `twttr.private.resize` `params[0].height`, origin
  `https://platform.twitter.com` + `event.source` 검증(실측 확인).

## Sol 보안 리뷰 반영 기록

- 블로커: DNS 재바인딩 TOCTOU → media_proxy의 pinned adapter 재사용으로 hop별
  IP 고정. / 예산 실효성 → 예산 획득을 DNS보다 선행, 전역 동시 실행 상한(기본 4),
  전체 deadline(기본 8s)로 슬로우 전송 차단.
- 중요: http 링크의 조회 슬롯 소진 → link_preview.js가 https만 선정. /
  X 타임아웃 시 숨은 iframe 포커스 트랩 → `visibility: hidden`으로 탭 진입 차단.
- 사소: 실패 캐시 TTL 300s 정합, `MIRROR_LINK_PREVIEW_*` 환경변수 README·
  .env.example 문서화(워커 프로세스별 한도 명시).
- 수용된 한계: 예산은 워커 프로세스별(기존 /embed/youtube-size와 동일 전례).

상세 UX 계약은 `docs/ux-flow.md`의 "Current Task Addendum 2" 참고.

---

# (이전 작업 기록) Fable Handoff - 읽기 본문 임베드 리빌드 (실제 비율 기반)

## Repo and Framework

- Flask 앱, Jinja 템플릿, 순수 CSS/JavaScript. 빌드 도구 없음.
- 읽기 화면은 `app/templates/read.html` + `app/static/css/main.css`가 렌더링한다.
- 본문 HTML은 `app/services/html_sanitizer.py`의 `prepare_read_html()`을 거쳐
  `<article class="article-body">` 안에 들어간다.
- DCInside 동영상 iframe은 sanitizer가 같은 출처 `/movie?no=...&board=...&pid=...`로
  재작성한다. `/movie` 라우트는 `app/services/media_proxy.py`의 `build_movie_response()`가
  `<video>` 하나를 담은 HTML(`movie_html()`) 또는 502 오류 HTML(`movie_error_html()`)을 내려준다.
- 응답에 CSP 헤더가 없으므로 플레이어 HTML 안 인라인 스크립트가 동작한다.

## User Report

- 사용자가 "임베디드 UX/UI 등 문제가 있는 것 같은데 다시 만들자"고 했다.
- 직전 개선(커밋 0ff1fce)이 DC 동영상 iframe을 무조건 9:16 세로로 강제해 새 문제를 만들었다.

## Verified Findings (실측)

- `/read?board=baseball_new11&pid=20969362&recommend=1`: DC 동영상 임베드가 332x590
  세로 박스로 강제된다. 실제 콘텐츠(삭제 안내 포함)는 가로형이라 거대한 검은 박스
  가운데 얇은 띠로만 보인다. 스크린샷: `output/playwright/embed-before-dcmovie.png`.
- `/read?board=dcbest&pid=445296`: YouTube 임베드는 16:9(772x434)로 정상.
- `/read?board=dcbest&pid=445293`: 소유자가 임베드를 차단한 YouTube 영상은
  "동영상을 재생할 수 없음"을 YouTube가 자체 렌더링한다. 프레임 크기는 정상.
- 목적별 셀렉터(youtube/movie/poll)에 매칭되지 않는 iframe은
  `height: auto` 때문에 브라우저 기본 150px로 납작해진다.

## Accepted UX Design

1. 기본 프레임: 모든 `.article-body iframe`은 block + 16:9 기본 비율. 납작한 150px 금지.
2. YouTube: 16:9 유지, 다크 미디어 배경 유지.
3. DC 동영상(`/movie`):
   - 플레이어가 아래 Interaction Contract대로 메타데이터/에러를 보고하고, 읽기 화면
     스크립트가 아래 크기 적용 규칙대로 iframe을 조정한다.
   - JS 미동작 폴백과 메타데이터 수신 전 기본값은 16:9다.
   - 502 오류 HTML에도 같은 error 신호 스크립트(요청 응답 포함)를 넣는다.
   - 크로스 오리진 폴백 src(`gall.dcinside.com/board/movie/movie_view`,
     `m.dcinside.com/movie/player`)는 postMessage가 닿지 않으므로 전용 규칙을 만들지
     않고 기본 16:9로 흡수한다. 기존 9:16 규칙과 그 셀렉터 목록(라이트/다크 배경 포함)은
     완전히 제거하고, 다크모드 배경 목록과 포커스 링 특이성 구조는 유지한다(B3).
4. 투표(`/poll`): `min-height: 400px` + surface 배경 유지.
5. 크기 전환은 즉시 적용(트랜지션 없음). 초점 링(`:focus-visible`/`:focus-within`) 유지.

## Interaction Contract (postMessage)

Fable 플랜 리뷰 블로커(B1, B2)를 반영한 핸드셰이크형 계약이다.

- 자식 → 부모: `{ type: "mirror:movie-meta", width: <number>, height: <number> }`
  또는 `{ type: "mirror:movie-meta", error: true }`.
- 부모 → 자식: `{ type: "mirror:movie-meta-request" }`.
- 자식(플레이어) 동작:
  - `window.parent === window`(직접 열람)이면 아무것도 하지 않는다(top-level 가드).
  - `<video>`는 `<source>` 자식 대신 `src` 속성을 직접 쓴다. `<source>` 구조에서는
    로드 실패 시 error 이벤트가 source 요소에서만 발생해 video 리스너가 영영 발화하지
    않기 때문이다(B1). `preload="metadata"`를 명시한다(B2, iOS 데이터 절약 대응).
  - `loadedmetadata` 시 실제 `videoWidth/videoHeight`를 보고한다. 둘 중 하나라도 0이면
    error로 취급한다.
  - video `error` 이벤트 시 error를 보고한다.
  - 마지막 상태를 기억해 두고, 부모의 `mirror:movie-meta-request`를 받으면 재전송한다
    (리스너 등록 전 메시지 유실 복구, B2).
  - `targetOrigin`은 `window.location.origin`(같은 출처).
- 부모(읽기 화면) 동작:
  - 후보를 `.article-body iframe[src^="/movie"]`로 제한한다.
  - 리스너 등록 직후와 각 후보 iframe의 `load` 이벤트 시점에
    `mirror:movie-meta-request`를 해당 `contentWindow`로 보낸다.
  - 수신 검증: `event.origin === window.location.origin`, `type` 일치,
    `event.source`가 후보 iframe의 `contentWindow`와 일치(null 가드 포함).
  - width/height는 유한한 양수만 신뢰하고 비율(w/h)은 0.2 ~ 5.0으로 클램프한다.
  - 크기 메시지는 첫 유효 값만 적용하되, error는 나중에 와도 덮어쓸 수 있다.
  - 그 외 형태의 메시지는 무시한다. JSON.parse 없이 구조화 데이터만 쓴다.

## 크기 적용 규칙 (부모 JS)

- 가로/정사각(w >= h): 인라인 스타일 `aspect-ratio: W / H`, 폭은 컬럼 100%.
- 세로(w < h): `aspect-ratio: W / H`,
  `width: min(100%, 360px, calc(82vh * R))` (R = w/h, B4 수식 확정), 중앙 정렬.
- error: `is-embed-error` 클래스 부여 — `height: 144px`, `aspect-ratio: auto`,
  배경 `--surface`. 부모가 iframe `title`을 "동영상을 불러오지 못했습니다"로 갱신한다.
- 크기 전환은 즉시 적용(트랜지션 없음).

## Files to Edit (write scope)

- `app/services/media_proxy.py`: `movie_html()`, `movie_error_html()`에 인라인 신호 스크립트.
- `app/static/javascript/embed_resizer.js`: 신규. 메시지 수신과 비율 적용.
  (후속 작업에서 movie_embed_resizer.js에서 개명, 유튜브 비율 조회 추가)
- `app/templates/read.html`: 새 스크립트 include 한 줄.
- `app/static/css/main.css`: `.article-body iframe` 관련 규칙과 다크모드 대응 재작성.
- `tests/test_routes_media_and_images.py`: movie HTML 신호 스크립트 회귀 테스트.

금지: 위 외 파일, sanitizer 허용 목록 확대, 라우트/스크래핑 변경, Git 조작.

## Constraints

- `DESIGN.md` 준수: 토큰 색만 사용(raw hex 금지, 단 `/movie` 플레이어 HTML 내부는
  독립 문서라 기존 `#05070b` 유지 가능), 그림자/장식 카드 금지.
- `movie_html()`/`movie_error_html()`은 f-string이므로 인라인 스크립트의 중괄호는
  전부 `{{ }}`로 이스케이프해야 한다.
- 이미지와 네이티브 `<video>` 규칙은 유지한다. 공유 셀렉터 분리는 필요할 때만.
- 목적별 셀렉터는 일반 iframe 규칙 뒤에 둔다.
- JS는 의존성 없이 기존 파일들과 같은 IIFE 스타일로 작성한다.
- 다크모드에서 초점 링이 outline-color 재정의에 묻히지 않게 기존 특이성 구조를 유지한다.
- `/movie` 응답은 `public, max-age=86400`으로 캐시되므로 검증 시 캐시를 우회한다.
  max-age 정책 변경은 이번 스코프가 아니다(구버전 캐시는 16:9 폴백으로 열화 수용).
- 리사이즈로 인한 스크롤 보정(scrollTop 조정)은 이번 스코프가 아니다. lazy 로딩으로
  리사이즈가 뷰포트 근처에서 일어나고 16:9→실비율 델타가 작아 수용한다.

## Acceptance Checks

- 라이브 DC 동영상: `/read?board=leagueoflegends6&pid=14382531&recommend=1`
  (movie no=6818615, 실제 mp4) — 임베드가 실제 영상 비율 프레임으로 보인다.
- 삭제 DC 동영상: `/read?board=baseball_new11&pid=20969362&recommend=1`
  (DC가 placeholder PNG를 mp4 source로 내려줌 → video error 경로) — 144px 오류 박스로
  줄어들고 title이 갱신된다.
- YouTube 정상: `/read?board=dcbest&pid=445296` — 16:9 유지.
- YouTube 임베드 차단: `/read?board=dcbest&pid=445293` — 16:9 프레임 유지(오류 UI는
  YouTube가 렌더링).
- 미분류 iframe: DOM에 임의 same-origin iframe을 주입해 150px로 납작해지지 않고
  16:9 기본 프레임을 받는지 확인한다.
- JS 비활성: 스크립트 차단 상태에서 DC 동영상 임베드가 16:9 폴백으로 보인다.
- 390px/1280px에서 가로 스크롤 없음.
- Tab 키로 iframe 진입 시 초점 링이 보인다(양쪽 테마).
- `make test` 통과.
