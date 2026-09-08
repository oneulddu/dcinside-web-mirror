"""Regression checks for read snapshots, context isolation and verified list ends."""
import asyncio
from contextlib import asynccontextmanager

import lxml.html
import pytest

from app import create_app, routes
from app.services import core
from app.services.dc.api import API, BoardUnavailableError
from app.services.dc.models import DocumentIndex


def item(pid):
    return DocumentIndex(
        id=str(pid), board_id='test', title=f'post {pid}', has_image=False,
        author='test', author_id=None, time='-', view_count=0, comment_count=0,
        voteup_count=0, document=lambda: None, comments=lambda: None,
        subject=None, isimage=False, isrecommend=False, isdcbest=False, ishit=False,
    )


def payload(rows=None):
    return {
        'title': 'body', 'author': 'test', 'time': '-', 'voteup_count': 0,
        'html': '<p>body</p>', '_comments_complete': True,
        'related_posts': rows if rows is not None else [{'id': '99', 'title': 'original', 'comment_count': 0}],
    }, [], []


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    for name in ('_READ_CACHE', '_READ_STALE_CACHE', '_READ_INFLIGHT', '_INITIAL_RELATED_CACHE',
                 '_BOARD_PAGE_CACHE', '_BOARD_INFLIGHT', '_LATEST_ID_CACHE', '_CACHE_PRUNE_STATE'):
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(core, 'READ_CACHE_TTL', 30)
    monkeypatch.setattr(core, 'READ_STALE_TTL', 300)


class Pages:
    def __init__(self, pages, ends=None):
        self.pages = pages
        self.ends = ends or {}
        self.calls = []

    async def board(self, start_page=1, pagination_collector=None, **kwargs):
        self.calls.append((start_page, kwargs))
        value = self.pages.get(start_page, [])
        if isinstance(value, Exception):
            raise value
        if pagination_collector is not None:
            pagination_collector.update({'has_next': self.ends.get(start_page)})
        for pid in value:
            yield item(pid)


def use_api(monkeypatch, api):
    @asynccontextmanager
    async def context():
        yield api
    monkeypatch.setattr(core, 'dc_api_context', context)


@pytest.mark.asyncio
async def test_snapshot_survives_body_cache_and_comment_refresh_without_extra_scrape(monkeypatch):
    calls = []
    async def fresh(*args, **kwargs):
        calls.append(kwargs)
        return payload()
    monkeypatch.setattr(core, '_read_document_with_api', fresh)
    use_api(monkeypatch, object())
    first = await core.async_read('100', 'test')
    first[0]['related_posts'][0]['title'] = 'mutated'
    key = core._initial_related_key('100', 'test')
    expiry = core._INITIAL_RELATED_CACHE[key]['expires_at']
    assert expiry - core.time.time() > 290
    cached = await core.async_read('100', 'test')
    core._READ_CACHE.clear()  # Exercise the retained-body/comment refresh path.
    retained = await core.async_read('100', 'test')
    assert cached[0]['related_posts'] == retained[0]['related_posts'] == payload()[0]['related_posts']
    assert len(calls) == 1
    assert core._INITIAL_RELATED_CACHE[key]['expires_at'] == expiry
    assert next(iter(core._READ_STALE_CACHE.values()))['value'][0]['related_posts'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('context', [
    {'recommend': 1}, {'search_keyword': 'word'},
    {'search_type': 'memo', 'search_keyword': 'word'}, {'head_id': '7'},
])
async def test_cached_body_does_not_leak_owner_list_into_other_filters(monkeypatch, context):
    async def fresh(*args, **kwargs):
        return payload()
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    await core.async_read('100', 'test')
    other = await core.async_read('100', 'test', **context)
    assert other[0]['html'] == '<p>body</p>'
    assert other[0]['related_posts'] == []
    assert '_related_has_more' not in other[0]


@pytest.mark.asyncio
async def test_concurrent_waiters_attach_their_own_context_only(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def fresh(*args, **kwargs):
        started.set()
        await release.wait()
        return payload()
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    owner = asyncio.create_task(core.async_read('100', 'test', recommend=1))
    await started.wait()
    same = asyncio.create_task(core.async_read('100', 'test', recommend=1))
    other = asyncio.create_task(core.async_read('100', 'test', search_keyword='search'))
    await asyncio.sleep(0)
    release.set()
    a, b, c = await asyncio.gather(owner, same, other)
    assert a[0]['related_posts'] == b[0]['related_posts'] == payload()[0]['related_posts']
    assert c[0]['related_posts'] == []
    assert not core._READ_INFLIGHT


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.TimeoutError(), core.dc_api.DocumentUnavailableError('upstream')])
async def test_body_error_fallback_still_attaches_valid_context_snapshot(monkeypatch, failure):
    async def fresh(*args, **kwargs):
        return payload()
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    await core.async_read('100', 'test')
    core._READ_CACHE.clear()
    async def failing(*args, **kwargs):
        raise failure
    monkeypatch.setattr(core, '_load_read_payload', failing)
    result = await core.async_read('100', 'test')
    assert result[0]['_served_stale'] is True
    assert result[0]['related_posts'] == payload()[0]['related_posts']


