"""Origin failures must not erase lists or keep request workers waiting forever."""

import asyncio
from contextlib import asynccontextmanager

import lxml.html
import pytest

from app import create_app, routes
from app.services import core
from app.services.dc.api import API, BoardUnavailableError


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    for name in ("_BOARD_INDEX_CACHE", "_BOARD_PAGE_CACHE", "_BOARD_REFRESH_CACHE",
                 "_BOARD_TIME_CACHE", "_BOARD_INFLIGHT", "_LATEST_ID_CACHE",
                 "_AUTHOR_CODE_CACHE", "_INITIAL_RELATED_CACHE", "_CACHE_PRUNE_STATE"):
        monkeypatch.setattr(core, name, {})


@pytest.fixture
def origin(monkeypatch):
    # Use the real API parser and failure classification, without network I/O.
    state = {"failure": False, "empty": False, "slow": False, "calls": 0, "cancelled": 0}
    api = API.__new__(API)

    async def fetch(*args, **kwargs):
        state["calls"] += 1
        if state["slow"]:
            try:
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                state["cancelled"] += 1
                raise
        if state["failure"]:
            return None, "", None
        rows = "" if state["empty"] else """
            <tr class="ub-content us-post" data-no="100">
              <td class="gall_tit"><a href="/board/view/?id=test&no=100">정상 글</a></td>
              <td class="gall_writer" data-nick="익명"></td>
              <td class="gall_date" title="2026.09.23 12:00:00"></td>
            </tr>
        """
        markup = f'<table>{rows}</table><div class="bottom_paging_box"><em>1</em></div>'
        return lxml.html.fromstring(markup), markup, "https://gall.dcinside.com/board/lists/?id=test&page=1"

    api._API__fetch_parsed_from_urls = fetch

    @asynccontextmanager
    async def context():
        yield api

    monkeypatch.setattr(core, "dc_api_context", context)
    monkeypatch.setattr(routes, "run_async", asyncio.run)
    return state


@pytest.mark.asyncio
async def test_failed_refresh_preserves_rows_pagination_and_cache_expiry(origin):
    pagination = {}
    before = await core.async_index_with_head_categories(1, "test", 0, pagination_collector=pagination)
    entry = next(iter(core._BOARD_INDEX_CACHE.values()))
    expiry = entry["expires_at"]
    origin["failure"] = True
    refreshed_pagination = {}
    after = await core.async_index_with_head_categories(
        1, "test", 0, force_refresh=True, pagination_collector=refreshed_pagination,
    )
    assert after == before
    assert refreshed_pagination == pagination
    assert entry["expires_at"] == expiry
    calls_after_refresh = origin["calls"]
    after[0][0]["title"] = "caller mutation"
    assert await core.async_index_with_head_categories(1, "test", 0) == before
    assert origin["calls"] == calls_after_refresh


@pytest.mark.asyncio
async def test_real_empty_refresh_replaces_old_rows(origin):
    assert (await core.async_index_with_head_categories(1, "test", 0))[0]
    origin["empty"] = True
    assert await core.async_index_with_head_categories(1, "test", 0, force_refresh=True) == ([], [])
    assert await core.async_index_with_head_categories(1, "test", 0) == ([], [])
    assert origin["calls"] == 2


@pytest.mark.parametrize("query", ["", "&refresh=1"])
def test_unavailable_board_is_retryable_not_empty_success(origin, query):
    origin["failure"] = True
    response = create_app().test_client().get("/board?board=test" + query)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "3"
    assert response.headers["Cache-Control"] == "no-store"
    assert core._BOARD_INDEX_CACHE == {}


@pytest.mark.parametrize("path, status", [("/board?board=test", 503), ("/board/times?board=test", 502)])
def test_board_deadline_cancels_origin_without_caching(origin, monkeypatch, path, status):
    monkeypatch.setattr(core, "BOARD_FETCH_TIMEOUT", 0.01, raising=False)
    origin["slow"] = True
    response = create_app().test_client().get(path)
    assert response.status_code == status
    assert origin["cancelled"] == 1
    assert core._BOARD_INDEX_CACHE == core._BOARD_TIME_CACHE == core._BOARD_INFLIGHT == {}
    origin["slow"] = False
    assert create_app().test_client().get(path).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["index", "times"])
async def test_shared_deadline_notifies_all_waiters_and_allows_retry(origin, monkeypatch, lookup):
    monkeypatch.setattr(core, "BOARD_FETCH_TIMEOUT", 0.01)
    origin["slow"] = True
    fetch = core.async_index_with_head_categories if lookup == "index" else core.async_board_precise_times
    results = await asyncio.gather(*(fetch(1, "test", 0) for _ in range(8)), return_exceptions=True)
    assert all(isinstance(result, BoardUnavailableError) for result in results)
    assert origin["calls"] == origin["cancelled"] == 1
    assert core._BOARD_INDEX_CACHE == core._BOARD_TIME_CACHE == core._BOARD_INFLIGHT == {}
    origin["slow"] = False
    assert await fetch(1, "test", 0)


@pytest.mark.asyncio
async def test_refresh_timeout_keeps_valid_cache_without_extending_it(origin, monkeypatch):
    monkeypatch.setattr(core, "BOARD_FETCH_TIMEOUT", 0.01)
    before = await core.async_index_with_head_categories(1, "test", 0)
    entry = next(iter(core._BOARD_INDEX_CACHE.values()))
    expiry = entry["expires_at"]
    origin["slow"] = True
    assert await core.async_index_with_head_categories(1, "test", 0, force_refresh=True) == before
    assert next(iter(core._BOARD_INDEX_CACHE.values()))["expires_at"] == expiry
    assert core._BOARD_INFLIGHT == {}


@pytest.mark.asyncio
async def test_failed_refresh_never_resurrects_expired_rows(origin):
    await core.async_index_with_head_categories(1, "test", 0)
    next(iter(core._BOARD_INDEX_CACHE.values()))["expires_at"] = 0
    origin["failure"] = True
    with pytest.raises(BoardUnavailableError):
        await core.async_index_with_head_categories(1, "test", 0, force_refresh=True)
    assert core._BOARD_INDEX_CACHE == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("known_end", [False, True])
async def test_index_empty_page_does_not_require_pagination_evidence(known_end):
    api = API.__new__(API)

    async def empty(*args, **kwargs):
        return lxml.html.fromstring('<ul class="gall-detail-lst"></ul>'), "", "https://m.dcinside.com/board/test"

    api._API__fetch_parsed_from_urls = empty
    api._API__parse_board_pagination = lambda *args: {"has_next": False if known_end else None}
    assert [row async for row in api.board("test", raise_on_unavailable=True)] == []
