# DESIGN.md - 숨터 기본 프론트엔드 디자인 시스템

적용 범위: 루트 기본 화면(`/`, `/board`, `/read`, `/recent`)과 `app/templates/`, `app/static/`.

## 1. Atmosphere / Signature

숨터는 장식보다 읽기 속도를 우선하는 고밀도 게시판 미러다. 820px 단일 컬럼, 굵은 상단 잉크 바, 얇은 괘선, 절제된 파란 활성 상태가 화면의 서명이다. 카드와 그림자는 쓰지 않고, 행의 밀도와 여백 변화로 현재 위치와 조작 가능성을 구분한다.

Design Read: 읽기 전용 게시판 미러를 자주 쓰는 사용자를 위한 고밀도 독서 화면. 차분한 신문식 목록과 선명한 조작부를 섞는다.

- `DESIGN_VARIANCE`: 4/10
- `MOTION_INTENSITY`: 2/10
- `VISUAL_DENSITY`: 8/10

## 2. Color

모든 색은 CSS 변수로만 사용한다. 컴포넌트에 raw hex를 직접 쓰지 않는다.

### Light

| Token | Value | Role |
|---|---|---|
| `--bg` | `#FFFFFF` | 기본 배경 |
| `--fg` | `#191F28` | 제목과 본문 텍스트 |
| `--muted` | `#6B7684` | 보조 텍스트, 메타 |
| `--line` | `#E5E8EB` | 기본 괘선 |
| `--line-strong` | `#C9D1DA` | 강조 괘선 |
| `--hover` | `#F2F4F6` | 행 호버 |
| `--surface` | `#F9FAFB` | 입력과 패널 배경 |
| `--surface-strong` | `#F2F4F6` | 눌림, 비활성 면 |
| `--accent` | `#3182F6` | 링크, 활성 탭, 포커스 |
| `--accent-strong` | `#1D64D8` | 강조 링크 호버 |
| `--accent-wash` | `rgba(49, 130, 246, 0.09)` | 하이라이트 배경 |
| `--on-accent` | `#FFFFFF` | 악센트 위 텍스트 |
| `--hot` | `#F04452` | 개념글 강조 |
| `--hot-video` | `#F04452` | 동영상 개념글 강조 |
| `--role-manager` | `#FF6B00` | 관리자 이름 |
| `--role-submanager` | `#3182F6` | 부매니저 이름 |

### Dark

| Token | Value | Role |
|---|---|---|
| `--bg` | `#18181B` | 기본 배경 |
| `--fg` | `#E4E4E7` | 제목과 본문 텍스트 |
| `--muted` | `#A1A1AA` | 보조 텍스트 |
| `--line` | `rgba(255, 255, 255, 0.08)` | 기본 괘선 |
| `--line-strong` | `rgba(255, 255, 255, 0.18)` | 강조 괘선 |
| `--hover` | `rgba(255, 255, 255, 0.045)` | 행 호버 |
| `--surface` | `#1F1F23` | 입력과 패널 배경 |
| `--surface-strong` | `#27272D` | 눌림, 비활성 면 |
| `--accent` | `#3B82F6` | 링크, 활성 탭, 포커스 |
| `--accent-strong` | `#60A5FA` | 강조 링크 호버 |
| `--accent-wash` | `rgba(59, 130, 246, 0.12)` | 하이라이트 배경 |
| `--on-accent` | `#FFFFFF` | 악센트 위 텍스트 |
| `--hot` | `#FB923C` | 개념글 강조 |
| `--hot-video` | `#EC4899` | 동영상 개념글 강조 |
| `--role-manager` | `#FB923C` | 관리자 이름 |
| `--role-submanager` | `#38BDF8` | 부매니저 이름 |

Do: 파란색은 링크, 활성 상태, 포커스, 상위 순위에만 쓴다.
Don't: 보라 그라디언트, 베이지와 황동 팔레트, 순수 검정 본문, 그림자 깊이 표현 금지.

## 3. Typography

스택: `"SUIT Variable", "SUIT", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif`.
의도: 좁고 단단한 한글 그로테스크. 모든 숫자 카운트는 `font-variant-numeric: tabular-nums`.

