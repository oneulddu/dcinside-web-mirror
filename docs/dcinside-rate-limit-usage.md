# DCinside 요청 제한 사용량 정리

이 문서는 현재 프로젝트가 DCinside 원본 서버에 보내는 요청이 **리스트뷰 제한**과 **게시글 제한**을 어느 정도 소모하는지 정리한 내용이다.

기준 코드는 다음 파일들이다.

- `/Users/oneul/Desktop/Workspace/mirror/app/routes.py`
- `/Users/oneul/Desktop/Workspace/mirror/app/services/core.py`
- `/Users/oneul/Desktop/Workspace/mirror/app/services/dc_api.py`
- `/Users/oneul/Desktop/Workspace/mirror/app/templates/board.html`
- `/Users/oneul/Desktop/Workspace/mirror/app/templates/read.html`
- `/Users/oneul/Desktop/Workspace/mirror/app/static/javascript/read_related_loader.js`

## 전제

DCinside 쪽 제한은 현재 알려진 기준으로 다음처럼 본다.

| 구분 | 추정 제한 |
|---|---:|
| 리스트뷰 | 약 1분에 40회 |
| 게시글 보기 | 약 1분에 30회 |

단, DCinside 응답 헤더에는 `X-RateLimit-Limit`, `X-RateLimit-Remaining`처럼 전체 제한과 남은 횟수만 보이고, 정확한 초기화 시각을 알려주는 `Reset`류 헤더는 확인되지 않았다. 따라서 “1분”은 실제 관찰과 동작 패턴에 따른 추정이다.

## 요청 구분

이 문서에서는 요청을 다음처럼 나눈다.

### 리스트뷰 요청

갤러리 글 목록을 가져오는 요청이다.

예:

```text
https://gall.dcinside.com/board/lists/?id={board}&page={page}
https://gall.dcinside.com/mgallery/board/lists/?id={board}&page={page}
https://gall.dcinside.com/mini/board/lists/?id={board}&page={page}
https://m.dcinside.com/board/{board}?page={page}
```

관련 코드:

- `app/services/dc_api.py`의 `API.board()`
- `app/services/dc_api.py`의 `__build_list_urls()`
- `app/services/core.py`의 `async_index()`
- `app/services/core.py`의 `_fetch_board_page()`

### 게시글 요청

개별 게시글 본문 페이지를 가져오는 요청이다.

예:

```text
https://gall.dcinside.com/board/view/?id={board}&no={pid}
https://gall.dcinside.com/mgallery/board/view/?id={board}&no={pid}
https://gall.dcinside.com/mini/board/view/?id={board}&no={pid}
https://m.dcinside.com/board/{board}/{pid}
```

관련 코드:

- `app/services/dc_api.py`의 `API.document()`
- `app/services/dc_api.py`의 `__build_view_urls()`
- `app/services/dc_api.py`의 `__get_pc_comment_context()`
- `app/services/core.py`의 `async_read()`
- `app/services/core.py`의 `_fill_missing_author_codes()`

### 댓글 요청

댓글 API 요청이다. 리스트뷰/게시글 보기 제한과 같은 묶음으로 집계되는지는 코드만으로 확정할 수 없다.

예:

```text
https://gall.dcinside.com/board/comment/
https://m.dcinside.com/ajax/response-comment
```

관련 코드:

- `app/services/dc_api.py`의 `API.comments()`
- `app/services/dc_api.py`의 `__comments_from_pc()`
- `app/services/dc_api.py`의 `__comments_from_mobile()`

### 미디어 요청

이미지와 디시콘을 가져오는 요청이다.

예:

```text
/media?src={dcinside_image_url}&board={board}&pid={pid}
```

이 요청은 우리 서버의 `/media` 라우트를 거친 뒤 DCinside 이미지 서버로 간다. 일반적인 글 목록/게시글 HTML 제한과 별도일 가능성이 크지만, 원본 이미지 서버 요청량은 따로 발생한다.

관련 코드:

- `app/routes.py`의 `media()`
- `app/routes.py`의 `_rewrite_content_images()`
- `app/templates/read.html`

## `/board` 목록 화면

라우트:

```text
GET /board?board={board}&page={page}
```

관련 코드:

- `app/routes.py`의 `board()`
- `app/services/core.py`의 `async_index()`
- `app/services/dc_api.py`의 `API.board()`

### 기본 흐름

