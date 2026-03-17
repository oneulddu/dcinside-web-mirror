# DCinside Web Mirror 업스트림 요청 Throttle 기능 추가 계획

> **⚠️ 이 문서는 초기 계획 문서입니다.**
>
> **최신 문서는 `docs/throttle/` 디렉토리를 참조하세요:**
> - [README.md](./docs/throttle/README.md) - 인덱스
> - [01-OVERVIEW.md](./docs/throttle/01-OVERVIEW.md) - 개요
> - [02-DESIGN.md](./docs/throttle/02-DESIGN.md) - 설계
> - [03-IMPLEMENTATION.md](./docs/throttle/03-IMPLEMENTATION.md) - 구현 가이드
> - [04-OPERATIONS.md](./docs/throttle/04-OPERATIONS.md) - 운영 가이드

---

## 목적

`dcinside-web-mirror`에 **요청 간 최소 간격**과 **레이트리밋 감지 후 일시 정지(backoff)** 를 넣어, DCinside 업스트림으로 짧은 시간에 요청이 몰리는 상황을 줄인다.

이 기능은 사용자가 전에 언급한 userscript의 `batchDelay`/레이트리밋 완화 아이디어를 **서버 사이드 구조에 맞게 재해석**해서 넣는 것이 목표다.

핵심 목표는 아래 3가지다.

1. 업스트림 요청 폭주를 줄인다.
2. 429/"너무 많은 요청" 계열 응답이 보이면 자동으로 잠깐 쉬었다가 다시 시도한다.
3. 페이지를 깨지지 않게 유지하면서, 부가 정보는 필요 시 일부 생략할 수 있게 만든다.

## 문서 개선 사항 (2026-03-17)

기존 430줄 단일 문서를 4개로 분리하고 다음 내용을 보완했습니다:

### 추가된 내용
1. **현재 코드 구조 반영**: `dc_api.py`, `core.py`, `routes.py`의 실제 구조 확인
2. **구현 우선순위 명확화**: Phase 1-4로 세분화, 점진적 적용 전략
3. **엣지 케이스 추가**:
   - 타임아웃과의 상호작용
   - `_run_async()` 패턴과 throttle
   - 캐시 히트 시 throttle 스킵
4. **설정값 재검토**: `MAX_CONCURRENCY=1` → `2` 권장
5. **관측성 우선순위 상향**: 필수 로깅을 1차 구현에 포함
6. **롤백 시나리오**: 비활성화 시 동작 명확화
7. **테스트 전략 구체화**: pytest 도입 필수, 단위/통합 테스트 가이드

### 문서 구조
- **01-OVERVIEW.md**: 목적, 문제, 해결 방향 (60줄)
- **02-DESIGN.md**: 아키텍처, 설정, 동작 원리 (180줄)
- **03-IMPLEMENTATION.md**: 단계별 작업, 코드 예시 (250줄)
- **04-OPERATIONS.md**: 배포, 모니터링, 트러블슈팅 (120줄)

---

## 빠른 참조

