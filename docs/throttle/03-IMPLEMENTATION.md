# DCinside Web Mirror 업스트림 Throttle - 구현 가이드

## 작업 범위

### 신규 파일
- `app/services/upstream_throttle.py`
- `tests/test_upstream_throttle.py` (pytest 도입 필요)

### 수정 파일
- `app/services/dc_api.py`
- `app/services/core.py`
- `app/routes.py`
- `app/config.py`
- `.env.example`
- `README.md`

## 단계별 작업

### Phase 1: Throttle 모듈 추가 (독립 검증)

**목표:** 실제 연동 없이 throttle 모듈만 추가하고 단위 테스트로 검증

**1-1. `app/services/upstream_throttle.py` 작성**

```python
import os
import time
import random
import asyncio
import threading
from dataclasses import dataclass

@dataclass
class ThrottleConfig:
    enabled: bool
    min_interval_ms: int
    max_concurrency: int
    jitter_ms: int
    rate_limit_backoff_ms: int
    rate_limit_max_backoff_ms: int
    log_events: bool

class ThrottleState:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request_at = 0.0
        self.blocked_until = 0.0
        self.consecutive_rate_limits = 0
        self.active_requests = 0

_config = None
_state = None

def init_from_env():
    global _config, _state
    _config = ThrottleConfig(
        enabled=os.getenv("MIRROR_UPSTREAM_THROTTLE_ENABLED", "true").lower() == "true",
        min_interval_ms=int(os.getenv("MIRROR_UPSTREAM_MIN_INTERVAL_MS", "150")),
        max_concurrency=int(os.getenv("MIRROR_UPSTREAM_MAX_CONCURRENCY", "2")),
        jitter_ms=int(os.getenv("MIRROR_UPSTREAM_JITTER_MS", "50")),
        rate_limit_backoff_ms=int(os.getenv("MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS", "5000")),
        rate_limit_max_backoff_ms=int(os.getenv("MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS", "15000")),
        log_events=os.getenv("MIRROR_UPSTREAM_LOG_EVENTS", "false").lower() == "true",
    )
    _state = ThrottleState()

def wait_for_turn_sync():
    if not _config.enabled:
        return

    with _state.lock:
        now = time.monotonic()

        # Check blocked state
        if _state.blocked_until > now:
            wait_time = _state.blocked_until - now
            if _config.log_events:
                print(f"[throttle] blocked, waiting {wait_time:.2f}s")
        else:
            wait_time = max(0, _state.next_request_at - now)

        if wait_time > 0:
            time.sleep(wait_time)

        # Apply jitter
        if _config.jitter_ms > 0:
            jitter = random.uniform(0, _config.jitter_ms / 1000.0)
            time.sleep(jitter)

        # Update next request time
        _state.next_request_at = time.monotonic() + (_config.min_interval_ms / 1000.0)

async def wait_for_turn_async():
    if not _config.enabled:
        return

    with _state.lock:
        now = time.monotonic()

        if _state.blocked_until > now:
            wait_time = _state.blocked_until - now
        else:
            wait_time = max(0, _state.next_request_at - now)

        jitter = random.uniform(0, _config.jitter_ms / 1000.0) if _config.jitter_ms > 0 else 0
        total_wait = wait_time + jitter

        _state.next_request_at = time.monotonic() + total_wait + (_config.min_interval_ms / 1000.0)

    if total_wait > 0:
        await asyncio.sleep(total_wait)

def register_rate_limit(retry_after_seconds=None):
    if not _config.enabled:
        return

    with _state.lock:
        _state.consecutive_rate_limits += 1

        if retry_after_seconds:
            backoff = min(retry_after_seconds * 1000, _config.rate_limit_max_backoff_ms)
        else:
            backoff = min(
                _config.rate_limit_backoff_ms * _state.consecutive_rate_limits,
                _config.rate_limit_max_backoff_ms
            )

        _state.blocked_until = time.monotonic() + (backoff / 1000.0)

        if _config.log_events:
            print(f"[throttle] rate limit detected, backoff {backoff}ms")

def is_rate_limited_response(status, text, headers):
    if status == 429:
        return True

    if text and any(phrase in text for phrase in ["Too Many Requests", "너무 많은 요청", "penalty-box"]):
        return True

    return False

def clear_rate_limit_state():
    """Called after successful request"""
    if not _config.enabled:
        return

    with _state.lock:
        _state.consecutive_rate_limits = 0
        _state.blocked_until = 0.0

# Initialize on import
init_from_env()
```

