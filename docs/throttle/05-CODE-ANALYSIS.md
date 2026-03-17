# 코드 분석 결과 및 구현 시사점

## 주요 발견 사항

### 1. `dc_api.py`의 공통 진입점 존재

**발견:** `__fetch_parsed_from_urls(urls)` (line 228)가 이미 모든 HTML 스크래핑의 공통 진입점으로 사용 중

**호출 체인:**
```
board() → __build_list_urls() → __fetch_parsed_from_urls()
document() → __build_view_urls() → __fetch_parsed_from_urls()
comments() → (직접) → __fetch_parsed_from_urls()
```

**시사점:**
- 새로운 래퍼를 만들 필요 없음
- `__fetch_parsed_from_urls()` 한 곳만 수정하면 모든 GET 요청에 throttle 적용
- 기존 코드 구조를 최대한 보존 가능

### 2. POST 요청은 별도 처리 필요

**발견:** POST 요청은 공통 진입점을 거치지 않음

**대상 메서드:**
- `__gallery_miner_from_web()` (line 265): 갤러리 검색
- `__access()` (line 1067): 쓰기/삭제/수정 계열

**시사점:**
- 이 2개 메서드에도 `wait_for_turn_async()` 추가 필요
- 하지만 쓰기 계열은 빈도가 낮아 우선순위 낮음

### 3. `core.py`의 `_fill_missing_author_code()` 구조

**발견:** `_fill_missing_author_code()` (line 138)는 이미 캐시를 먼저 확인함

```python
async def _fill_missing_author_code(api, board, kind, row):
    if row.get("author_code"):
        return row  # 이미 있으면 스킵

    cache_key = (board, kind or "", str(doc_id))
    cached = _cache_get(_AUTHOR_CODE_CACHE, cache_key)
    if cached is not None:
        # 캐시 히트 - api 호출 안 함
        return row

    # 캐시 미스 - api.document() 호출
    doc = await api.document(board_id=board, document_id=doc_id, kind=kind)
```

**시사점:**
- 캐시 히트 시 throttle을 타지 않음 (api 호출 자체가 없음)
- budget 제어는 `_fill_missing_author_code()` 호출 전에 해야 함
- 캐시 TTL이 600초로 길어서 실제 api 호출 빈도는 낮을 수 있음

### 4. `routes.py`의 sync 호출 위치

**발견:** `requests.get()` 직접 호출은 2곳

1. `_fetch_heung_galleries()` (line 86-88):
```python
def _fetch_heung_galleries():
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get("https://gall.dcinside.com/", headers=headers, timeout=HTTP_TIMEOUT)
```

2. `/media` 프록시 (확인 필요):
```python
@bp.route("/media")
def media_proxy():
    # requests.get() 사용 가능성
```

**시사점:**
- 흥한 갤러리는 파일 캐시(TTL 3600초)가 있어 실제 호출 빈도 낮음
- `/media`는 이미지 프록시라 throttle 적용 시 체감 속도 저하 가능

### 5. API 클래스 초기화

**발견:** `API.__init__()` (line 145)에서 session 생성 시 이미 기본 헤더/쿠키 설정

```python
def __init__(self):
    self.session = aiohttp.ClientSession(
        headers=GET_HEADERS,
        cookies={"_ga": "GA1.2.693521455.1588839880"}
    )
```

**시사점:**
- throttle 모듈 초기화는 `API` 인스턴스 생성과 무관하게 모듈 import 시점에 해야 함
- `upstream_throttle.init_from_env()`는 모듈 레벨에서 자동 실행

## 구현 전략 수정

### 기존 계획
1. 새로운 래퍼 메서드 추가
2. 기존 메서드를 하나씩 래퍼로 전환
3. 점진적 적용

### 수정된 계획
1. `__fetch_parsed_from_urls()` 한 곳만 수정
2. POST 요청 2곳 추가
3. 즉시 전체 적용 (점진 불필요)

**장점:**
- 코드 변경 최소화 (3곳)
- 테스트 범위 명확
- 롤백 용이

## 추가 고려사항

### 1. 리다이렉트 처리

`__fetch_parsed_from_urls()`는 리다이렉트를 감지하면 queue에 추가하여 재시도:

```python
redirect_match = re.search(r"location\\.href\\s*=\\s*'([^']+)'", text)
if redirect_match:
    redirect_url = redirect_match.group(1).strip()
    if redirect_url and redirect_url not in queue:
        queue.append(redirect_url)  # 재시도
    continue
```

**시사점:**
- 리다이렉트 시 추가 throttle 대기 발생
- 하지만 같은 요청 컨텍스트 내이므로 문제 없음

### 2. 여러 URL 시도 패턴

`__build_list_urls()`는 여러 URL을 생성하여 순차 시도:

```python
urls = [
    "https://m.dcinside.com/board/{}?page={}",
    "https://gall.dcinside.com/board/lists/?id={}&page={}",
    "https://gall.dcinside.com/mgallery/board/lists/?id={}&page={}",
    # ...
]
```

**시사점:**
- 첫 URL이 실패하면 다음 URL 시도
- 각 시도마다 throttle 대기 발생
- 실패가 많으면 응답 시간 증가 가능

### 3. 캐시 효과

현재 캐시 TTL:
- `_LATEST_ID_CACHE`: 20초
- `_RELATED_CACHE`: 90초
- `_AUTHOR_CODE_CACHE`: 600초

**시사점:**
- author_code 캐시가 가장 효과적
- 실제 api 호출 빈도는 예상보다 낮을 수 있음
- throttle 효과 측정 시 캐시 워밍업 필요
