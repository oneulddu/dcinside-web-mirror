# DESIGN.md — 숨터 v2 프론트엔드 디자인 시스템

적용 범위: `/v2` 프론트엔드 (`app/templates/v2/`, `app/static/v2/`).
기존 v1 프론트엔드(`app/templates/*.html`, `app/static/css/main.css`)는 이 계약의 대상이 아니다.

## 1. Atmosphere / Signature

만년필 잉크와 인주 도장. 종이 신문의 괘선(하이라인) 위에 놓인 고밀도 한글 조판.
그림자는 0, 깊이는 오직 괘선과 톤 변화로만 만든다. 상단의 굵은 잉크 바(4px)와
마스트헤드가 페이지의 서명이고, 인주색(vermilion) 악센트는 숫자와 활성 상태에만
찍는다. 화면은 720px 단일 읽기 컬럼, 카드 없이 행과 괘선으로만 리듬을 만든다.

## 2. Color

모든 색은 CSS 변수로만 사용한다. 컴포넌트에 raw hex 금지.

### Light (기본 `data-theme="light"`)

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#FCFCF9` | 페이지 배경 (따뜻한 백지) |
| `--fg` | `#191A16` | 본문 잉크 (순흑 아님) |
| `--muted` | `#6D6F66` | 보조 텍스트, 메타 (bg 대비 4.9:1) |
| `--line` | `#E3E2DA` | 괘선 (hairline) |
| `--hover` | `#F1F0E8` | 행 호버 틴트 |
| `--surface` | `#F4F3EC` | 패널 배경 (검색 폼 등) |
| `--accent` | `#BC3F26` | 인주(도장) 악센트 (bg 대비 5.9:1) |
| `--accent-wash` | `#F7E7E2` | 악센트 워시 (검색 하이라이트 배경) |
| `--on-accent` | `#FFFFFF` | 악센트 위 텍스트 |

### Dark (`data-theme="dark"`)

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#16171A` | 페이지 배경 (먹색) |
| `--fg` | `#E8E7E1` | 본문 (순백 아님) |
| `--muted` | `#9A9B93` | 보조 텍스트 (bg 대비 5.6:1) |
| `--line` | `#2C2D31` | 괘선 |
| `--hover` | `#1E1F23` | 행 호버 틴트 |
| `--surface` | `#1C1D21` | 패널 배경 |
| `--accent` | `#E2694E` | 인주 악센트 (다크 보정, bg 대비 5.5:1) |
| `--accent-wash` | `#3A241E` | 악센트 워시 |
| `--on-accent` | `#16171A` | 악센트 위 텍스트 |

Do: 악센트는 페이지당 한 계열(인주색)만, 숫자·활성 탭·개념글 불꽃에만 사용.
Don't: 파랑/보라 CTA 추가 금지, 그라디언트 금지, 순수 `#000000`/`#FFF` 본문 금지
(on-accent 전용 백색 제외).

## 3. Typography

스택: `"SUIT Variable", "SUIT", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif`
(CDN: jsdelivr sun-typeface/SUIT variable). 의도: Pretendard(v1)와 구별되는 좁고 단단한
한글 그로테스크. 모든 카운트 숫자는 `font-variant-numeric: tabular-nums`.

| Role | Token | Spec |
|---|---|---|
| 마스트헤드 | `--type-masthead` | 22px(모바일) / 24px(768px 이상) · 800 · 1.1 · 0 |
| 글 제목(read) | `--type-display` | 26px(모바일) / 28px(768px 이상) · 750 · 1.3 · 0 |
| 섹션/게시판 제목 | `--type-title` | 17px · 700 · 1.3 |
| 피드 행 제목 | `--type-feed` | 15.5px · 600 · 1.45 |
| 본문(article) | `--type-body` | 16.5px · 400 · 1.75 |
| 레이블/버튼 | `--type-label` | 13px · 600 · 1.2 |
| 메타 | `--type-meta` | 12.5px · 500 · 1.4 |

## 4. Spacing

베이스 유닛 base 4px. 모든 margin/padding/gap은 4의 배수만 허용 (0, 1px 괘선 제외).

`--space-1: 4px` · `--space-2: 8px` · `--space-3: 12px` · `--space-4: 16px` ·
`--space-5: 20px` · `--space-6: 24px` · `--space-8: 32px` · `--space-10: 40px` ·
`--space-12: 48px` · `--space-16: 64px`