**1-2. pytest 설정**

`requirements.txt`에 추가:
```
pytest>=7.0.0
pytest-asyncio>=0.21.0
```

**1-3. `tests/test_upstream_throttle.py` 작성**

```python
import time
import asyncio
import pytest
from app.services import upstream_throttle

def test_throttle_disabled():
    upstream_throttle._config.enabled = False
    start = time.monotonic()
    upstream_throttle.wait_for_turn_sync()
    upstream_throttle.wait_for_turn_sync()
    elapsed = time.monotonic() - start
    assert elapsed < 0.01  # Should be instant

@pytest.mark.asyncio
async def test_throttle_min_interval():
    upstream_throttle._config.enabled = True
    upstream_throttle._config.min_interval_ms = 100
    upstream_throttle._config.jitter_ms = 0
    upstream_throttle._state.next_request_at = 0
    upstream_throttle._state.blocked_until = 0

    start = time.monotonic()
    await upstream_throttle.wait_for_turn_async()
    await upstream_throttle.wait_for_turn_async()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.1  # At least 100ms interval

def test_rate_limit_backoff():
    upstream_throttle._config.enabled = True
    upstream_throttle._config.rate_limit_backoff_ms = 1000
    upstream_throttle._state.consecutive_rate_limits = 0

    upstream_throttle.register_rate_limit()

    assert upstream_throttle._state.blocked_until > time.monotonic()
    assert upstream_throttle._state.consecutive_rate_limits == 1
```

**1-4. 검증**
```bash
make install  # pytest 설치
pytest tests/test_upstream_throttle.py -v
```

---

### Phase 2: `dc_api.py` 공통 래퍼 도입

**목표:** 기존 `__fetch_parsed_from_urls()` 메서드에만 throttle 추가 (최소 침습)

**중요:** `dc_api.py`는 이미 `__fetch_parsed_from_urls()` (line 228)를 공통 진입점으로 사용 중. 여기에만 throttle을 추가하면 `board()`, `document()`, `comments()` 등 모든 메서드가 자동으로 적용됨.

**2-1. `dc_api.py` 상단에 import 추가**

```python
# app/services/dc_api.py 상단
from . import upstream_throttle
```

**2-2. `__fetch_parsed_from_urls()` 수정 (line 228)**

```python
async def __fetch_parsed_from_urls(self, urls):
    await upstream_throttle.wait_for_turn_async()  # 추가

    queue = list(urls)
    idx = 0
    while idx < len(queue):
        url = queue[idx]
        idx += 1
        try:
            async with self.session.get(url) as res:
                # Rate limit 감지 추가
                if res.status == 429:
                    retry_after = res.headers.get("Retry-After")
                    upstream_throttle.register_rate_limit(
                        int(retry_after) if retry_after and retry_after.isdigit() else None
                    )
                    continue

                if res.status >= 400:
                    continue
                text = await res.text()

            if not text:
                continue

            # 응답 본문 검사 (앞 1KB만)
            if upstream_throttle.is_rate_limited_response(res.status, text[:1000], res.headers):
                upstream_throttle.register_rate_limit()
                continue

            # 성공 시 초기화
            upstream_throttle.clear_rate_limit_state()

            # 기존 로직 (리다이렉트 등)
            redirect_match = re.search(r"location\\.href\\s*=\\s*'([^']+)'", text)
            if redirect_match:
                redirect_url = redirect_match.group(1).strip()
                if redirect_url and redirect_url not in queue:
                    queue.append(redirect_url)
                continue
            parsed = lxml.html.fromstring(text)
            return parsed, text, url
        except Exception:
            continue
    return None, "", None
```

**2-3. POST 요청 래핑 (선택)**

```python
# __gallery_miner_from_web() (line 265)
async def __gallery_miner_from_web(self, category, category_code, name=None):
    await upstream_throttle.wait_for_turn_async()  # 추가
    # ... 기존 로직

# __access() (line 1067)
async def __access(self, token_verify, target_url, require_conkey=True, csrf_token=None):
    await upstream_throttle.wait_for_turn_async()  # 추가
    # ... 기존 로직
```

