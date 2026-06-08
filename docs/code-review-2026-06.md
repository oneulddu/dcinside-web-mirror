# 코드 전체 검토 (2026-06)

이 문서는 `app/` 전체 코드와 설정·배포·문서를 검토하고, **수정할 점**과 **추가하면 좋은 점**을
우선순위별로 정리한 기록입니다. 검토 시점 기준 테스트는 152개 전부 통과합니다.

검토 범위: `app/routes.py`, `app/__init__.py`, `app/config.py`, `app/services/*`,
`gunicorn.conf.py`, `ecosystem.config.js`, `env_loader.py`, `tests/`, 프로젝트 문서.

---

## 요약

| 우선순위 | 항목 | 분류 | 상태 |
|---|---|---|---|
| 🔴 높음 | `AGENTS.md` · `CLAUDE.md` 문서가 실제 코드와 불일치 | 문서 | 처리됨 |
| 🔴 높음 | 요청마다 `aiohttp.ClientSession` 새로 생성 (연결 재사용 없음) | 성능 | 검토 필요 |
| 🟡 중간 | `async_related_by_position` 계열이 앱에서 미사용 (테스트만 참조) | 정리 | 제거됨 |
| 🟡 중간 | `dc_api.py`의 쓰기 API(write/modify/remove) 미사용 | 정리 | 보류 |
| 🟡 중간 | 미디어 프록시 DNS rebinding 잔여 창(TOCTOU) | 보안 | 인지/선택 |
| 🟢 낮음 | `XML_HTTP_REQ_HEADERS` 중복 키, `re` import 위치 등 코드 위생 | 위생 | 일부 처리 |
| 🟢 낮음 | 린터/포매터(CI) 부재 | 도구 | 추가 권장 |
| 🟢 낮음 | 프런트엔드(JS/CSS) 테스트·접근성 점검 부재 | 품질 | 선택 |

이번 PR 처리 기록:
- `AGENTS.md`와 `CLAUDE.md`를 README 기준의 실제 라우트, 함수명, 서비스 구조로 갱신.
- 미사용 `async_related_by_position` / `_related_by_position_with_api` 경로와 전용 관련글 캐시를 제거하고, 테스트는 현재 런타임 경로인 `_related_after_position_with_api` 중심으로 정리.
- `dc_api.py`의 중복 `XML_HTTP_REQ_HEADERS` 키, `re` import 위치, 죽은 `!TODO` 주석 블록을 정리.
- 쓰기 API는 앱 라우트에서 미사용임을 `rg`로 확인했지만, `API`의 공개 메서드 형태이고 전용 검증 테스트가 있어 이번 저위험 PR에서는 삭제하지 않고 보류 주석만 추가.

---

## 🔴 높음

### 1. `AGENTS.md` · `CLAUDE.md` 문서가 실제 코드와 어긋남

두 가이드 문서가 현재 라우트/함수와 맞지 않습니다. 에이전트가 잘못된 전제를 갖게 됩니다.

| 문서에 적힌 내용 | 실제 코드 |
|---|---|
| `/board/<board_id>` | `/board?board=...&page=...` (쿼리 파라미터) |
| `/read/<board_id>/<doc_id>` | `/read?board=...&pid=...` |
| `/media-proxy` | `/media` (이미지), `/movie` (영상) |
| `/api/related` | `/read/related` |
| `get_gallery_posts()`, `get_document()`, `search_gallery()` | 존재하지 않음. 실제: `dc_api.API.board()`, `API.document()`, `heung.search_galleries()` |
| `async_index()`, `async_related_by_position()` | 실제 라우트는 `async_index_with_head_categories()`, `async_related_after_position()` 사용 |
| `_run_async(coro)` | `async_bridge.run_async(coro)` |

> README.md는 최근에 현행화했으므로, AGENTS.md / CLAUDE.md도 같은 기준으로 맞추는 것을 권장합니다.

**조치**: 두 문서의 라우트/함수 표기를 실제 코드 기준으로 갱신.

**처리**: 이번 PR에서 README 기준으로 `AGENTS.md`와 `CLAUDE.md`를 갱신했습니다.

---

### 2. 요청마다 새 `aiohttp.ClientSession` 생성

`core.py`의 모든 진입점(`async_read`, `async_index*`, `async_related_*`)이
`async with dc_api.API() as api:` 로 매 호출마다 세션을 새로 만들고 닫습니다.

