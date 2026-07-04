# 최적화 작업 문서 (2026-07)

> 2026-07-04 코드베이스 분석 기준. `docs/codex-optimization-tasks.md`(2026-06)의 전 항목이 구현 완료된 상태에서, 현재 코드를 다시 훑어 남은 최적화 지점을 정리한 후속 문서다.
> 각 작업은 독립적인 PR 단위로 진행하고, 작업마다 `make test`(pytest) 통과를 확인한다.
> 우선순위: P1(효과 큼, 저위험) → P2(구조/측정 필요) → P3(소소한 개선)

## 이전 문서 후속 항목 처리 현황

- **공유 ClientSession 쿠키 누적** (2026-06 문서의 미조치 항목): 해결됨. `app/services/dc/api.py`의 `__prune_session_cookies()`가 매 요청 후 `DC_SESSION_COOKIE_ALLOWLIST` 외 쿠키를 제거한다.

## 공통 제약 (모든 작업에 적용)

기존 문서의 제약을 그대로 계승한다.

- `/media` 프록시의 SSRF 방어를 약화시키지 않는다. `app/services/media_proxy.py`의 `is_public_hostname()`이 **허용(공개 IP) 결과를 캐시하지 않는 것은 의도된 동작**(DNS 리바인딩 방어)이므로 바꾸지 않는다.
- 공개 라우트 URL(`/`, `/board`, `/read`, `/media`, `/movie`, `/read/related`, `/board/times`, `/recent`)과 쿼리 파라미터 형식은 바꾸지 않는다.
- 최근 방문 쿠키(`recent_galleries`) 형식은 기존 사용자 쿠키와 호환을 유지한다.
- HTML 새니타이저(`app/services/html_sanitizer.py`)의 허용 태그/속성 목록은 변경하지 않는다 (XSS 방어 영역).
- DCinside 요청 빈도를 늘리는 방향의 변경은 금지 (`docs/dcinside-rate-limit-usage.md` 참고: 리스트뷰 약 40회/분, 게시글 약 30회/분 추정).

---

## P1: 원본 요청 절감 (rate limit 예산 직결)

### P1-1. `/board` 뷰에 목록 캐시 적용

**배경.** `MIRROR_BOARD_PAGE_CACHE_TTL`(기본 20초) 캐시(`core.py`의 `_BOARD_PAGE_CACHE`)는 현재 **관련글 로딩 경로(`_fetch_board_page`)에서만** 사용된다. 정작 트래픽이 가장 많은 `/board` 라우트는 `async_index_with_head_categories()` → `api.board()`를 매 요청 직접 호출해서, 같은 갤러리 같은 페이지를 여러 사용자가 연달아 열어도 매번 DCinside 리스트뷰 요청이 나간다. 리스트뷰 제한(약 40회/분)을 가장 크게 소모하는 지점이다.

**변경.**

- `async_index_with_head_categories()` 결과(목록 + 말머리 카테고리)를 `(board, kind, recommend, page, fetch_num, scan_limit, upper_limit, lower_limit, search_type, search_keyword, head_id)` 키로 `_BOARD_PAGE_CACHE`와 같은 TTL 정책으로 캐시한다. `limit`, `max_scan_pages`, `document_id_upper_limit`, `document_id_lower_limit`가 결과를 바꾸므로 반드시 키에 포함한다.
- 말머리(`headtexts`)가 목록과 함께 반환되므로 캐시 값에 `(rows, categories)`를 같이 저장해야 한다. `_fetch_board_page`의 캐시와 키 형식을 맞추되, 저장 값 구조가 다르므로 별도 캐시 딕셔너리를 쓰는 편이 안전하다.
- 캐시 값은 저장/조회 시 복사(현행 `[dict(row) for row in cached]` 패턴)로 호출자 간 변조를 막는다. 행 딕셔너리와 카테고리 딕셔너리를 각각 새 객체로 만들어 반환한다.
- TTL은 기존 `MIRROR_BOARD_PAGE_CACHE_TTL`을 재사용한다 (신규 환경 변수 불필요).

