"""Search segment boundaries must not reuse page offsets from another segment."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode, urlparse

import lxml.html
import pytest

from app import create_app, routes
from app.services import core
from app.services.dc import api as dc_api
from app.services.dc.api import API, BoardUnavailableError
from app.services.dc.search import normalize_search_pos


def upstream(page, pos=None, **extra):
    return 'https://m.dcinside.com/board/test?' + urlencode({
        'page': page, 's_type': 'subject_m', 'serval': 'word',
        **({'s_pos': pos} if pos is not None else {}), **extra,
    })


def nav(page, links):
    return '<div id="pagination_div"><div class="paging-inner"><strong>' + str(page) + '</strong>' + ''.join(
        f'<a class="{kind}" href="{url}">{kind or "page"}</a>' for kind, url in links
    ) + '</div></div>'


def markup(page, ids, links):
    rows = ''.join(f'''<li><a class="lt" href="https://m.dcinside.com/board/test/{pid}">
      <span class="subjectin">post {pid}</span><ul class="ginfo"><li>일반</li><li>author</li>
      <li>00:01</li><li>조회 1</li><li>추천 0</li></ul></a></li>''' for pid in ids)
    if not ids:
        rows = '<li class="empty">등록된 게시물이 없습니다.</li>'
    return '<html><body><ul class="gall-detail-lst">' + rows + '</ul>' + nav(page, links) + '</body></html>'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ('_BOARD_INDEX_CACHE', '_BOARD_PAGE_CACHE', '_BOARD_TIME_CACHE', '_BOARD_INFLIGHT',
                 '_READ_CACHE', '_READ_STALE_CACHE', '_READ_INFLIGHT', '_INITIAL_RELATED_CACHE',
                 '_LATEST_ID_CACHE', '_AUTHOR_CODE_CACHE', '_CACHE_PRUNE_STATE'):
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(dc_api, '_BOARD_KIND_CACHE', {})
    monkeypatch.setattr(core, 'BOARD_FILL_AUTHOR_CODES', False)
    monkeypatch.setattr(routes, 'run_async', asyncio.run)


@pytest.fixture
def origin(monkeypatch):
    api = API.__new__(API)
    calls = []
    async def request(method, url, **kwargs):
        calls.append(url)
        assert urlparse(url).hostname == 'm.dcinside.com'
        q = parse_qs(urlparse(url).query)
        pos = q.get('s_pos', [''])[0]
        page = int(q.get('page', ['1'])[0])
        if pos == '-100':
            ids = [80, 79] if page == 1 else []
            links = [('prev', upstream(1, '')), ('', upstream(1, pos)), ('', upstream(2, pos)),
                     ('next', upstream(1, '-90'))]
            current = min(page, 2)
        elif pos == '-90':
            ids, links, current = [70], [('prev', upstream(1, '-100'))], 1
        else:
            current = min(page, 2)
            ids = [100, 99] if current == 1 else [90]
            links = [('', upstream(1)), ('', upstream(2)), ('next', upstream(1, '-100'))]
        return 200, {}, markup(current, ids, links)
    api._API__request_text = request
    @asynccontextmanager
    async def context():
        yield api
    monkeypatch.setattr(core, 'dc_api_context', context)
    return api, calls


@pytest.mark.parametrize(('value', 'expected'), [('-123', '-123'), ('0002', '2'), ('0', '0'),
    ('', None), ('cursor', None), ('1&x=2', None), ('1'*21, None), ('１２', None)])
def test_cursor_validation(value, expected):
    assert normalize_search_pos(value) == expected


def test_parser_uses_adjacent_page_then_segment_and_inherits_cursor():
    api = API.__new__(API)
    links = [('', upstream(1, '-200')), ('', upstream(2)), ('next', upstream(1, '-100'))]
    state = api._API__parse_board_pagination(lxml.html.fromstring(nav(1, links)), upstream(1, '-200'))
    assert (state['next_page'], state['next_search_pos']) == (2, '-200')
    state = api._API__parse_board_pagination(lxml.html.fromstring(nav(2, links)), upstream(2, '-200'))
    assert (state['next_page'], state['next_search_pos']) == (1, '-100')
    assert (state['prev_page'], state['prev_search_pos']) == (1, '-200')
    assert state['has_next'] is True


@pytest.mark.parametrize('target', [upstream(1, 'bad'), upstream(1, '-1').replace('/test?', '/other?'),
    upstream(1, '-1').replace('m.dcinside.com', 'evil.example'), upstream(1, '-1', serval='other'),
    upstream(1, '-1')+'&s_pos=-2', 'javascript:alert(1)', 'https://m.dcinside.com:bad/board/test'])
def test_parser_rejects_untrusted_or_ambiguous_targets(target):
    api = API.__new__(API)
    state = api._API__parse_board_pagination(lxml.html.fromstring(nav(1, [('next', target)])), upstream(1))
    assert 'next_page' not in state
    assert state['has_next'] is False


def test_upstream_cursor_mapping_and_reset():
    api = API.__new__(API)
    urls = api._API__build_list_urls('test', 1, search_keyword='word', search_pos='-123')
    assert parse_qs(urlparse(urls[0]).query)['s_pos'] == ['-123']
    assert parse_qs(urlparse(urls[1]).query)['search_pos'] == ['-123']
    for kwargs in ({}, {'notice': True, 'search_keyword': 'word'}):
        for url in api._API__build_list_urls('test', 1, search_pos='-123', **kwargs):
            assert not {'s_pos', 'search_pos'} & parse_qs(urlparse(url).query).keys()


@pytest.mark.asyncio
async def test_api_segment_boundary_and_empty_segment(origin):
    api, calls = origin
    first = [p.id async for p in api.board('test', start_page=2, max_scan_pages=1, search_keyword='word')]
    state = {}
    second = [p.id async for p in api.board('test', max_scan_pages=1, search_keyword='word', search_pos='-100', pagination_collector=state)]
    assert first == ['90'] and second == ['80', '79']
    assert not set(first) & set(second)
    assert state['prev_search_pos'] == ''
    empty = [p async for p in api.board('test', start_page=2, max_scan_pages=1, search_keyword='word', search_pos='-100', pagination_collector=state, raise_on_failure=True)]
    assert empty == [] and state['has_next'] is True and state['next_search_pos'] == '-90'
    assert all(urlparse(u).hostname == 'm.dcinside.com' for u in calls)


@pytest.mark.asyncio
async def test_search_failure_does_not_switch_to_incompatible_pc_pages(origin):
    api, calls = origin
    async def failed(method, url, **kwargs):
        calls.append(url)
        return 503, {}, ''
    api._API__request_text = failed
    with pytest.raises(BoardUnavailableError):
        _ = [p async for p in api.board('test', start_page=2, search_keyword='word', raise_on_unavailable=True)]
    assert len(calls) == 1 and urlparse(calls[0]).hostname == 'm.dcinside.com'


@pytest.mark.asyncio
async def test_cache_and_related_rows_are_segment_specific(origin):
    api, calls = origin
    first = await core._fetch_board_page(api, 1, 'test', 0, search_keyword='word')
    second = await core._fetch_board_page(api, 1, 'test', 0, search_keyword='word', search_pos='-100')
    assert first[0]['id'] == '100' and second[0]['id'] == '80'
    assert first[0]['search_pos'] == '' and second[0]['search_pos'] == '-100'
    await core._fetch_board_page(api, 1, 'test', 0, search_keyword='word', search_pos='-100')
    assert len(calls) == 2
    rows, more = await core._related_after_position_with_api(api, '90', 0, 'test', source_page=2, search_keyword='word', limit=1)
    assert rows[0]['id'] == '80' and rows[0]['search_pos'] == '-100' and rows[0]['source_page'] == 1
    assert more is True
    again, more = await core._related_after_position_with_api(api, '90', '80', 'test', source_page=1, search_keyword='word', search_pos='-100', limit=1)
    assert again[0]['id'] == '79' and again[0]['search_pos'] == '-100'


@pytest.mark.asyncio
async def test_related_skips_empty_page_to_next_segment_with_default_tail_limit(origin):
    api, calls = origin
    rows, more = await core._related_after_position_with_api(
        api, '80', '79', 'test', source_page=1,
        search_keyword='word', search_pos='-100', limit=12,
    )
    assert [(row['id'], row['source_page'], row['search_pos']) for row in rows] == [('70', 1, '-90')]
    assert more is False
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_related_empty_segment_cycle_is_bounded(origin):
    api, calls = origin
    original = api._API__request_text
    async def cyclic(method, url, **kwargs):
        q = parse_qs(urlparse(url).query)
        if q.get('s_pos') == ['-90']:
            calls.append(url)
            return 200, {}, markup(1, [], [('next', upstream(2, '-100'))])
        return await original(method, url, **kwargs)
    api._API__request_text = cyclic
    rows, more = await core._related_after_position_with_api(
        api, '80', '79', 'test', source_page=1,
        search_keyword='word', search_pos='-100', limit=12,
    )
    assert rows == [] and more is False
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_clamped_search_page_returns_no_rows_and_is_not_cached(origin):
    meta = {}
    rows, _ = await core.async_index_with_head_categories(3, 'test', 0, search_keyword='word', pagination_collector=meta)
    assert rows == [] and meta['current_page'] == 2 and meta['search_clamped']
    assert not core._BOARD_INDEX_CACHE
    rows = await core._fetch_board_page(origin[0], 3, 'test', 0, search_keyword='word', pagination_collector=meta)
    assert rows == [] and not core._BOARD_PAGE_CACHE


def query(url):
    return parse_qs(urlparse(url).query)


def test_real_board_routes_navigate_segments_and_reset_new_search(origin):
    client = create_app().test_client()
    response = client.get('/board?board=test&page=2&serval=word')
    tree = lxml.html.fromstring(response.data)
    next_url = tree.xpath('//a[contains(@class,"pager-btn") and contains(.,"다음")]/@href')[0]
    assert query(next_url)['search_pos'] == ['-100'] and query(next_url)['page'] == ['1']
    tree = lxml.html.fromstring(client.get(next_url).data)
    post_url = tree.xpath('//a[contains(@class,"feed-item")]/@href')[0]
    assert query(post_url)['search_pos'] == ['-100']
    prev_url = tree.xpath('//a[contains(@class,"pager-btn") and contains(.,"이전")]/@href')[0]
    assert 'search_pos' not in query(prev_url)
    assert not tree.xpath('//form//input[@name="search_pos"]')
    assert all('search_pos' not in query(u) for u in tree.xpath('//a[contains(@class,"tab-item")]/@href'))
    redirect = client.get('/board?board=test&page=3&serval=word')
    assert redirect.status_code == 302 and query(redirect.location)['page'] == ['2']
    assert client.get(redirect.location).status_code == 200


def test_read_return_and_related_requests_keep_cursor(monkeypatch):
    seen = []
    async def read(*args, **kwargs):
        seen.append(kwargs)
        return {'title':'test','author':'test','time':'-','html':'<p>body</p>','voteup_count':0,'related_posts':[]}, [], []
    async def related(*args, **kwargs):
        seen.append(kwargs)
        return [{'id':'79','title':'next','search_pos':'-90','source_page':1}], False
    monkeypatch.setattr(routes, 'async_read', read)
    monkeypatch.setattr(routes, 'async_related_after_position', related)
    client = create_app().test_client()
    response = client.get('/read?board=test&pid=80&source_page=2&serval=word&search_pos=-100')
    assert response.status_code == 200
    tree = lxml.html.fromstring(response.data)
    back = tree.xpath('//a[contains(@class,"pager-btn") and contains(.,"목록으로")]/@href')[0]
    assert query(back)['search_pos'] == ['-100'] and query(back)['page'] == ['2']
    result = client.get('/read/related?board=test&pid=80&source_page=2&serval=word&search_pos=-100').json
    assert all(k['search_pos'] == '-100' for k in seen)
    assert result['items'][0]['search_pos'] == '-90'


def test_cursor_keys_keep_legacy_default_and_isolate_snapshots():
    first = core._board_index_cache_key(1,'test',0,search_keyword='word')
    assert first == core._board_index_cache_key(1,'test',0,search_keyword='word',search_pos='bad')
    assert first != core._board_index_cache_key(1,'test',0,search_keyword='word',search_pos='-100')
    assert core._board_index_cache_key(1,'test',0) == core._board_index_cache_key(1,'test',0,search_pos='-100')
    assert core._initial_related_key('80','test',search_keyword='word') != core._initial_related_key('80','test',search_keyword='word',search_pos='-100')
