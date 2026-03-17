# DCinside Web Mirror 업스트림 Throttle - 설계

## 아키텍처

### A. 새 모듈: `app/services/upstream_throttle.py`

업스트림 요청 제어를 한 곳에 모은다.

**책임:**
- 환경변수 읽기
- 프로세스 로컬 throttle 상태 보관
- 요청 시작 간 최소 간격(min interval) 적용
- jitter(랜덤 소폭 지연) 적용
- 레이트리밋 감지 시 `blocked_until` 갱신
- async/sync 양쪽 재사용 가능한 대기 함수 제공

**내부 구조:**
```python
class ThrottleConfig:
    enabled: bool
    min_interval_ms: int
    max_concurrency: int
    jitter_ms: int
    rate_limit_backoff_ms: int
    rate_limit_max_backoff_ms: int
    log_events: bool

class ThrottleState:
    next_request_at_monotonic: float
    blocked_until_monotonic: float
    consecutive_rate_limits: int
    last_reason: str
    lock: threading.Lock

def wait_for_turn_sync() -> None
def wait_for_turn_async() -> None
def register_rate_limit(status: int, text: str, headers: dict) -> None
def is_rate_limited_response(status: int, text: str, headers: dict) -> bool
```

**동기/비동기 공용 상태:**
- 상태 보호: `threading.Lock`
- sync 대기: `time.sleep(...)`
- async 대기: `await asyncio.sleep(...)`

이 방식으로 같은 프로세스 안에서 `requests`와 `aiohttp` 호출이 **같은 페이싱 규칙을 공유**한다.

### B. `dc_api.py` 공통 래퍼

**중요 발견:** `API` 클래스는 `__fetch_parsed_from_urls(urls)` (line 228)라는 공통 메서드를 이미 사용 중이다. 이 메서드가 **모든 HTML 스크래핑의 진입점**이다.

**현재 호출 구조:**
- `board()` → `__fetch_parsed_from_urls(__build_list_urls(...))`
- `document()` → `__fetch_parsed_from_urls(__build_view_urls(...))`
- `comments()` → `__fetch_parsed_from_urls(...)`
- 기타 메서드들도 동일

**따라서 래핑 전략을 단순화할 수 있다:**

```python
async def __fetch_parsed_from_urls(self, urls):
    """이미 존재하는 공통 메서드 - 여기에만 throttle 추가하면 됨"""
    await wait_for_turn_async()  # 추가

    queue = list(urls)
    idx = 0
    while idx < len(queue):
        url = queue[idx]
        idx += 1
        try:
            async with self.session.get(url) as res:
                # 레이트리밋 감지 추가
                if res.status == 429:
                    register_rate_limit()
                    raise RuntimeError(f"Rate limited: {res.status}")

                if res.status >= 400:
                    continue
                text = await res.text()

                # 응답 본문 검사
                if is_rate_limited_response(res.status, text[:1000], res.headers):
                    register_rate_limit()
                    raise RuntimeError("Rate limited")

            # ... 기존 로직
```

**추가로 래핑이 필요한 곳:**
- `__gallery_miner_from_web()` (line 265): POST 요청
- `__access()` (line 1067): 쓰기/삭제 계열

**레이트리밋 감지 기준:**
1. HTTP status `429`
2. 응답 본문에 포함:
   - `Too Many Requests`
   - `너무 많은 요청`
   - `penalty-box`
3. `Retry-After` 헤더가 있으면 backoff에 반영

**왜 "요청 시작 시점 간격" 방식인가:**
- 구현 단순
- burst 억제에 직관적
- 한 요청이 내부적으로 여러 개로 찢어질 때 제어 쉬움

### C. `routes.py` sync 호출 연동

**대상:**
- `_fetch_heung_galleries()` (line 86-88)
- `_search_galleries()` (검색 기능)
- `/media` (선택 적용)

**처리:**
```python
def _fetch_heung_galleries():
    wait_for_turn_sync()  # 추가
    res = requests.get("https://gall.dcinside.com/", ...)
```

**`/media` 프록시 처리:**
- 이미지 체감 속도에 직접 영향
- 1차는 기본 제외 (`MIRROR_MEDIA_THROTTLE_ENABLED=false`)
- 또는 아주 느슨한 별도 한도 적용

### D. Secondary Fetch Budget

가장 큰 burst 원인: `author_code` 보강을 위한 추가 `document(...)` 호출

**정책:**
- 게시판 목록(`/board`): author_code 보강 요청 수를 페이지당 제한
- 관련 게시글(`/read/related`): 별도 제한
- 예산 소진 시: **작성자명만 표시, author_code는 비워둠**