**주의.**

- `_fill_missing_author_codes()`가 캐시된 행을 제자리(in-place) 수정하지 않도록 복사본에만 적용한다.
- 검색 결과·recommend 목록도 같은 캐시를 타므로 TTL 20초 내 최신성 저하는 허용 범위로 본다 (관련글 경로는 이미 동일 트레이드오프를 수용 중).

**검증.** 동일 `/board` 요청을 TTL 내 반복했을 때 `api.board()` 호출이 1회인지 테스트 추가. 서로 다른 `limit`/`max_scan_pages`/상하한 ID 요청이 같은 캐시를 공유하지 않는지도 확인한다. 기존 `tests/test_rate_limit_reductions.py` 패턴 참고.

### P1-2. 정적 자산 압축 활성화

**배경.** `app/__init__.py`의 `COMPRESS_MIMETYPES`가 `["text/html", "application/json"]`뿐이라 CSS/JS가 비압축으로 나간다. `main.css`는 28KB(gzip 시 5.5KB, 약 80% 절감), `read_related_loader.js`는 18KB다. 캐시 버스팅 + `immutable` 헤더 덕에 재방문은 문제없지만, 첫 방문·캐시 만료 시 전송량이 불필요하게 크다.

**변경.**

```python
app.config.setdefault("COMPRESS_MIMETYPES", [
    "text/html", "application/json", "text/css",
    "application/javascript", "text/javascript",
])
```

`/media`·`/movie` 제외 로직은 endpoint 기준이므로 그대로 유효하다.

**검증.** `Accept-Encoding: gzip`으로 정적 파일 요청 시 `Content-Encoding` 헤더 확인 테스트.

### P1-3. `/read` 문서 단기 캐시 (선택, 측정 후 결정)

**배경.** `/read`는 캐시가 전혀 없다. 매 조회가 문서 요청 1회(+임베디드 댓글이 모자라면 댓글 요청 1회)를 소모하며, 게시글 제한(약 30회/분)이 리스트뷰보다 빡빡하다. 인기글이 짧은 시간에 반복 조회되는 패턴이면 10~30초 TTL 문서 캐시로 원본 요청을 흡수할 수 있다.

**변경(안).**

- `async_read()` 결과 `(data, comments, images)`를 `(board, kind, pid, recommend, search_type, search_keyword, head_id)` 키로 짧은 TTL 캐시. 환경 변수 `MIRROR_READ_CACHE_TTL`(기본 0 = 비활성)로 도입해 운영에서 점진 적용.
- 댓글 최신성이 TTL만큼 늦어지는 트레이드오프가 있으므로 **기본값은 꺼둔 채로 배포**하고, 요청 로그(duration_ms + rate limit WARNING 빈도)로 필요성을 확인한 뒤 켠다.

**주의.** `data["html"]`은 라우트에서 `prepare_read_html()`로 매번 재가공되므로 캐시는 가공 전 원본 기준으로 한다 (`url_for` 등 요청 컨텍스트 의존 값 캐시 금지). 또한 라우트가 `data.pop("related_posts")`, `data["html"]` 대입, 댓글 `dccon` 프록시 URL 대입을 수행하므로 캐시 조회/저장 시 `data`, `comments`, `images`를 모두 복사해서 반환한다.

**검증.** TTL 내 동일 pid 반복 조회 시 `api.document()` 1회 호출 테스트. TTL 0일 때 기존 동작과 동일함을 확인한다. 캐시된 응답을 받은 뒤 라우트 수준 변형을 해도 다음 조회의 `related_posts`, 원본 `html`, 댓글 `dccon` 값이 오염되지 않는지도 확인한다.

---

## P2: 구조·리소스 효율

### P2-1. 워커 수 재검토 (캐시 중복 완화)

