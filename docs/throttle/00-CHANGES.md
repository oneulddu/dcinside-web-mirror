# 문서 개선 이력

## 2026-03-17: 초기 계획 문서 보완 + 코드 분석

### 변경 사항

**1. 문서 구조 개선**
- 430줄 단일 문서 → 5개 모듈형 문서로 분리
- 각 문서는 독립적으로 읽을 수 있도록 구성

**2. 코드 분석 결과 반영**

**핵심 발견:**
- `dc_api.py`의 `__fetch_parsed_from_urls()` (line 228)가 이미 모든 HTML 스크래핑의 공통 진입점
- 여기 한 곳만 수정하면 `board()`, `document()`, `comments()` 등 모든 메서드에 자동 적용
- 기존 계획보다 훨씬 단순하게 구현 가능

**현재 코드 구조:**
- `app/routes.py`: `_fetch_heung_galleries()` (line 86), `_run_async()` 패턴 확인
- `app/services/dc_api.py`: `aiohttp.ClientSession` 기반, 공통 진입점 존재
- `app/services/core.py`: `_fill_missing_author_code()` (line 138)는 캐시 우선 확인

**3. 구현 전략 수정**

**기존 계획:**
- 새로운 래퍼 메서드 추가
- 기존 메서드를 하나씩 래퍼로 전환
- 점진적 적용

**수정된 계��:**
- `__fetch_parsed_from_urls()` 한 곳만 수정
- POST 요청 2곳 추가 (선택)
- 즉시 전체 적용 (점진 불필요)

**변경 범위 최소화:**
- 신규 코드: ~200줄 (throttle 모듈 + 테스트)
- 기존 코드 수정: ~30줄 (3-4곳만)

**4. 추가된 내용**

**엣지 케이스:**
- 타임아웃과의 상호작용 (blocked_until vs HTTP_TIMEOUT)
- `_run_async()` 패턴과 throttle 호환성
- 캐시 히트 시 throttle 스킵 로직 (자동 처리됨)
- 리다이렉트 처리 (queue에 추가하여 재시도)
- 여러 URL 시도 패턴 (각 시도마다 throttle 대기)

**설정값 재검토:**
- `MAX_CONCURRENCY`: 1 → 2 권장 (응답 시간 개선)
- 근거: 단일 요청이 여러 업스트림 호출로 분해되는 구조

**관측성:**
- 필수 로깅을 1차 구현에 포함
- debug/info/warning 레벨 구분

**롤백 시나리오:**
- `MIRROR_UPSTREAM_THROTTLE_ENABLED=false` 시 동작 명확화
- 성능 오버헤드: < 1μs (함수 호출 + 조건 분기만)

**테스트 전략:**
- pytest 도입 필수 (1차 PR에 포함)
- 단위/통합/수동 검증 가이드

**캐시 효과:**
- `_AUTHOR_CODE_CACHE`: TTL 600초로 가장 효과적
- 실제 api 호출 빈도는 예상보다 낮을 수 있음
- throttle 효과 측정 시 캐시 워밍업 필요

**5. 문서 구성**

| 문서 | 내용 | 줄 수 |
|---|---|---:|
| README.md | 인덱스, 빠른 시작, 코드 변경 범위 | 90 |
| 01-OVERVIEW.md | 목적, 문제, 해결 방향 | 60 |
| 02-DESIGN.md | 아키텍처, 설정, 동작 원리 | 180 |
| 03-IMPLEMENTATION.md | 단계별 작업, 코드 예시 | 250 |
| 04-OPERATIONS.md | 배포, 모니터링, 트러블슈팅 | 120 |
| 05-CODE-ANALYSIS.md | 코드 분석 결과 및 시사점 | 150 |

**6. 기존 문서 처리**

`dcinside-web-mirror-request-throttle-plan.md`:
- 상단에 최신 문서 링크 추가
- 빠른 참조 섹션 추가
- 원본 내용은 "아카이브" 섹션으로 보존

### 다음 단계

구현을 시작하려면:
1. `docs/throttle/05-CODE-ANALYSIS.md` 읽기 (코드 분석 결과)
2. `docs/throttle/03-IMPLEMENTATION.md` 읽기 (구현 가이드)
3. Phase 1부터 순차 진행
4. 각 Phase마다 별도 PR 생성 권장