**권장 예산:**
- 목록 페이지: 최대 6~8건
- 관련글: 최대 3~4건
- rate-limit 상태: 0건

**필요한 변경:**
- `core.async_index(...)`: `author_code_budget` 인자 추가
- `_fill_missing_author_code(...)` 호출 전 budget 차감
- `async_related_by_position(...)`: 동일 정책

## 환경변수 설정

### 필수 설정

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `MIRROR_UPSTREAM_THROTTLE_ENABLED` | `true` | 업스트림 요청 페이싱 사용 여부 |
| `MIRROR_UPSTREAM_MIN_INTERVAL_MS` | `150` | 요청 시작 간 최소 간격 (ms) |
| `MIRROR_UPSTREAM_MAX_CONCURRENCY` | `2` | 프로세스 내 동시 업스트림 요청 수 |
| `MIRROR_UPSTREAM_JITTER_MS` | `50` | 요청 간 랜덤 지터 (ms) |
| `MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS` | `5000` | rate limit 감지 시 기본 휴지 시간 (ms) |
| `MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS` | `15000` | 연속 rate limit 시 최대 휴지 시간 (ms) |

### 부가 최적화 설정

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE` | `8` | `/board`에서 author code 추가 조회 허용 수 |
| `MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_RELATED_LOAD` | `4` | `/read/related`에서 author code 추가 조회 허용 수 |
| `MIRROR_MEDIA_THROTTLE_ENABLED` | `false` | `/media` 경로에 throttle 적용 여부 |
| `MIRROR_UPSTREAM_LOG_EVENTS` | `false` | throttle/backoff 이벤트 로그 출력 |

**참고:** `MAX_CONCURRENCY=1`은 너무 보수적일 수 있음. 단일 요청이 여러 업스트림 호출로 분해되는 구조에서는 응답 시간이 선형 증가. `MIN_INTERVAL_MS`를 적절히 설정하고 concurrency는 2-3 권장.

## 동작 원리

### 정상 상태
- 요청은 150ms 간격으로 시작
- 프로세스 내 동시 업스트림 요청은 2개까지 허용
- 소량 jitter로 기계적 패턴 회피

### Rate Limit 감지 상태
- 즉시 `blocked_until = now + 5000ms`
- 연속 발생 시: 5s → 10s → 15s 점증
- 이 상태에서는 secondary fetch(author code 보강) 생략
- 핵심 요청만 통과

### 캐시와의 관계
기존 `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE`는 유지. 새 throttle은 캐시를 대체하는 것이 아니라 **캐시 미스 시 burst를 제어하는 안전장치**.

**캐시 히트 시 throttle 스킵:**
```python
# core.py 예시
cached = _cache_get(_LATEST_ID_CACHE, cache_key)
if cached:
    return cached  # throttle 타지 않음

# 캐시 미스 시에만 throttle 적용
result = await api.get_gallery_posts(...)  # 여기서 throttle
```

## 엣지 케이스

### 1) 타임아웃과의 상호작용
```python
# throttle 대기 중 timeout 발생 가능
# blocked_until이 HTTP_TIMEOUT(20s)보다 길면?
# → 최대 backoff를 15s로 제한하여 완화
```

### 2) `_run_async()` 패턴과 throttle
Flask sync route에서 `_run_async(coro)`를 통해 async 함수 호출:
```python
# routes.py
def board_view():
    result = _run_async(async_index(...))  # 내부에서 throttle 적용
```

`wait_for_turn_async()`는 `asyncio.sleep()`을 사용하므로 `_run_async()` 내부에서 정상 동작.

### 3) 동시성 모델
- `threading.Lock`으로 상태 보호
- async 코드에서 lock 획득 시 blocking이지만, 대기 시간이 짧아 문제 없음
- 필요 시 `asyncio.Lock`으로 분리 가능 (2차 개선)

### 4) 롤백 시나리오
`MIRROR_UPSTREAM_THROTTLE_ENABLED=false` 시:
```python
def wait_for_turn_sync():
    if not config.enabled:
        return  # 즉시 반환, 오버헤드 최소

async def wait_for_turn_async():
    if not config.enabled:
        return
```

성능 오버헤드: 함수 호출 + 조건 분기만 (< 1μs)

## 다중 워커 환경 제약

이 설계는 **프로세스 로컬** 기준이다.

예: Gunicorn worker 3개 → 각 worker가 자기 throttle state를 따로 보유

따라서 전체 인스턴스 기준 완전한 글로벌 한도는 아니다. 이 한계는 문서에 명시 필요.

**2차 확장:** Redis 기반 shared state로 여러 worker/인스턴스 간 통합 한도 구현 가능.