```python
# core.py
async def async_read(...):
    async with dc_api.API() as api:   # 요청마다 새 ClientSession
        ...
```

여기에 `async_bridge`가 (running loop가 있을 때) `ThreadPoolExecutor` + `asyncio.run()`으로
**매 요청마다 새 이벤트 루프**까지 만드는 구조가 더해집니다. 일반 WSGI 경로에서는 asgiref가
처리하지만, 어느 경로든 커넥션 풀/Keep-Alive 재사용 이점이 사라집니다.

DCinside는 한 페이지 렌더링에 목록·문서·댓글·작성자코드 보강 등 다건 요청을 보내므로,
TCP/TLS 핸드셰이크 반복 비용이 체감됩니다.

**조치 방향(선택)**:
- 단기: 한 라우트 처리 동안 단일 `API()`를 만들어 관련 호출을 공유 (지금도 대체로 그렇지만, 관련글/본문이 별도 호출로 갈리는 지점 점검).
- 중기: 이벤트 루프별 영속 세션 재사용(또는 ASGI/Quart 전환)으로 커넥션 풀 유지.
- 미디어 프록시(`media_proxy.py`)는 이미 `requests.Session`을 thread-local로 재사용 중 → 같은 철학을 스크래퍼에도 적용 가능.

---

## 🟡 중간

### 3. `async_related_by_position` / `_related_by_position_with_api` 미사용

라우트(`/read/related`)는 `async_related_after_position`만 사용합니다.
제거 전 `async_related_by_position`·`_related_by_position_with_api`는
**앱 런타임에서 호출되지 않고 `tests/test_rate_limit_reductions.py`에서만** 참조됐습니다.

- 약 230줄의 유지보수 부담이 됩니다.
- "관련글" 로직이 사실상 두 벌(by_position / after_position)로 존재해 변경 시 혼동을 유발합니다.

**조치(택1)**:
- 제거: 함수와 해당 테스트를 함께 삭제하여 단일 경로(after_position)로 통일.
- 유지: 의도적으로 남긴다면 docstring으로 "현재 미사용, 폴백/실험용" 명시.

**처리**: 이번 PR에서 제거를 선택했습니다. 런타임 경로는 `async_related_after_position` 하나로 두고,
테스트도 after_position 중심으로 정리했습니다.

### 4. `dc_api.py`의 쓰기 계열 API 미사용

`write_comment`, `write_document`, `modify_document`, `remove_document`, `__access`,
`__write_or_modify_document` 등은 읽기 전용 미러 제품에서 사용되지 않습니다(약 250줄+).
업로드 도메인(`mupload.dcinside.com`) 호출, CSRF/con_key 처리 등 복잡도가 큽니다.

**조치(택1)**:
- 제품이 읽기 전용으로 고정이면 별도 모듈로 분리하거나 제거해 `dc_api.py` 표면 축소.
- 향후 쓰기 기능 계획이 있으면 유지하되 "현재 라우트 미연결" 주석 추가.

**처리**: 이번 PR에서는 삭제하지 않고 보류했습니다. 앱 라우트에서는 미사용이지만 `API` 공개 메서드처럼
노출되어 있고, 쓰기 응답 검증 테스트가 남아 있어 저위험 정리 범위를 넘는다고 판단했습니다.

### 5. 미디어 프록시 DNS rebinding 잔여 창 (TOCTOU)

`media_proxy.py`는 `is_public_hostname()`으로 DNS를 한 번 검사한 뒤,
실제 요청은 `requests`가 호스트명을 **다시 resolve** 합니다. 두 resolve 사이에
응답이 사설 IP로 바뀌면 이론적으로 우회 가능합니다(코드 주석도 캐시 미적용으로 이를 인지).

현재는 ① 호스트 allowlist(`dcinside.com` 등) + ② 공인 IP 검사 + ③ 리다이렉트 재검증으로
실질 위험은 낮습니다. 완전 차단을 원하면:

**조치(선택)**: resolve한 IP로 직접 연결하고 `Host`/SNI를 고정하는 방식(핀드 커넥션)으로
"검사한 IP == 접속한 IP"를 보장. 구현 비용이 있으므로 위협 모델에 따라 결정.

---

## 🟢 낮음 (코드 위생 / 도구)