읽기 컬럼 폭 720px, 좌우 패딩 `--space-4`(모바일) / `--space-6`(768px 이상).

## 5. Components

- **마스트헤드**: 페이지 최상단 4px `--fg` 잉크 바 + 브랜드(masthead 램프) + 테마 토글.
  아래 1px `--line` 괘선.
- **탭 (`.top-tabs`)**: 13px/600, `--muted`; 활성은 `--fg` 텍스트 + 2px `--accent` 밑줄.
  hover: `--fg`. focus-visible: 2px `--accent` 아웃라인.
- **말머리 스크롤 (`.board-category-scroll`)**: 가로 오버플로 시 얇은 스크롤바 노출
  (`scrollbar-width: thin`, thumb `--line`, 높이 `--rule-bold`). 숨기지 않는다 — 잘린
  탭이 더 있다는 신호를 남긴다.
- **피드 행 (`.feed-item`)**: 세로 패딩 `--space-3`, 행 사이 1px `--line`. 제목
  `--type-feed`; 댓글 수 `.reply-count`는 `--accent` tabular-nums 12.5px. 메타 행은
  `--type-meta` `--muted`. hover: `--hover` 배경. 읽음(`.is-read`) 제목은 `--muted`.
  개념글 불꽃 아이콘 `--accent`, 사진/영상 아이콘 `--muted`.
- **검색 폼/패널**: 1px `--line` 테두리, `--surface` 배경, radius `--radius-m`.
  입력 focus: 테두리 `--accent`. 제출 버튼: `--accent` 텍스트, 1px 테두리.
- **버튼 (`.pager-btn`, `.related-more-btn`)**: 1px `--line` 테두리, radius `--radius-s`,
  패딩 8px 16px, `--type-label`. hover: `--hover` 배경 + `--fg` 테두리. disabled/off:
  `--muted` 텍스트, hover 없음. loading: 펄스(opacity) + "불러오는 중" 레이블.
- **댓글**: 괘선 리스트, 답글은 들여쓰기 `--space-5` + `.reply-badge`(악센트 테두리 텍스트).
  디시콘 이미지 max-height 120px, radius `--radius-xs`.
- **검색 하이라이트 (`mark.search-highlight`)**: `--accent-wash` 배경, `--fg` 텍스트.
- **크럼 링크 (`.crumb-link`)**: 읽기 화면 상단 목록 복귀. meta 램프, `--muted`,
  hover `--accent`.
- **행 호버 마커 (`.feed-item::before`)**: 좌측 `--rule-bold` 폭 `--accent` 세로 바,
  opacity 전이만 사용 (레이아웃 불변).
- **맨 위로 (`.back-to-top`)**: 40px(`--space-10`) 정사각, `--surface` 배경 + 1px `--line`,
  radius `--radius-m`. 스크롤 480px 초과 시 표시(opacity/transform 전이).
  hover: `--hover` 배경 + `--fg` 테두리.
- **스킵 링크 (`.skip-link`)**: focus-visible에서만 표시, `--bg` 배경 + 1px `--fg` 테두리.
- **다크 모드 이미지 감광**: 본문 이미지/디시콘에 `brightness(0.85)` 필터 (다크 전용).

Radius 스케일: `--radius-xs: 4px` · `--radius-s: 6px` · `--radius-m: 10px`. 이 셋 외 금지.

괘선 두께: `--rule: 1px`(hairline) · `--rule-bold: 2px`(활성 밑줄, 답글 들여쓰기 괘선,
화살표 스트로크) · 마스트헤드 잉크 바 4px. 이 셋 외 금지.

## 6. Motion

`--dur-fast: 120ms` · `--dur: 160ms`, easing `ease-out`. GPU 합성 속성만
(opacity/transform/filter) + color/background 전이. 레이아웃 속성 애니메이션 금지.
로딩 펄스는 opacity 키프레임. `prefers-reduced-motion: reduce`에서 전이·애니메이션 제거.

## 7. Depth

전략: **괘선 + 톤 변화 단일 전략. box-shadow 금지.**
엘리베이션 사다리: 평면(배경) → 행 호버(`--hover` 틴트) → 패널(`--surface` + 1px `--line`).
이 세 단계 외의 깊이 표현 금지.