```text
/board
→ async_index()
→ API.board()
→ DCinside 리스트 페이지 요청
→ 목록 파싱
→ _fill_missing_author_codes()
→ 필요한 경우 일부 글에 대해 API.document() 추가 호출
```

### 제한 사용량

| 항목 | 보통 | 늘어나는 경우 |
|---|---:|---:|
| 리스트뷰 제한 | 1회 | 후보 URL 실패/리다이렉트 시 추가 |
| 게시글 제한 | 0회에 가까움 | 작성자 코드가 비어 있는 글마다 추가 |
| 댓글 요청 | 0회 | 없음 |
| 미디어 요청 | 0회 | 없음 |

### 중요한 부분

`async_index()`는 목록만 가져온 뒤 끝나지 않고, 마지막에 `_fill_missing_author_codes()`를 호출한다.

이 함수는 목록에서 작성자 식별 코드가 빠진 글이 있으면 아래처럼 개별 게시글을 다시 요청한다.

```python
doc = await api.document(board_id=board, document_id=doc_id, kind=kind)
```

따라서 `/board`는 화면상으로는 목록만 보여주지만, 네트워크 기준으로는 게시글 제한을 같이 소모할 수 있다.

### `/board` 1페이지 예상

현재 `MAX_PAGE = 31`이다.

| 상황 | 리스트뷰 사용 | 게시글 사용 |
|---|---:|---:|
| 작성자 코드가 목록에서 모두 파싱됨 | 1회 | 0회 |
| 일부 작성자 코드 누락 | 1회 | 누락 글 수만큼 |
| 모든 글의 작성자 코드 누락 | 1회 | 최대 31회 |

즉 최악에 가까운 경우:

```text
리스트뷰 1회 + 게시글 최대 31회
```

이 경우 리스트뷰 제한보다 게시글 제한 30회에 먼저 닿을 수 있다.

단, `_AUTHOR_CODE_CACHE`가 있어 같은 글의 작성자 코드는 일정 시간 재사용된다.

관련 상수:

```python
AUTHOR_CODE_CACHE_TTL = 600
AUTHOR_CODE_FETCH_CONCURRENCY = 5
```

## `/read` 게시글 화면

라우트:

```text
GET /read?board={board}&pid={pid}
```

관련 코드:

- `app/routes.py`의 `read()`
- `app/services/core.py`의 `async_read()`
- `app/services/core.py`의 `_read_document_with_api()`
- `app/services/dc_api.py`의 `API.document()`
- `app/services/dc_api.py`의 `API.comments()`

### 기본 흐름

```text
/read
→ async_read()
→ API.document()
→ DCinside 게시글 본문 요청
→ doc.comments()
→ API.comments()
→ __get_pc_comment_context()
→ 댓글용 컨텍스트 확보를 위해 같은 게시글 view 재요청
→ PC 댓글 API 요청
→ 실패 시 모바일 댓글 API 요청
```

### 제한 사용량

| 항목 | 보통 | 늘어나는 경우 |
|---|---:|---:|
| 게시글 제한 | 2회 안팎 | 후보 URL 실패, 댓글 컨텍스트 재시도 |
| 리스트뷰 제한 | 0회 | 본문 HTML 렌더링만 보면 없음 |
| 댓글 요청 | 1회 이상 | 댓글 페이지가 여러 페이지이거나 PC 댓글 실패 후 모바일 fallback |
| 미디어 요청 | 이미지 개수만큼 | 본문 이미지, 댓글 디시콘이 많을수록 증가 |

### 중요한 부분

게시글 화면은 게시글 본문을 한 번만 가져오는 것처럼 보이지만, 댓글 처리 때문에 같은 게시글 view를 다시 요청할 수 있다.

흐름은 다음과 같다.

```text
API.document()
→ 게시글 본문 요청 1회

API.comments()
→ __get_pc_comment_context()
→ 댓글 토큰/갤러리 타입 확인용 게시글 view 요청 1회
```

따라서 `/read` 한 번은 보통 게시글 제한을 최소 1회, 실제로는 2회 안팎 소모할 수 있다.

## `/read/related` 관련글 요청

라우트:

```text
GET /read/related?board={board}&pid={pid}&limit=12
```

관련 코드:

- `app/templates/read.html`
- `app/static/javascript/read_related_loader.js`
- `app/routes.py`의 `read_related()`
- `app/services/core.py`의 `async_related_by_position()`
- `app/services/core.py`의 `_fetch_board_page()`

### 기본 흐름