### 구현 체크리스트
- [ ] Phase 1: `upstream_throttle.py` 모듈 + pytest
- [ ] Phase 2: `dc_api.py` 래퍼 (`get_gallery_posts`, `get_document`)
- [ ] Phase 3: `routes.py` sync 호출 (`_fetch_heung_galleries`, `_search_galleries`)
- [ ] Phase 4: `core.py` author_code budget

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
```

---

## 아카이브: 원본 계획 문서

> 아래 내용은 참고용으로 보존합니다. 최신 정보는 `docs/throttle/`을 참조하세요.

### 현재 구조 요약 (main 브랜치 기준)

### 1) 업스트림 호출이 두 군데로 나뉘어 있다

- `app/services/dc_api.py`
  - `aiohttp.ClientSession` 기반 비동기 스크래핑 API
  - 게시판 목록, 글 읽기, 댓글, 각종 Ajax/쓰기 계열까지 이 파일이 대부분 담당
- `app/routes.py`
  - 홈 흥한 갤러리 조회, 갤러리 검색, 미디어 프록시에서 `requests.get(...)` 를 직접 사용

즉, **비동기 호출(aiohttp)** 과 **동기 호출(requests)** 이 동시에 존재한다.

### 2) 현재 목록/관련글 경로는 생각보다 요청 수가 커질 수 있다

- `/board` → `async_index(...)`
  - 목록 HTML을 가져온 뒤
  - `author_code` 가 비어 있으면 `_fill_missing_author_code(...)` 에서 `api.document(...)` 를 다시 호출해 보강한다.
- `/read/related` → `async_related_by_position(...)`
  - 여러 페이지를 탐색해 현재 글 주변 글을 찾고
  - 결과 항목마다 다시 `_fill_missing_author_code(...)` 가 돌 수 있다.

즉, **1개의 화면 응답이 다수의 업스트림 요청으로 번지는 N+1 패턴** 이 이미 있다.

### 3) 이미 일부 완화 장치는 있지만, “요청 페이싱”은 없다

- `core.py` 에는 `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE` 가 있다.
- `read_related_loader.js` 는 `requestIdleCallback(...)` 로 관련 글 fetch 자체를 idle 시점에 늦춘다.

하지만 현재 main 기준으로 `dc_api.py` 내부에는 아래가 보이지 않는다.

- `Semaphore`
- `sleep(...)`
- `429` 대응
- `retry`/`backoff`
- `throttle` 관련 로직

즉, **클라이언트 idle 로딩은 있어도, 서버가 업스트림에 보내는 실제 요청 속도 제어는 아직 없다**.

---

## 왜 이 기능이 필요한가

이 레포는 Flask 요청 한 번이 내부에서 여러 DCinside 호출로 이어질 수 있다. 특히 아래 조합에서 burst 가 커질 수 있다.

- 게시판 목록 진입
- 관련 게시글 로딩
- 작성자 코드 보강
- 흥한 갤러리/검색 호출
- 같은 시각에 여러 사용자가 같은 인스턴스를 때리는 경우

또한 README/.env 예시를 보면 워커/스레드 설정이 있으므로, 실제 배포 환경에서는 **동시성 증가 = 업스트림 burst 증가** 로 이어질 수 있다. 따라서 단순 캐시만으로는 부족하고, **요청 시작 시점 자체를 간격 두고 보내는 장치**가 필요하다.

---

## 구현 원칙

1. **1차 목표는 “서버-업스트림 사이의 간격 제어”** 다. 프런트에 또 다른 지연 UI를 넣는 것이 아니다.
2. **핵심 데이터는 우선**, 부가 데이터는 필요 시 줄인다.
3. **기본값은 보수적이되 과하게 느리지 않게** 잡는다.
4. **환경변수 기반 설정**으로 넣는다. 이 프로젝트는 이미 운영 설정을 env 중심으로 관리한다.
5. **프로세스 단위로 먼저 구현**한다. 다중 워커 전체를 완전히 하나의 글로벌 레이트리미터로 묶는 것은 2차 과제로 둔다.

---

## 제안 아키텍처

## A. 새 모듈 추가: `app/services/upstream_throttle.py`

새 모듈을 하나 만들고, 업스트림 요청 제어를 이곳에 모은다.

### 책임

- 환경변수 읽기
- 프로세스 로컬 throttle 상태 보관
- 요청 시작 간 최소 간격(min interval) 적용
- jitter(랜덤 소폭 지연) 적용
- 레이트리밋 감지 시 `blocked_until` 갱신
- async/sync 양쪽에서 모두 재사용 가능한 대기 함수 제공

### 권장 내부 구조

- `ThrottleConfig`
- `ThrottleState`
- `wait_for_turn_sync()`
- `wait_for_turn_async()`
- `register_rate_limit(...)`
- `is_rate_limited_response(status, text, headers)`

### 상태값 예시

- `next_request_at_monotonic`
- `blocked_until_monotonic`
- `consecutive_rate_limits`
- `last_reason`

### 동기/비동기 공용 상태

이 프로젝트는 Flask sync route + aiohttp async 호출을 함께 쓰므로, **상태는 thread-safe 한 공용 객체**로 들고 가는 편이 좋다.

- 상태 보호: `threading.Lock`
- sync 대기: `time.sleep(...)`
- async 대기: `await asyncio.sleep(...)`

이 방식이면 같은 프로세스 안에서 `requests` 와 `aiohttp` 호출이 **같은 페이싱 규칙을 공유**할 수 있다.

---

## B. `dc_api.py` 의 모든 업스트림 호출을 래핑

현재 `dc_api.API` 는 `self.session.get(...)`, `self.session.post(...)` 를 여러 위치에서 직접 부른다. 이 패턴을 유지하면 throttle 누락 지점이 계속 생긴다.

따라서 `API` 클래스 안에 아래 래퍼를 추가하는 방향을 권장한다.

### 추가할 래퍼 예시

- `_request_text(method, url, *, headers=None, data=None, cookies=None, referer=None)`
- `_request_bytes(...)`
- `_get_html_from_urls(urls)`

### 래퍼 동작 순서

1. `wait_for_turn_async()` 호출
2. 실제 `session.get/post` 실행
3. status / response body 일부 확인
4. 레이트리밋 징후가 있으면 `register_rate_limit(...)`
5. 필요 시 1회 재시도 또는 그대로 상위에 예외 전달

### 감지 기준

우선순위는 아래처럼 단순하게 시작하는 것이 좋다.

1. HTTP status `429`
2. 응답 본문에 다음 문자열 포함
   - `Too Many Requests`
   - `너무 많은 요청`
   - `penalty-box`
3. `Retry-After` 헤더가 있으면 그 값을 backoff 에 반영

### 왜 “요청 시작 시점 간격” 방식이 좋은가

- 구현이 단순하다.
- burst 억제에 직관적이다.
- 현재 구조처럼 한 요청이 내부적으로 여러 개로 찢어질 때 제어하기 쉽다.

---

## C. `routes.py` 의 sync `requests.get(...)` 도 같은 정책으로 묶기

`app/routes.py` 에는 아래 sync 호출이 있다.

- `_fetch_heung_galleries()`
- `_search_galleries()`
- `/media`

이 중 최소한 **흥한 갤러리 조회/갤러리 검색** 은 같은 throttle 을 타게 하는 것이 좋다.

### 권장 처리

- `_fetch_heung_galleries()` → `wait_for_turn_sync()` 후 `requests.get(...)`
- `_search_galleries()` → `wait_for_turn_sync()` 후 `requests.get(...)`

### `/media` 는 1차에서 선택 적용

미디어 프록시는 이미지 체감 속도에 직접 영향을 준다. 여기에 기본 throttle 을 강하게 걸면 화면이 너무 무거워질 수 있다.

따라서 1차는 아래 중 하나를 권장한다.

- 기본 제외 (`MIRROR_MEDIA_THROTTLE_ENABLED=false`)
- 혹은 아주 느슨한 별도 한도 적용

즉, **텍스트/HTML 스크래핑 경로와 이미지 프록시 경로를 같은 강도로 묶지 않는 것**이 낫다.

---

## D. 단순 지연만 넣지 말고, “부가 요청 축소”도 같이 넣기

이 레포에서 가장 큰 burst 원인 중 하나는 `author_code` 보강을 위한 추가 `document(...)` 호출이다.

지연만 넣으면 요청 수는 그대로인데 응답 시간만 길어진다. 그래서 **secondary fetch budget** 을 같이 넣는 편이 좋다.

### 권장 정책

- 게시판 목록(`/board`)에서는 `author_code` 보강 요청 수를 페이지당 제한
- 관련 게시글(`/read/related`)에서도 별도 제한
- 예산이 다 떨어지면 **작성자명만 표시하고 author_code 는 비워 둠**

### 추천 예시

- 목록 페이지 author code 보강: 최대 6~8건
- 관련글 author code 보강: 최대 3~4건
- rate-limit 상태일 때: 0건

### 필요한 변경점

- `core.async_index(...)` 에 `author_code_budget` 인자 추가
- `_fill_missing_author_code(...)` 호출 전에 budget 차감
- `async_related_by_position(...)` 도 동일 정책 적용

이렇게 하면 userscript 의 `disableFetch`/캐시 전략과 비슷하게, **부가 기능은 줄이고 핵심 화면은 유지**하는 구조가 된다.

---

## 설정값 제안

## 필수 설정

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `MIRROR_UPSTREAM_THROTTLE_ENABLED` | `true` | 업스트림 요청 페이싱 사용 여부 |
| `MIRROR_UPSTREAM_MIN_INTERVAL_MS` | `150` | 요청 시작 간 최소 간격 |
| `MIRROR_UPSTREAM_MAX_CONCURRENCY` | `1` | 프로세스 내 동시 업스트림 요청 수 |
| `MIRROR_UPSTREAM_JITTER_MS` | `50` | 요청 간 랜덤 지터 |
| `MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS` | `5000` | rate limit 감지 시 기본 휴지 시간 |
| `MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS` | `15000` | 연속 rate limit 시 최대 휴지 시간 |

## 부가 최적화 설정

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE` | `8` | `/board` 에서 author code 추가 조회 허용 수 |
| `MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_RELATED_LOAD` | `4` | `/read/related` 에서 author code 추가 조회 허용 수 |
| `MIRROR_MEDIA_THROTTLE_ENABLED` | `false` | `/media` 경로에 throttle 적용 여부 |
| `MIRROR_UPSTREAM_LOG_EVENTS` | `false` | throttle/backoff 이벤트 로그 출력 |

