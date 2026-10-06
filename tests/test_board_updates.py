"""Updates snapshots reuse lists without enrichment or recent-gallery writes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import threading

import lxml.html
import pytest

from app import create_app, routes
from app.services import core
from app.services.dc.api import API, BoardUnavailableError


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    for name in ("_BOARD_INDEX_CACHE", "_BOARD_PAGE_CACHE", "_BOARD_UPDATES_CACHE",
                 "_BOARD_INFLIGHT", "_CACHE_PRUNE_STATE", "_AUTHOR_CODE_CACHE"):
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(core, "BOARD_PAGE_CACHE_TTL", 20)
    monkeypatch.setattr(routes, "run_async", asyncio.run)


@pytest.fixture
def origin(monkeypatch):
    state = {"calls": [], "failure": False, "empty": False, "delay": 0,
             "has_next": False, "now": 1000.0}
    monkeypatch.setattr(core.time, "time", lambda: state["now"])
    api = API.__new__(API)

    async def fetch(*args, **kwargs):
        await asyncio.sleep(state["delay"])
        if state["failure"]:
            return None, "", None
        rows = "" if state["empty"] else '''
            <tr class="ub-content us-post" data-no="100">
              <td class="gall_tit"><a href="/board/view/?id=test&no=100">새 글</a></td>
              <td class="gall_writer" data-nick="작성자" data-uid="writer"></td>
              <td class="gall_date" title="2026.10.06 12:00:00"></td>
            </tr>
        '''
        # The real list parser excludes the pinned notice.
        notice = '''<tr class="ub-content ub-notice" data-no="999">
            <td class="gall_num">공지</td>
            <td class="gall_tit"><a href="/board/view/?id=test&no=999">공지</a></td>
            <td class="gall_writer" data-nick="관리자"></td></tr>'''
        markup = f'<table>{notice}{rows}</table><div class="bottom_paging_box"><em>1</em></div>'
        return lxml.html.fromstring(markup), markup, "https://gall.dcinside.com/board/lists/?id=test&page=1"

    api._API__fetch_parsed_from_urls = fetch
    real_board = api.board

    async def board(**kwargs):
        state["calls"].append(kwargs.copy())
        async for item in real_board(**kwargs):
            yield item
        kwargs["pagination_collector"]["has_next"] = state["has_next"]

    api.board = board

    async def forbidden(*args, **kwargs):
        pytest.fail("updates must not enrich a list")

    api.document = api.comments = forbidden
    monkeypatch.setattr(core, "_fill_missing_author_codes", forbidden)
    monkeypatch.setattr(core, "async_board_precise_times", forbidden)
    monkeypatch.setattr(core, "BOARD_FILL_AUTHOR_CODES", True)

    @asynccontextmanager
    async def context():
        yield api

    monkeypatch.setattr(core, "dc_api_context", context)
    return state


@pytest.fixture
def client():
    return create_app().test_client()


def assert_headers(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Set-Cookie" not in response.headers


def test_success_shape_page_one_no_enrichment_or_cookie(client, origin):
    response = client.get("/board/updates?board=test&kind=minor")
    assert response.status_code == 200
    assert response.json == {
        "ok": True, "board": "test", "kind": "minor", "fetched_at": 1000.0,
        "has_next": False,
        "items": [{"id": "100", "title": "새 글", "author": "작성자", "author_code": "writer"}],
    }
    assert_headers(response)
    assert len(origin["calls"]) == 1
    call = origin["calls"][0]
    assert (call["start_page"], call["max_scan_pages"], call["recommend"]) == (1, 1, 0)
    assert call["head_id"] is call["search_type"] is call["search_keyword"] is None


@pytest.mark.parametrize("has_next", [True, False, None])
def test_pagination_tristate_and_normalization(client, origin, has_next):
    origin["has_next"] = has_next
    response = client.get("/board/updates?board=%20test%20&kind=NORMAL")
    assert response.status_code == 200
    assert response.json["kind"] is None
    assert response.json["board"] == "test"
    assert response.json["has_next"] is has_next


def test_confirmed_empty_is_cached_success(client, origin):
    origin["empty"] = True
    for _ in range(2):
        response = client.get("/board/updates?board=test")
        assert response.status_code == 200
        assert response.json["items"] == []
        assert_headers(response)
    assert len(origin["calls"]) == 1


def test_failure_headers_and_failure_cache_expiry(client, origin):
    origin["failure"] = True
    for _ in range(2):
        response = client.get("/board/updates?board=test")
        assert response.status_code == 503
        assert response.json == {"ok": False, "error": "board_updates_unavailable"}
        assert response.headers["Retry-After"] == "60"
        assert_headers(response)
    assert len(origin["calls"]) == 1
    origin.update(failure=False, now=1061.0)
    assert client.get("/board/updates?board=test").status_code == 200
    assert len(origin["calls"]) == 2


@pytest.mark.parametrize("query", ["", "board=", "board=%20", "board=../test",
    "board=test&kind=invalid", "board=test&page=1", "board=test&search=x",
    "board=test&s_type=subject_m", "board=test&s_keyword=x", "board=test&headid=1",
    "board=test&recommend=0", "board=test&filter=x", "board=test&refresh=1",
    "board=test&board=other", "board=test&kind=minor&kind=mini"])
def test_invalid_requests_never_fetch(client, origin, query):
    response = client.get("/board/updates?" + query)
    assert response.status_code == 400
    assert response.is_json and response.json["ok"] is False
    assert_headers(response)
    assert origin["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["index", "route_index", "page"])
async def test_reuses_existing_list_and_preserves_fetch_time(monkeypatch, origin, source):
    if source in {"index", "route_index"}:
        async def no_backfill(*args, **kwargs):
            pass
        monkeypatch.setattr(core, "_fill_missing_author_codes", no_backfill)
        if source == "route_index":
            await routes._load_board_payload(1, "test", 0, search_type="subject_m", search_keyword="")
        else:
            await core.async_index_with_head_categories(1, "test", 0, max_scan_pages=1)
    else:
        async with core.dc_api_context() as api:
            await core._fetch_board_page(api, 1, "test", 0)
    origin["now"] = 1010.0
    first = await core.async_board_updates("test")
    origin["now"] = 1030.0  # Source cache expired; snapshot remains valid.
    second = await core.async_board_updates("test")
    assert first == second
    assert first["fetched_at"] == 1000.0
    assert len(origin["calls"]) == 1
    first["items"][0]["title"] = "caller edit"
    assert (await core.async_board_updates("test"))["items"][0]["title"] == "새 글"
    origin["now"] = 1071.0
    assert (await core.async_board_updates("test"))["fetched_at"] == 1071.0
    assert len(origin["calls"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_concurrent_callers_share_fetch(origin, failure):
    origin.update(delay=0.03, failure=failure)
    results = await asyncio.gather(*(core.async_board_updates("test") for _ in range(8)),
                                   return_exceptions=True)
    assert len(origin["calls"]) == 1
    if failure:
        assert all(isinstance(result, BoardUnavailableError) for result in results)
    else:
        assert all(result == results[0] for result in results)
    assert core._BOARD_INFLIGHT == {}


def test_concurrent_http_requests_across_threads(origin):
    origin["delay"] = 0.05
    app = create_app()
    barrier = threading.Barrier(4)

    def request():
        with app.test_client() as client:
            barrier.wait(timeout=5)
            return client.get("/board/updates?board=test")

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: request(), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len(origin["calls"]) == 1


@pytest.mark.asyncio
async def test_cache_is_bounded_and_keys_include_kind(monkeypatch, origin):
    monkeypatch.setattr(core, "BOARD_UPDATES_CACHE_MAX_ITEMS", 2)
    for kind in (None, "minor", "mini"):
        assert (await core.async_board_updates("test", kind))["kind"] == kind
    assert len(origin["calls"]) == 3
    assert len(core._BOARD_UPDATES_CACHE) == 2


def test_ambiguous_empty_is_unavailable(client, origin):
    origin.update(empty=True, has_next=None)
    assert client.get("/board/updates?board=test").status_code == 503


@pytest.mark.asyncio
async def test_timeout_is_failure_cached(monkeypatch, origin):
    monkeypatch.setattr(core, "BOARD_FETCH_TIMEOUT", 0.01)
    origin["delay"] = 0.1
    for _ in range(2):
        with pytest.raises(BoardUnavailableError):
            await core.async_board_updates("test")
    assert len(origin["calls"]) == 1
    assert core._BOARD_INFLIGHT == {}