게시글 화면에는 관련글 영역이 있고, 브라우저가 페이지 로드 후 `/read/related`를 한 번 호출한다.

```text
/read 화면 렌더링
→ read_related_loader.js 실행
→ /read/related fetch
→ async_related_by_position()
→ 현재 글 위치를 찾기 위해 DCinside 리스트 페이지 여러 번 요청
→ 필요한 경우 관련글 작성자 코드 보완용 게시글 요청
```

### 제한 사용량

| 항목 | 보통 | 늘어나는 경우 |
|---|---:|---:|
| 리스트뷰 제한 | 1회 이상 | 현재 글 위치 탐색, 꼬리 페이지 로딩 |
| 게시글 제한 | 0회에 가까움 | 관련글 작성자 코드가 비어 있으면 추가 |
| 댓글 요청 | 0회 | 없음 |
| 미디어 요청 | 0회 | 없음 |

### 관련 상수

```python
RELATED_LIMIT = 12
RELATED_PAGE_PROBE_STEPS = 8
RELATED_TAIL_PAGES = 3
RELATED_CACHE_TTL = 90
LATEST_ID_CACHE_TTL = 20
```

### `/read/related` 1회 예상

관련글 로직은 대략 다음 요청을 만들 수 있다.

| 단계 | 리스트뷰 사용 |
|---|---:|
| 최신 글 번호 확인 | 최대 1회 |
| 현재 글 위치 탐색 | 최대 8회 |
| 다음 페이지 꼬리 탐색 | 최대 3회 |

최악에 가까운 경우:

```text
리스트뷰 최대 12회
```

여기에 관련글 결과의 작성자 코드가 비어 있으면 게시글 요청이 추가된다.

관련글 결과 기본 개수는 `limit=12`이므로, 최악에 가까우면:

```text
리스트뷰 최대 12회 + 게시글 최대 12회
```

까지 갈 수 있다.

단, `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE`가 있어 같은 조건의 관련글 조회는 일정 시간 재사용된다.

## `/media` 이미지 요청

라우트:

```text
GET /media?src={src}&board={board}&pid={pid}
```

관련 코드:

- `app/routes.py`의 `media()`
- `app/routes.py`의 `_rewrite_content_images()`
- `app/templates/read.html`

### 기본 흐름

```text
/read
→ API.document()로 본문 HTML과 이미지 URL 확보
→ _rewrite_content_images()가 본문 img src를 /media로 변경
→ 브라우저가 이미지 로딩 시 /media 요청
→ 우리 서버가 DCinside 이미지 URL을 requests.get()으로 가져옴
```

### 제한 사용량

미디어 요청은 글 목록/게시글 HTML 요청과 성격이 다르다. 보통 리스트뷰 40회, 게시글 30회 제한과 직접 같은 묶음으로 보기는 어렵다.

다만 원본 이미지 서버 요청은 이미지 개수만큼 발생한다.

| 상황 | 미디어 요청 |
|---|---:|
| 이미지 없는 글 | 0회 |
| 본문 이미지 3개 | 3회 |
| 댓글 디시콘 5개 | 5회 |

## 화면별 종합

### 목록 한 페이지 보기

```text
GET /board?board={board}&page=1
```

| 요청 종류 | 최소 | 보통 | 최악에 가까운 경우 |
|---|---:|---:|---:|
| 리스트뷰 | 1회 | 1회 | 후보 URL 실패 시 추가 |
| 게시글 | 0회 | 일부 글 수만큼 | 최대 31회 |
| 댓글 | 0회 | 0회 | 0회 |
| 미디어 | 0회 | 0회 | 0회 |

주의점:

```text
목록 화면인데도 게시글 제한을 소모할 수 있다.
```

원인은 작성자 코드 보완용 `api.document()` 호출이다.

### 게시글 하나 보기

```text
GET /read?board={board}&pid={pid}
```

관련글 자동 로딩까지 포함하면:

| 요청 종류 | 최소 | 보통 | 최악에 가까운 경우 |
|---|---:|---:|---:|
| 게시글 | 1회 | 2회 안팎 | 후보 URL 실패, 댓글 컨텍스트 재시도, 관련글 작성자 코드 보완 |
| 리스트뷰 | 0회 | 관련글 로딩으로 1회 이상 | 관련글 탐색 최대 12회 |
| 댓글 | 1회 안팎 | 1회 이상 | 댓글 여러 페이지 + fallback |
| 미디어 | 0회 | 이미지 개수만큼 | 이미지/디시콘 개수만큼 |