**배경.** `gunicorn.conf.py` 기본값이 `workers = cpu*2+1`, `threads = 4`다. 모든 캐시(`_BOARD_PAGE_CACHE`, `_AUTHOR_CODE_CACHE`, heung 메모리 캐시, `_BOARD_KIND_CACHE` 등)가 프로세스 로컬이므로 워커가 N개면 캐시 적중률이 1/N로 희석되고, 같은 목록을 워커마다 따로 DCinside에 요청한다. 이 앱은 CPU 연산(파싱)보다 원본 대기 I/O가 지배적이라 워커를 늘려 얻는 이득이 작다.

**변경(안).**

- 코드 변경 없이 운영 환경 변수로 조정 가능: `MIRROR_WORKERS=2~4`, `MIRROR_THREADS=8~16` 수준으로 낮춰 캐시 공유도를 높인다. heung 캐시는 파일 캐시가 있어 영향이 작고, board/author 캐시가 주 수혜 대상.
- `gunicorn.conf.py`의 **기본값 자체를** `min(cpu*2+1, 4)`처럼 상한을 두는 방안도 검토 (배포 서버 코어 수가 크면 현행 기본값이 과도).
- Redis 등 외부 캐시 도입은 이 프로젝트 규모에서 과하므로 보류.

**검증.** 스테이징에서 워커 수 변경 전후로 요청 로그의 rate limit WARNING 빈도와 p95 duration_ms 비교.

### P2-2. 갤러리 검색 결과 캐시

**배경.** `heung.py`의 `search_galleries()`는 캐시가 없어 같은 검색어 반복 입력이 매번 DCinside 검색 요청을 발생시킨다. 홈 화면 검색이라 봇/새로고침에 취약하다.

**변경.** 검색어(정규화: strip + lower) 키로 짧은 TTL(예: 60초) 메모리 캐시 추가. `core.py`의 `_cache_get`/`_cache_set`와 같은 방식의 TTL + lock + max items 패턴을 `heung.py` 안에 두고, 최대 항목 수 제한(예: 256)을 포함한다. 모듈 경계를 흐리지 않도록 `core.py`의 private helper를 직접 import하지 않는다.

**검증.** TTL 내 동일 검색어 재호출 시 `requests.get` 1회 테스트.

### P2-3. BeautifulSoup 파서 통일 (`html.parser` → `lxml`)

**배경.** `html_sanitizer.py`는 `lxml` 파서를 쓰지만 `heung.py`(2곳: 흥갤 목록, 갤러리 검색)와 `media_proxy.py`의 `parse_movie_media()`는 아직 순수 파이썬 `html.parser`를 쓴다. DCinside 메인/검색 페이지는 수백 KB라 파싱 비용 차이가 체감된다.

**변경.** `BeautifulSoup(text, "html.parser")` → `BeautifulSoup(text, "lxml")`. `lxml`은 이미 런타임 의존성이다.

**주의.** 파서 차이로 셀렉터 결과가 달라질 수 있으므로, 흥갤 목록·검색·동영상 파싱 테스트를 실제 HTML 픽스처 기준으로 확인한다.

**검증.** `make test` + 흥갤/검색/movie 관련 기존 테스트 통과.

### P2-4. 문서 HTML 이중 파싱 계측 (조사 항목)

**배경.** 게시글 본문은 `dc/api.py`에서 lxml로 파싱된 뒤 문자열로 직렬화되고, 라우트에서 `prepare_read_html()`이 BeautifulSoup로 **다시** 파싱한다. 이미지가 많은 긴 글에서 CPU를 이중으로 쓴다. 다만 파서 통합은 새니타이저(XSS 방어 영역)를 건드리므로 리스크가 크다.

**변경(안).** 바로 통합하지 않는다. 먼저 대형 게시글 샘플로 `prepare_read_html()` 소요 시간을 계측하고, 전체 응답 시간에서 파싱이 유의미한 비중(예: >20ms)일 때만 별도 PR로 설계한다. 원본 대기 I/O가 지배적이면 이 항목은 종결 처리한다.

---

## P3: 소소한 개선

### P3-1. Pretendard 폰트 로딩 최적화

