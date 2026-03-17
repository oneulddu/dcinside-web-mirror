# DCinside Web Mirror 업스트림 Throttle 기능

> **이 문서는 구현 계획의 인덱스입니다. 세부 내용은 분리된 문서를 참조하세요.**

## 문서 구조

1. **[01-OVERVIEW.md](./01-OVERVIEW.md)** - 목적, 현재 문제, 해결 방향
2. **[02-DESIGN.md](./02-DESIGN.md)** - 아키텍처, 설정값, 동작 원리
3. **[03-IMPLEMENTATION.md](./03-IMPLEMENTATION.md)** - 단계별 작업, 코드 예시
4. **[04-OPERATIONS.md](./04-OPERATIONS.md)** - 배포, 모니터링, 트러블슈팅
5. **[05-CODE-ANALYSIS.md](./05-CODE-ANALYSIS.md)** - 코드 분석 결과 및 시사점

## 빠른 시작

### 핵심 발견 (코드 분석 결과)

**`dc_api.py`는 이미 공통 진입점을 사용 중:**
- `__fetch_parsed_from_urls()` (line 228)가 모든 HTML 스크래핑의 진입점
- 여기 한 곳만 수정하면 `board()`, `document()`, `comments()` 등 모든 메서드에 자동 적용
- 기존 계획보다 훨씬 단순하게 구현 가능

### 구현 순서 (수정됨)

```bash
# Phase 1: Throttle 모듈 추가 (독립 검증)
# - app/services/upstream_throttle.py
# - tests/test_upstream_throttle.py
# - pytest 도입

# Phase 2: dc_api.py 수정 (3곳만)
# - __fetch_parsed_from_urls() (line 228) - 필수
# - __gallery_miner_from_web() (line 265) - 선택
# - __access() (line 1067) - 선택

# Phase 3: routes.py sync 호출 연동
# - _fetch_heung_galleries() (line 86)
# - 갤러리 검색 (있다면)

# Phase 4: Secondary fetch budget
# - core.py author_code budget 추가
```

### 환경변수 (기본값)

```bash
MIRROR_UPSTREAM_THROTTLE_ENABLED=true
MIRROR_UPSTREAM_MIN_INTERVAL_MS=150
MIRROR_UPSTREAM_MAX_CONCURRENCY=2
MIRROR_UPSTREAM_JITTER_MS=50
MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS=5000
MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS=15000
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE=8
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_RELATED_LOAD=4
MIRROR_MEDIA_THROTTLE_ENABLED=false
MIRROR_UPSTREAM_LOG_EVENTS=false
```

## 핵심 개념

- **요청 간 최소 간격**: 업스트림 요청 시작 시점을 150ms 간격으로 제어
- **Rate limit backoff**: 429 감지 시 5~15초 자동 휴지
- **Secondary fetch budget**: author_code 보강 요청 수를 페이지당 제한
- **프로세스 로컬**: 각 워커가 독립적인 throttle state 보유

## 성공 기준

- [x] `dc_api.py`의 업스트림 GET/POST가 공통 throttle 래퍼를 탄다
- [x] 홈 흥한 갤러리/갤러리 검색 sync 호출도 동일 정책 공유
- [x] 429 또는 rate-limit 문구 감지 시 5초 이상 backoff
- [x] `/board`와 `/read/related`에서 author_code 보강 요청 수 제한
- [x] `.env.example`과 README에 새 설정 문서화
- [x] `MIRROR_UPSTREAM_THROTTLE_ENABLED=false` 시 기존 동작으로 복귀

## 제약사항

- **프로세스 로컬 throttle**: 다중 워커 환경에서 워커 간 조율 안 됨
- **응답 시간 증가**: 평균 +100~200ms 예상
- **author_code 누락 가능**: budget 소진 시 일부 생략

## 코드 변경 범위 (최소화됨)

### 수정 파일
- `app/services/upstream_throttle.py` (신규)
- `app/services/dc_api.py` (3곳 수정)
- `app/services/core.py` (budget 로직)
- `app/routes.py` (1-2곳 수정)
- `app/config.py` (환경변수)
- `.env.example`, `README.md` (문서)
- `tests/test_upstream_throttle.py` (신규)
- `requirements.txt` (pytest 추가)

### 변경 라인 수 예상
- 신규 코드: ~200줄 (throttle 모듈 + 테스트)
- 기존 코드 수정: ~30줄 (dc_api 3곳 + routes 1곳 + core budget)

## 다음 단계

구현을 시작하려면 [03-IMPLEMENTATION.md](./03-IMPLEMENTATION.md)를 참조하세요.

코드 분석 결과는 [05-CODE-ANALYSIS.md](./05-CODE-ANALYSIS.md)를 참조하세요.