주의점:

```text
게시글 화면인데도 리스트뷰 제한을 소모한다.
```

원인은 `read_related_loader.js`가 자동으로 `/read/related`를 호출하기 때문이다.

## 제한 관점에서 위험한 지점

### 1. 목록의 작성자 코드 보완

위치:

```text
app/services/core.py
```

흐름:

```text
async_index()
→ _fill_missing_author_codes()
→ _fill_missing_author_code()
→ api.document()
```

문제:

```text
목록 한 페이지에서 게시글 요청이 최대 31회까지 늘 수 있다.
```

게시글 제한이 1분에 30회라면, 목록 한 번으로 제한에 닿을 가능성이 있다.

### 2. 게시글의 댓글 컨텍스트용 재조회

위치:

```text
app/services/dc_api.py
```

흐름:

```text
API.document()
→ 게시글 본문 요청

API.comments()
→ __get_pc_comment_context()
→ 같은 게시글 view 재요청
```

문제:

```text
게시글 하나를 열 때 게시글 view 요청이 2회 안팎 발생할 수 있다.
```

### 3. 게시글의 관련글 자동 로딩

위치:

```text
app/static/javascript/read_related_loader.js
app/routes.py
app/services/core.py
```

흐름:

```text
/read
→ /read/related
→ async_related_by_position()
→ _fetch_board_page()
```

문제:

```text
게시글 하나를 열 때 리스트뷰 요청이 최대 12회 안팎 추가될 수 있다.
```

## 개선 우선순위

### 1순위: 목록에서 작성자 코드 보완 요청 줄이기

현재 가장 큰 위험은 `/board`에서 `_fill_missing_author_codes()`가 게시글 요청을 대량으로 만들 수 있다는 점이다.

가능한 개선:

- 목록 페이지에서는 작성자 코드 보완을 하지 않는다.
- 작성자 코드가 목록 HTML에서 파싱될 때만 보여준다.
- 필요하다면 환경변수로 켜고 끌 수 있게 한다.
- 게시글 상세에 들어갔을 때만 정확한 작성자 코드를 보여준다.

효과:

```text
/board 1페이지: 리스트뷰 1회 + 게시글 최대 31회
→ 리스트뷰 1회 + 게시글 0회
```

### 2순위: 댓글 컨텍스트 재조회 줄이기

`API.document()`에서 이미 PC 게시글 HTML을 가져왔다면, 댓글에 필요한 `e_s_n_o`, `_GALLTYPE_` 같은 값을 함께 보관해서 `__get_pc_comment_context()`가 같은 게시글을 다시 요청하지 않도록 만들 수 있다.

효과:

```text
/read 1회: 게시글 본문 1회 + 댓글 컨텍스트 1회
→ 게시글 본문 1회
```

### 3순위: 관련글 자동 로딩 완화

현재 게시글 페이지가 열리면 관련글을 자동으로 불러온다.

가능한 개선:

- 관련글을 자동 로딩하지 않고 버튼 클릭 시 로딩한다.
- 관련글 탐색 범위를 줄인다.
- `RELATED_PAGE_PROBE_STEPS`, `RELATED_TAIL_PAGES`를 낮춘다.
- 관련글 캐시 시간을 늘린다.

효과:

```text
/read 1회: 관련글 리스트뷰 최대 12회
→ 자동 로딩 비활성화 시 0회
```

## 요약

현재 구조는 다음처럼 볼 수 있다.

| 화면 | 리스트뷰 제한 사용 | 게시글 제한 사용 |
|---|---:|---:|
| `/board` | 기본 1회 | 작성자 코드 누락 글 수만큼 추가 |
| `/read` | 본문만 보면 0회 | 본문 1회 + 댓글 컨텍스트 1회 안팎 |
| `/read` + 관련글 | 관련글 탐색으로 최대 12회 안팎 | 관련글 작성자 코드 보완 시 추가 |
| `/media` | 직접 관련 낮음 | 직접 관련 낮음 |

가장 주의해야 할 점은 두 가지다.

```text
목록 화면도 게시글 제한을 쓸 수 있다.
게시글 화면도 리스트뷰 제한을 쓸 수 있다.
```

현재 제한이 리스트뷰 약 40회/분, 게시글 약 30회/분이라면, 가장 먼저 손봐야 할 곳은 `/board`의 작성자 코드 보완 요청이다.