### 네이밍 메모

userscript 쪽 이름이 `batchDelay` 였더라도, 서버 앱에서는 실제 배치 처리보다는 **업스트림 요청 간 최소 간격**이 핵심이므로 이름은 `MIN_INTERVAL_MS` 쪽이 더 맞다.

---

## 권장 기본 동작

### 정상 상태

- 요청은 150ms 간격으로 보낸다.
- 프로세스 내 동시 업스트림 요청은 1개만 허용한다.
- 소량의 jitter 를 넣어 완전히 기계적인 패턴을 피한다.

### rate limit 감지 상태

- 즉시 `blocked_until = now + 5000ms`
- 연속 발생 시 5s → 10s → 15s 수준으로 점증
- 이 상태에서는 secondary fetch(author code 보강)는 생략
- 핵심 요청만 통과시킨다.

### 캐시와의 관계

기존 `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE` 는 유지한다. 새 throttle 은 캐시를 대체하는 것이 아니라, **캐시 미스 시 burst 를 제어하는 안전장치**다.

---

## 작업 범위

## 1. 신규 파일

- `app/services/upstream_throttle.py`

## 2. 수정 파일

- `app/services/dc_api.py`
- `app/services/core.py`
- `app/routes.py`
- `app/config.py`
- `.env.example`
- `README.md`