### 6. 코드 위생

- **`dc_api.py` `XML_HTTP_REQ_HEADERS`에 `"X-Requested-With"` 키 중복**(54행, 57행). 무해하나 정리 권장.
- **`re` import 위치**: `to_int()`(21행)가 `re.sub`를 쓰는데 `import re`는 77행. 모듈 로드 후 호출되므로 동작은 하지만, import를 파일 상단으로 모으는 편이 안전/명확.
- **`__comments_from_pc` / `__comments_from_mobile`의 `range(start_page, 999999)`**: 매직 상수. 페이지 상한 상수화 권장.
- **`document()` 말미의 주석 처리된 `!TODO` 블록**(약 1941~1948행): 죽은 주석 코드 제거 권장.
- **`config.py`의 `prune_recent_server_cache_locked` 류**는 양호. 캐시 구현이 모듈마다 비슷한 패턴(prune/lock/TTL)으로 반복됨 → 공통 TTL 캐시 유틸로 추출하면 `core.py`·`recent.py`·`media_proxy.py` 중복 감소.

**처리**: 중복 헤더 키, `re` import 위치, 죽은 `!TODO` 블록은 이번 PR에서 정리했습니다.
매직 상수와 TTL 캐시 공통화는 동작 영향이 있어 후속으로 남겼습니다.

### 7. 린터/포매터(CI) 부재

`ruff`/`black` 등 정적 검사 설정이 없습니다. 위 6번류(중복 키, import 순서, 미사용 심볼)는
린터로 자동 검출됩니다.

**조치(권장)**: `ruff`를 `requirements-dev.txt`에 추가하고 GitHub Actions에 `ruff check` 단계 추가.

### 8. 프런트엔드 품질

`static/javascript/*`(테마, 관련글 로더, 댓글 스팸 필터)와 `main.css`에 대한
자동 테스트·접근성 점검이 없습니다.

**조치(선택)**:
- 댓글 스팸 필터 등 순수 로직은 작은 JS 단위 테스트 추가 여지.
- 이미지/iframe `title`·`alt`, 키보드 포커스, 대비 등 접근성 1회 점검.

### 9. 설정·배포 메모

- `gunicorn.conf.py` 기본 `threads=4`, `ecosystem.config.js`는 `MIRROR_THREADS=12`로 상이. 의도된 운영값이면 OK이나 문서에 근거를 남기면 좋음.
- `__init__.py`의 `app.config.from_prefixed_env("MIRROR")`가 `from_object` 뒤에 호출되어 모든 `MIRROR_*`를 config로 흡수. 의도된 동작이며 SECRET_KEY 강제도 정상. 별도 조치 불필요(참고용 기록).

---

## 좋은 점 (유지할 강점)

- **미디어 프록시 보안 설계**: 호스트 allowlist + 공인 IP 검사 + 리다이렉트 재검증 + 콘텐츠 타입 제한 + `nosniff` + 응답 크기 상한. 견고함.
- **HTML 새니타이저**: 허용 태그/속성 화이트리스트, `on*`·비허용 `src` 제거, iframe(YouTube/DC movie/poll)만 정규화 허용. XSS 방어가 명확.
- **캐시 전략**: 게시판 페이지/최신 ID/관련글/작성자코드 다층 캐시에 TTL·최대 항목·prune 적용. 락으로 스레드 안전.
- **흥갤 캐시**: 메모리+파일 2단 캐시에 single-flight refresh 락으로 thundering herd 방지.
- **폴백 견고성**: 모바일→PC URL 폴백, 댓글 모바일/PC 이중 경로, placeholder 이미지 PC 보정 등 DCinside 구조 변화 대응이 촘촘함.
- **테스트 커버리지**: URL 우선순위·미디어/이미지·rate limit 절감 등 회귀 방지 테스트가 두텁다(152개).

---

## 권장 처리 순서

1. **문서 현행화** (#1) — 위험 없음, 즉시 가능.
2. **죽은 코드 정리** (#3, #4, #6의 TODO/중복) — 테스트 동반 수정.
3. **린터 도입** (#7) — 이후 회귀 자동 방지.
4. **세션 재사용 성능 개선** (#2) — 별도 작업으로 신중히.
5. **DNS 핀드 커넥션** (#5) — 위협 모델 결정 후 선택.