@pytest.mark.asyncio
async def test_expired_list_does_not_refetch_body_or_extend_snapshot(monkeypatch):
    calls = []
    async def fresh(*args, **kwargs):
        calls.append(1)
        return payload()
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    await core.async_read('100', 'test')
    key = core._initial_related_key('100', 'test')
    core._INITIAL_RELATED_CACHE[key]['expires_at'] = 0
    result = await core.async_read('100', 'test')
    assert result[0]['related_posts'] == []
    assert key not in core._INITIAL_RELATED_CACHE
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('ids', [[100, 99, 98], [100]])
async def test_initial_endpoint_seeds_rows_or_confirmed_empty_for_next_read(monkeypatch, ids):
    api = Pages({1: ids}, {1: False})
    use_api(monkeypatch, api)
    async def fresh(*args, **kwargs):
        return payload([])
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    await core.async_read('100', 'test')
    rows, more = await core.async_related_after_position('100', 0, 'test', source_page=1)
    result = await core.async_read('100', 'test')
    assert result[0]['related_posts'] == rows
    assert result[0]['_related_has_more'] is more is False
    assert len(api.calls) == 1


@pytest.mark.asyncio
async def test_more_requests_do_not_replace_initial_snapshot(monkeypatch):
    api = Pages({1: [100, 99, 98]}, {1: False})
    use_api(monkeypatch, api)
    original = [{'id': '99', 'title': 'initial'}]
    key = core._initial_related_key('100', 'test')
    core._store_initial_related(key, original)
    await core.async_related_after_position('100', '99', 'test', source_page=1)
    assert core._INITIAL_RELATED_CACHE[key]['value'][0] == original


def test_snapshot_cache_is_bounded_and_unknown_empty_is_not_cached(monkeypatch):
    monkeypatch.setattr(core, 'READ_CACHE_MAX_ITEMS', 2)
    for pid in ('100', '101', '102'):
        core._store_initial_related(core._initial_related_key(pid, 'test'), [{'id': '99'}])
    assert len(core._INITIAL_RELATED_CACHE) == 2
    core._store_initial_related(('unknown',), [], None)
    assert ('unknown',) not in core._INITIAL_RELATED_CACHE


@pytest.mark.asyncio
@pytest.mark.parametrize('more', [None, True, False])
async def test_tail_limit_is_unknown_unless_end_is_verified(more):
    api = Pages({1: [100, 99]}, {1: more})
    rows, has_more = await core._related_after_position_with_api(
        api, '100', 0, 'test', source_page=1, limit=12, tail_pages=0,
    )
    assert [row['id'] for row in rows] == ['99']
    assert has_more is (False if more is False else None)


@pytest.mark.asyncio
async def test_missing_cursor_is_retryable_and_not_end():
    api = Pages({1: [200, 199]}, {1: False})
    with pytest.raises(core.RelatedPositionUnavailableError):
        await core._related_after_position_with_api(api, '100', 0, 'test', source_page=1, probe_steps=1)


@pytest.mark.asyncio
async def test_failed_source_hint_can_recover_from_another_candidate():
    api = Pages({9: BoardUnavailableError('hint failed'), 1: [100, 99]}, {1: False})
    rows, more = await core._related_after_position_with_api(api, '100', 0, 'test', source_page=9)
    assert [row['id'] for row in rows] == ['99']
    assert more is False


@pytest.mark.asyncio
async def test_tail_failure_is_retryable_without_caching_partial_initial_list(monkeypatch):
    api = Pages({1: [100, 99], 2: BoardUnavailableError('tail failed')}, {1: True})
    use_api(monkeypatch, api)
    with pytest.raises(BoardUnavailableError):
        await core.async_related_after_position('100', 0, 'test', source_page=1)
    assert core._INITIAL_RELATED_CACHE == {}
    api.pages[2] = [98]
    api.ends[2] = False
    rows, more = await core.async_related_after_position('100', 0, 'test', source_page=1)
    assert [row['id'] for row in rows] == ['99', '98']
    assert more is False


@pytest.mark.asyncio
async def test_page_cache_preserves_pagination_and_mutation_safety():
    api = Pages({1: [100, 99]}, {1: False})
    metadata = {}
    rows = await core._fetch_board_page(api, 1, 'test', 0, pagination_collector=metadata)
    metadata['has_next'] = True
    rows[0]['id'] = 'bad'
    cached_metadata = {}
    cached_rows = await core._fetch_board_page(api, 1, 'test', 0, pagination_collector=cached_metadata)
    assert cached_rows[0]['id'] == '100'
    assert cached_metadata['has_next'] is False
    assert len(api.calls) == 1