**배경.** `base.html`이 jsdelivr CDN에서 Pretendard CSS를 렌더 블로킹으로 로드하는데 `preconnect` 힌트가 없어 첫 방문 시 DNS+TLS 핸드셰이크만큼 첫 렌더가 늦어진다.

**변경.** `<head>` 최상단에 추가:

```html
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
```

셀프 호스팅(subset woff2)은 효과가 더 크지만 정적 파일 관리 부담이 있으므로 preconnect만 우선 적용하고 필요 시 후속 검토.

### P3-2. 정적 파일 요청 로그 제외

**배경.** `_init_request_logging()`이 정적 파일 요청까지 INFO로 남긴다. 페이지당 CSS/JS 여러 건이라 로그 볼륨 대부분이 정적 요청이고, PM2 로그 회전 부담과 실제 페이지 지표 파악을 방해한다.

**변경.** `request.endpoint == "static"`이면 로그를 건너뛴다 (또는 DEBUG 레벨로 강등).

**검증.** 기존 `tests/test_config.py`의 정적 요청 로그 기대값을 갱신한다. 정적 파일 요청은 INFO 로그가 남지 않고, 일반 라우트 요청은 path/status/duration 로그가 계속 남는지 확인한다.

### P3-3. `_cache_prune` 호출 비용 절감

**배경.** `core.py`의 `_cache_set()`이 매 저장마다 전체 키 순회 + 만료 스캔을 하고, 오버플로 시 전체 정렬을 한다. `_AUTHOR_CODE_CACHE`는 최대 8192개라 저장이 몰리면 락 보유 시간이 늘어난다.

**변경(안).** 저장 N회마다(예: 64회) 또는 마지막 prune 후 일정 시간 경과 시에만 prune을 수행하도록 카운터/타임스탬프 가드를 추가한다. 캐시 최대치가 수천 개 수준이라 효과는 작으므로 P3로 둔다.

### P3-4. `/board/times` 캐시 max_items 상수 정리

**배경.** `core.py`의 `async_board_precise_times()`가 `_BOARD_TIME_CACHE`에 저장할 때 max_items로 `BOARD_PAGE_CACHE_MAX_ITEMS`를 넘긴다. 동작에는 문제없지만 보드 시간 캐시 전용 상수가 없어 의도가 불분명하다. `BOARD_TIME_CACHE_MAX_ITEMS`를 분리 정의한다 (값은 동일해도 무방).

---

## 진행 관리

| 항목 | 우선순위 | 예상 효과 | 리스크 | 상태 |
|---|---|---|---|---|
| P1-1 `/board` 목록 캐시 | P1 | 리스트뷰 요청 대폭 절감 | 낮음 (기존 캐시 패턴 재사용) | 미착수 |
| P1-2 정적 자산 압축 | P1 | 첫 방문 전송량 ~80% 절감 | 매우 낮음 | 미착수 |
| P1-3 `/read` 단기 캐시 | P1(선택) | 게시글 요청 절감 | 중간 (댓글 최신성) | 측정 후 결정 |
| P2-1 워커 수 재검토 | P2 | 캐시 적중률 상승 | 낮음 (운영 설정) | 미착수 |
| P2-2 검색 결과 캐시 | P2 | 검색 요청 절감 | 낮음 | 미착수 |
| P2-3 BS4 파서 통일 | P2 | 홈/검색 파싱 CPU 절감 | 낮음 (테스트로 확인) | 미착수 |
| P2-4 이중 파싱 계측 | P2 | 미정 (계측 우선) | — | 미착수 |
| P3-1 폰트 preconnect | P3 | 첫 렌더 소폭 개선 | 매우 낮음 | 미착수 |
| P3-2 정적 로그 제외 | P3 | 로그 볼륨 절감 | 매우 낮음 | 미착수 |
| P3-3 prune 비용 절감 | P3 | 락 경합 소폭 완화 | 낮음 | 미착수 |
| P3-4 캐시 상수 정리 | P3 | 가독성 | 없음 | 미착수 |