**장점:**
- 기존 코드 최소 수정 (3곳만)
- 모든 메서드가 자동 적용
- 테스트 범위 최소화


---

### Phase 3: `routes.py` sync 호출 연동

**3-1. `_fetch_heung_galleries()` 수정**

```python
# app/routes.py
from .services import upstream_throttle

def _fetch_heung_galleries():
    upstream_throttle.wait_for_turn_sync()  # 추가

    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get("https://gall.dcinside.com/", headers=headers, timeout=HTTP_TIMEOUT)
    # ... 나머지 로직
```

**3-2. 검색 기능도 동일 적용**

```python
def _search_galleries(query):
    upstream_throttle.wait_for_turn_sync()  # 추가
    # ... 기존 로직
```

**3-3. `/media` 프록시는 1차 제외**

```python
@bp.route("/media")
def media_proxy():
    # throttle 적용 안 함 (MIRROR_MEDIA_THROTTLE_ENABLED=false 기본값)
    # 필요 시 2차에서 추가
```

---

### Phase 4: Secondary Fetch Budget

**4-1. `core.py`에 budget 파라미터 추가**

```python
# app/services/core.py
import os

AUTHOR_CODE_BUDGET_PER_BOARD_PAGE = int(os.getenv("MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE", "8"))
AUTHOR_CODE_BUDGET_PER_RELATED_LOAD = int(os.getenv("MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_RELATED_LOAD", "4"))

async def async_index(board_id, page=1, author_code_budget=None):
    if author_code_budget is None:
        author_code_budget = AUTHOR_CODE_BUDGET_PER_BOARD_PAGE

    # ... 기존 로직

    # _fill_missing_author_code 호출 전
    if author_code_budget > 0:
        await _fill_missing_author_code(board_id, item)
        author_code_budget -= 1
    # else: skip
```

**4-2. rate-limit 상태 시 budget 0으로 설정**

```python
async def async_index(board_id, page=1, author_code_budget=None):
    # Check if currently rate limited
    if upstream_throttle._state.blocked_until > time.monotonic():
        author_code_budget = 0  # Skip all secondary fetches
    elif author_code_budget is None:
        author_code_budget = AUTHOR_CODE_BUDGET_PER_BOARD_PAGE
```

---

## 구현 시 주의사항

### 1) monotonic clock 사용
```python
# ❌ 잘못된 예
now = time.time()

# ✅ 올바른 예
now = time.monotonic()
```

### 2) HTML body 검사 비용
- HTML/JSON 응답: 문자열 검사 가능
- binary/media 응답: status 중심 검사 권장
- 큰 응답은 앞 1KB만 검사

### 3) 너무 큰 지연 방지
- 기본 간격: 150ms (작게 유지)
- rate limit 시만 강한 backoff (5s~15s)
- 최대 backoff를 15s로 제한하여 timeout(20s)보다 짧게 유지

### 4) 로깅 레벨
```python
if _config.log_events:
    # debug: throttle wait 시작/종료
    # info: backoff 진입/해제
    # warning: rate limit 감지
```

---

## 테스트 전략

### 단위 테스트 (pytest)
- throttle 모듈 독립 테스트
- 간격 적용 확인
- backoff 동작 확인
- budget 차감 로직

### 수동 검증
1. 단일 요청 시 정상 동작
2. 연속 요청 시 간격 적용 확인 (로그)
3. 429 mock 응답 시 backoff 진입 확인
4. author_code budget 초과 시 graceful degradation

### 통합 테스트 (선택)
```bash
# 개발 서버 실행
MIRROR_UPSTREAM_LOG_EVENTS=true make run

# 브라우저에서 게시판 목록 여러 번 새로고침
# 로그에서 throttle 동작 확인
```

---

## 권장 PR 분리

### PR #1: Throttle 모듈 + 테스트
- `app/services/upstream_throttle.py`
- `tests/test_upstream_throttle.py`
- `requirements.txt` (pytest 추가)
- 아직 실제 연동 안 함

### PR #2: dc_api.py 래퍼 도입
- `dc_api.py` 공통 래퍼
- `get_gallery_posts`, `get_document` 2개만 적용
- `.env.example` 기본 설정 추가

### PR #3: routes.py + budget
- `routes.py` sync 호출 연동
- `core.py` author_code budget
- README 문서화

이렇게 나누면 위험도가 높은 변경을 한 번에 몰아넣지 않는다.