@pytest.mark.asyncio
async def test_shared_page_failure_preserves_error_class():
    started, release = asyncio.Event(), asyncio.Event()
    class Failing:
        async def board(self, **kwargs):
            started.set()
            await release.wait()
            raise BoardUnavailableError('failed')
            yield
    api = Failing()
    owner = asyncio.create_task(core._fetch_board_page(api, 1, 'test', 0))
    await started.wait()
    waiter = asyncio.create_task(core._fetch_board_page(api, 1, 'test', 0))
    await asyncio.sleep(0)
    release.set()
    errors = await asyncio.gather(owner, waiter, return_exceptions=True)
    assert all(isinstance(error, BoardUnavailableError) for error in errors)
    assert core._BOARD_INFLIGHT == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('strict', [False, True])
async def test_board_strict_failure_is_opt_in(strict):
    api = API.__new__(API)
    async def unavailable(*args, **kwargs):
        return None, '', None
    api._API__fetch_parsed_from_urls = unavailable
    if strict:
        with pytest.raises(BoardUnavailableError):
            _ = [row async for row in api.board('test', num=30, raise_on_failure=True)]
    else:
        assert [row async for row in api.board('test', num=30)] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('known_end', [False, True])
async def test_strict_empty_page_requires_end_evidence(known_end):
    api = API.__new__(API)
    async def empty(*args, **kwargs):
        return lxml.html.fromstring('<html><ul class="gall-detail-lst"></ul></html>'), '', 'https://m.dcinside.com/board/test?page=1'
    api._API__fetch_parsed_from_urls = empty
    api._API__parse_board_pagination = lambda *args: {'has_next': False if known_end else None}
    if known_end:
        assert [row async for row in api.board('test', num=30, raise_on_failure=True)] == []
    else:
        with pytest.raises(BoardUnavailableError):
            _ = [row async for row in api.board('test', num=30, raise_on_failure=True)]


@pytest.mark.asyncio
async def test_related_server_deadline_leaves_no_poisoned_snapshot(monkeypatch):
    use_api(monkeypatch, object())
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(core, '_related_after_position_with_api', slow)
    monkeypatch.setattr(core, 'RELATED_FETCH_TIMEOUT', 0.01)
    with pytest.raises(asyncio.TimeoutError):
        await core.async_related_after_position('100', 0, 'test')
    assert core._INITIAL_RELATED_CACHE == {}


@pytest.mark.parametrize('error,code', [
    (BoardUnavailableError('upstream'), 'related_fetch_failed'),
    (core.RelatedPositionUnavailableError('not found'), 'related_position_unavailable'),
])
def test_related_route_errors_are_retryable_and_never_terminal(monkeypatch, error, code):
    async def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(routes, 'async_related_after_position', fail)
    response = create_app().test_client().get('/read/related?board=test&pid=100')
    assert response.status_code == 502
    assert response.json == {'ok': False, 'items': [], 'error': code}
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Retry-After'] == '3'


def test_related_route_preserves_unknown_more(monkeypatch):
    async def unknown(*args, **kwargs):
        return [], None
    monkeypatch.setattr(routes, 'async_related_after_position', unknown)
    response = create_app().test_client().get('/read/related?board=test&pid=100')
    assert response.status_code == 200
    assert response.json == {'ok': True, 'items': [], 'has_more': None}


def test_read_route_renders_repeated_snapshot_without_list_network_calls(monkeypatch):
    calls = []
    async def fresh(*args, **kwargs):
        calls.append(1)
        return payload()
    monkeypatch.setattr(core, '_load_read_payload', fresh)
    client = create_app().test_client()
    for _ in range(2):
        response = client.get('/read?board=test&pid=100&source_page=2')
        assert response.status_code == 200
        tree = lxml.html.fromstring(response.data)
        assert tree.xpath('//*[@id="related-list"]//a[@data-post-id="99"]')
    assert len(calls) == 1
    other = client.get('/read?board=test&pid=100&recommend=1')
    assert not lxml.html.fromstring(other.data).xpath('//*[@id="related-list"]//a')


@pytest.mark.asyncio
async def test_filtered_search_without_page_hint_does_not_estimate_from_sparse_ids():
    api = Pages({1: [1000000, 100, 99]}, {1: False})
    rows, more = await core._related_after_position_with_api(
        api, '100', 0, 'test', search_type='subject', search_keyword='word',
    )
    assert [row['id'] for row in rows] == ['99']
    assert more is False
    assert [page for page, _ in api.calls] == [1]