| Role | Token | Spec |
|---|---|---|
| 마스트헤드 | `--type-masthead` | 23px 모바일 / 26px 데스크톱, 800, 1.08 |
| 글 제목 | `--type-display` | 25px 모바일 / 30px 데스크톱, 760, 1.28 |
| 섹션 제목 | `--type-title` | 17px, 750, 1.28 |
| 피드 제목 | `--type-feed` | 15.5px, 620, 1.42 |
| 본문 | `--type-body` | 16.5px, 400, 1.78 |
| 레이블과 버튼 | `--type-label` | 13px, 650, 1.2 |
| 검색 입력 | `--type-input` | 14px, 400, 1.4 |
| 댓글 본문 | `--type-comment` | 14px, 400, 1.65 |
| 메타 | `--type-meta` | 12.5px, 520, 1.4 |

## 4. Spacing

베이스 유닛은 4px. 모든 여백은 4의 배수만 허용한다. 괘선 1px과 굵은 괘선 2px은 예외다.

`--space-1: 4px`, `--space-2: 8px`, `--space-3: 12px`, `--space-4: 16px`, `--space-5: 20px`, `--space-6: 24px`, `--space-8: 32px`, `--space-10: 40px`, `--space-12: 48px`, `--space-16: 64px`, `--space-20: 80px`.

읽기 컬럼 폭은 최대 820px, 본문 콘텐츠는 같은 컬럼 안에서 흐른다. 모바일 좌우 패딩은 `--space-4`, 태블릿 이상은 `--space-6`.

## 5. Components

- **마스트헤드**: 4px 상단 잉크 바, 브랜드 표식, 브랜드 이름, 테마 전환, 탭을 한 덩어리로 묶는다. 배경은 `--bg`, 하단 괘선은 `--line`.
- **브랜드 표식**: 실제 `favicon.svg`와 같은 도형을 28px 정사각 인라인 SVG로 사용한다. 판은 `--fg`, 글자는 `--bg`, 점은 `--accent`를 따라 테마와 함께 바뀐다. 장식용 가짜 화면은 쓰지 않는다.
- **테마 전환**: 32px 정사각 버튼. 현재 테마의 반대 아이콘은 `html[data-theme]` 기반 CSS가 그리고, JS는 레이블만 갱신한다. 첫 페인트에서 상태를 거짓으로 말하지 않는다.
- **탭**: 13px 레이블, 활성은 `--fg`와 2px `--accent` 밑줄. hover는 `--fg`, focus는 2px `--accent`.
- **검색 패널**: `--surface`, 1px `--line`, radius `--radius-m`. 입력 focus는 `--accent`. 입력은 `--type-input`, 제출 버튼도 최소 높이 40px 규칙을 따른다.
- **피드 행**: 12px/8px 패딩, 1px 괘선. hover는 `--hover`, 좌측 2px `--accent` 마커는 opacity만 전환한다.
- **배지와 순위**: radius `--radius-s`, 괘선 기반. 상위 순위와 hot 배지는 `--accent` 또는 `--hot`.
- **버튼과 페이저**: 최소 높이 40px, 1px `--line`, radius `--radius-s`, hover는 `--hover`와 `--fg` 괘선.
- **읽기 화면**: 제목 아래 메타 바, 본문은 24px 위아래 여백. 이미지와 영상은 최대 너비 100%, radius `--radius-xs`.
- **댓글**: 행 기반 목록. 답글은 왼쪽 2px `--line`과 20px 들여쓰기.
- **상단 이동 버튼**: 40px 정사각, `--surface`, 1px `--line`, opacity/transform만 전환한다.

Radius: `--radius-xs: 4px`, `--radius-s: 6px`, `--radius-m: 8px`.
괘선: `--rule: 1px`, `--rule-bold: 2px`, 마스트헤드 잉크 바 4px.

## 6. Motion

`--dur-fast: 120ms`, `--dur: 160ms`, easing `ease-out`. 전환은 color, background-color, border-color, opacity, transform, filter에 한정한다. `prefers-reduced-motion: reduce`에서는 전환과 애니메이션을 제거한다.

## 7. Depth

깊이 전략은 괘선과 톤 변화만 사용한다. `box-shadow`는 금지한다.
엘리베이션 사다리: 배경 `--bg`, 패널 `--surface`, 강조 면 `--surface-strong`.