## 3. 선택적 신규 파일

- `tests/test_upstream_throttle.py`
- `tests/test_author_code_budget.py`

현재 레포에는 테스트 인프라가 거의 없으므로, 테스트를 도입한다면 `pytest` 추가 여부까지 같이 정해야 한다.

---

## 세부 작업 순서

### 1단계: throttle 모듈 추가

- env 파서 작성
- thread-safe 상태 객체 작성
- sync/async 대기 함수 작성
- rate limit 감지 함수 작성

### 2단계: `dc_api.py` 공통 래퍼 도입

- 직접 `self.session.get/post` 하는 지점을 공통 래퍼로 치환
- `__fetch_parsed_from_urls(...)` 부터 먼저 바꾸고
- 이후 `document`, `comments`, 쓰기/삭제/수정 계열까지 점진 적용

### 3단계: `routes.py` sync 호출 연결

- `_fetch_heung_galleries()`
- `_search_galleries()`
- 필요 시 `/media`

### 4단계: secondary fetch budget 추가

- `core.async_index(...)`
- `core.async_related_by_position(...)`
- `_fill_missing_author_code(...)` 호출 조건 변경

### 5단계: 운영 설정 문서화

- `.env.example` 기본값 추가
- README 환경변수 표 업데이트
- “응답은 조금 느려질 수 있지만 upstream 429 빈도를 낮춘다” 는 운영 가이드 추가

