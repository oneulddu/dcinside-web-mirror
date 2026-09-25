# DESIGN.md - 숨터 기본 프론트엔드 디자인 시스템

적용 범위: 루트 기본 화면(`/`, `/board`, `/read`, `/recent`)과 `app/templates/`, `app/static/`.

## 1. Atmosphere / Signature

숨터는 원본 사이트의 흔적을 지운 차분한 단색 읽기 화면이다. 토스 앱처럼 흰(또는 어두운) 바탕에 굵은 제목,
회색 입력칸과 회색 띠, 얇은 구분선만으로 화면을 나눈다. 카드 안의 카드, 알약 모양 칩, 장식 그라디언트는 쓰지 않는다.
파란색은 링크·활성 상태·상위 1~3위·댓글 수에만, 빨간 불꽃은 추천글에만 쓴다.

- `DESIGN_VARIANCE`: 3/10
- `MOTION_INTENSITY`: 2/10
- `VISUAL_DENSITY`: 7/10 (글 한 줄 약 64px, 기존 목록 밀도 유지)

## 2. Color

모든 색은 CSS 변수로만 사용한다. 컴포넌트에 raw hex를 직접 쓰지 않는다.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#191B20` | 기본 배경 |
| `--fg` | `#191F28` | `#F5F6F8` | 제목, 강조 텍스트 |
| `--text` | `#333D4B` | `#E3E5E8` | 본문, 목록 제목 |
| `--muted` | `#6B7684` | `#9097A1` | 보조 텍스트, 아이콘 |
| `--meta` | `#8B95A1` | `#8D929C` | 작성자·시간 같은 메타 |
| `--faint` | `#8B95A1` | `#6E747E` | 순위 4위 이하, 비활성 |
| `--read` | `#A0A9B4` | `#6B707A` | 읽은 글 제목 |
| `--line` | `#E5E8EB` | `#2A2D34` | 구분선 |
| `--surface` | `#F2F4F6` | `#2B2E35` | 입력칸, 회색 버튼, 타일 |
| `--surface-strong` | `#E5E8EB` | `#363A42` | 눌림·호버 면 |
| `--band` | `#F2F4F6` | `#0E0F12` | 섹션을 나누는 회색 띠 |
| `--elevated` | `#FFFFFF` | `#2B2E35` | 떠 있는 메뉴, 맨 위로 버튼 |
| `--accent` | `#3182F6` | `#4C94FA` | 링크, 활성, 포커스, 상위 순위, 댓글 수 |
| `--hot` / `--hot-video` | `#F04452` / `#DB2777` | `#F04452` / `#EC4899` | 추천글 불꽃(사진 / 동영상) |
| `--role-manager` / `--role-submanager` | `#FF6B00` / `#3182F6` | `#FB923C` / `#38BDF8` | 매니저 이름 |

Do: 섹션 구분은 회색 띠(`.section-band`)와 1px 구분선으로만 한다.
Don't: 게시판마다 다른 색 타일, 배지 테두리, 보라 그라디언트, 카드형 목록.

## 3. Typography

스택: `"Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif`.
폰트는 `main.css` 안에서 글자 범위별 `@font-face`(`font-display: swap`)로 선언해 필요한 조각만 받는다. 외부 CSS 링크는 쓰지 않는다.
본문 자간 `-0.02em`, 제목 `-0.03~-0.035em`. 숫자 카운트는 `tabular-nums`.

| Role | Token | Spec |
|---|---|---|
| 워드마크 | `--type-masthead` | 20px, 800 |
| 글 제목 | `--type-display` | 24px 모바일 / 28px 데스크톱, 700, 1.38 |
| 게시판·페이지 제목 | `--type-page-title` | 24px, 700 |
| 섹션 제목 | `--type-title` | 20px, 700 |
| 홈 순위 행 | `--type-row` | 17px, 600 |
| 목록 제목 | `--type-feed` | 15.5px, 600, 1.42 |
| 본문 | `--type-body` | 16.5px, 400, 1.75 |
| 탭 | `--type-tab` | 15px, 600 |
| 레이블·버튼 | `--type-label` | 14px, 600 |
| 입력 | `--type-input` | 16px, 500 (iOS 확대 방지) |
| 댓글 | `--type-comment` | 15.5px, 400, 1.55 |
| 메타 | `--type-meta` | 12.5px, 500 |

## 4. Spacing / Layout

베이스 유닛 4px. 좌우 여백 `--gutter`는 모바일 20px, 768px 이상 24px.
읽기 컬럼은 최대 820px 가운데 단일 컬럼. 회색 띠와 탭 구분선만 화면 끝까지 칠한다.

## 5. Components

- **머리**: 홈은 `숨터` 워드마크 + `베스트` 글자 링크 + 이미지 차단 + 테마. 하위 화면은 뒤로 가기(글 보기는 게시판 이름 포함) + [게시판 검색 아이콘] + 이미지 차단 + 테마. 모든 아이콘 버튼은 44×44px, 테두리 없음.
- **이미지 차단 메뉴**: 떠 있는 둥근 메뉴(radius 18px, `--shadow-float`). 선택 항목은 파란 글자와 오른쪽 체크.
- **검색칸**: `--surface` 면, radius 14px, 높이 48px, 포커스 시 안쪽 2px 파란 테두리. 홈 검색 버튼은 입력이 있을 때만 보인다(Enter는 항상 동작).
- **탭**: 글자 탭, 활성은 `--fg` 글자와 2px `--fg` 밑줄. 분류는 밑줄 없는 가로 스크롤 글자 줄, 활성은 굵게.
- **글 행**: 구분선으로만 나눈다. 아이콘·제목·댓글 수가 한 줄 글자처럼 이어지고, 메타는 `분류 · 작성자 · 시간 · 추천 N`.
- **최근 본 게시판 타일**: 58px 회색 둥근 사각형(radius 20px)에 첫 글자, 모두 같은 색. 4열(640px 이상 8열).
- **회색 버튼**: 접힌 댓글 보기, 이미지 보기, 목록으로. `--surface` 면, radius 12~14px.
- **댓글**: 답글은 26px 들여쓰기와 꺾인 화살표 아이콘. 배지 상자는 쓰지 않는다.
- **맨 위로**: 48px 원형, `--elevated` + `--shadow-float`.

Radius: `--radius-xs` 4px, `--radius-s` 8px, `--radius-m` 12px, `--radius-l` 14px, `--radius-xl` 18px.

## 6. Motion

`--dur-fast: 120ms`, `--dur: 160ms`, easing `ease-out`. 눌림은 `scale(0.96)`. 전환은 color, background-color, opacity, transform, filter, box-shadow에 한정한다.
`prefers-reduced-motion: reduce`에서는 전환·애니메이션·부드러운 스크롤을 제거한다.

## 7. Depth

기본 화면은 평면이다. 그림자는 화면 위에 떠 있는 요소(이미지 차단 메뉴, 맨 위로 버튼)에만 `--shadow-float`로 쓴다.