### 6단계: 테스트/수동 검증

- 단일 요청 시 정상 동작
- 연속 요청 시 간격 적용 확인
- 429/mock 문자열 응답 시 backoff 진입 확인
- author_code budget 초과 시 graceful degradation 확인

---

## 구현 시 주의점

### 1) 다중 워커 환경에서는 “프로세스별 throttle” 이다

이 계획의 1차 구현은 **프로세스 로컬** 기준이다.

예를 들어 Gunicorn worker 가 3개면, 각 worker 가 자기 throttle state 를 따로 가진다. 따라서 전체 인스턴스 기준 완전한 글로벌 한도는 아니다.

이 한계는 문서에 명시해야 한다.

### 2) 너무 큰 지연은 Flask 응답 시간을 직접 늘린다

`/board`, `/read`, `/read/related` 는 서버 응답 안에서 업스트림 호출을 기다린다. 따라서 지연값을 과하게 크게 주면 페이지 체감이 나빠진다.

그래서 기본값은 **작은 간격 + rate limit 시만 강한 backoff** 구조가 좋다.

### 3) HTML body 전체를 항상 읽어 rate limit 문자열을 검사하면 비용이 커질 수 있다

- HTML/JSON 응답: 문자열 검사 가능
- binary/media 응답: status 중심 검사 권장

### 4) monotonic clock 사용

간격 계산/blocked_until 계산은 `time.time()` 보다 `time.monotonic()` 기준이 안전하다.

---

## 최소 성공 기준 (Acceptance Criteria)

아래가 만족되면 1차 목적은 달성한 것으로 본다.

- `dc_api.py` 의 업스트림 GET/POST 가 공통 throttle 래퍼를 탄다.
- 홈 흥한 갤러리/갤러리 검색 sync 호출도 동일 정책을 공유한다.
- 429 또는 rate-limit 문구 감지 시 5초 이상 backoff 가 걸린다.
- `/board` 와 `/read/related` 에서 author_code 보강 요청 수를 제한할 수 있다.
- `.env.example` 와 README 에 새 설정이 문서화된다.
- 기능 비활성화(`MIRROR_UPSTREAM_THROTTLE_ENABLED=false`) 시 기존 동작으로 되돌릴 수 있다.

---

## 권장 PR 분리 방식

### PR 1

- `upstream_throttle.py`
- `dc_api.py` 공통 래퍼
- `.env.example` / README 기본 문서

### PR 2

- `routes.py` sync 호출 연동
- media 경로 처리 방침 정리

### PR 3

- `core.py` author_code budget / graceful degradation
- 테스트 보강

이렇게 나누면 위험도가 높은 변경을 한 번에 몰아넣지 않을 수 있다.

---

## 후속 확장 아이디어

1. **관측성**
   - 최근 1분 rate-limit 감지 횟수
   - 현재 blocked 상태 여부
   - secondary fetch skip 횟수

2. **분산 throttle**
   - Redis 기반 shared state
   - 여러 worker / 여러 인스턴스 간 통합 한도

3. **동적 정책**
   - 최근 실패가 많으면 자동으로 `author_code_budget` 축소
   - 한동안 안정적이면 원복

4. **관련글 로더 재시도**
   - 서버가 throttle 상태일 때 `/read/related` 응답에 `deferred: true` 를 넣고
   - 프런트가 2~3초 뒤 1회 재시도

---

## 결론

이 기능은 단순히 `sleep(...)` 한 줄 추가로 끝내기보다,

- 업스트림 요청 공통 래핑
- process-local throttle state
- rate limit backoff
- secondary fetch budget
- 운영 설정 문서화

까지 같이 들어가야 효과가 좋다.

특히 이 레포는 이미 **클라이언트 idle 로딩**과 **캐시**는 일부 갖고 있으므로, 다음으로 필요한 것은 **서버가 업스트림에 보내는 실제 요청 속도와 추가 요청 개수를 제어하는 층**이다.

그 방향으로 구현하면 userscript 의 `batchDelay` 개념을 이 프로젝트에 가장 자연스럽게 옮길 수 있다.
