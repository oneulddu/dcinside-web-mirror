from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import lxml.html
import pytest

from app import create_app, routes
from app.services import core
from app.services.dc import api as dc_api
from app.services.dc.api import API, BoardUnavailableError


def mobile_row(pid=1, board='airforce', extra='', classes='notice'):
    return f'<li class="{classes}"><a class="lt" href="/board/{board}/{pid}"><span class="subjectin">공지 {pid}</span>{extra}</a></li>'


def mobile_page(rows='', pager=''):
    return f'<html><body><ul id="notice_list">{rows}</ul>{pager}</body></html>'


def pc_row(pid=1, board='airforce', classes='ub-content', notice=True, extra=''):
    marker = 'icon_notice' if notice else 'icon_txt'
    return f'''<tr class="{classes}" data-type="{marker}" data-no="{pid}">
        <td class="gall_num">{'공지' if notice else pid}</td>
        <td class="gall_tit"><a href="/board/view/?id={board}&amp;no={pid}">공지 {pid}</a></td>{extra}</tr>'''


def pc_page(rows='', pager='<div class="bottom_paging_box"><em>1</em></div>'):
    return f'<html><body><table class="gall_list"><tbody>{rows}</tbody></table>{pager}</body></html>'


def query(url):
    return parse_qs(urlparse(url).query)


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    monkeypatch.setattr(dc_api, '_BOARD_KIND_CACHE', {})
    for name in ('_BOARD_INDEX_CACHE', '_BOARD_REFRESH_CACHE', '_BOARD_PAGE_CACHE',
                 '_LATEST_ID_CACHE', '_INITIAL_RELATED_CACHE', '_READ_CACHE', '_READ_STALE_CACHE'):
        monkeypatch.setattr(core, name, {})


def fake_api(html, mobile=True):
    api = API.__new__(API)
    calls = []

    async def request(method, url, **kwargs):
        calls.append(url)
        if mobile or urlparse(url).hostname == 'gall.dcinside.com':
            return 200, {}, html
        return 200, {}, '<html>unusable mobile page</html>'

    api._API__request_text = request
    return api, calls


@pytest.mark.asyncio
async def test_mobile_notices_keep_all_rows_without_fabricating_metadata():
    api, calls = fake_api(mobile_page(''.join(mobile_row(pid) for pid in range(1, 41))))
    pagination = {}
    items = [item async for item in api.board('airforce', notice=True, num=31, start_page=7,
                                            pagination_collector=pagination)]
    assert len(items) == 40
    assert len(calls) == 1
    assert pagination == {'requested_page': 7, 'current_page': 1, 'has_next': False}
    item = items[0]
    assert item.is_notice
    assert (item.author, item.time, item.time_text, item.voteup_count, item.view_count, item.comment_count) == (None,) * 6
    row = core._index_item_to_dict(item)
    assert row['author'] is row['time'] is row['voteup_count'] is None
    assert row['is_notice'] is True
    assert row['needs_time_hydrate'] is False


@pytest.mark.asyncio
async def test_mobile_notice_metadata_and_same_board_validation():
    metadata = '<ul class="ginfo"><li>관리자</li><li>2026.10.06</li><li>조회 20</li><li>추천 3</li></ul>'
    rows = mobile_row(1, extra=metadata) + mobile_row(2, board='other')
    rows += '<li class="notice"><a href="https://poll.dcinside.com/board/airforce/3">설문</a></li>'
    rows += mobile_row(4, classes='notice ad') + mobile_row(5, classes='notice survey')
    api, _ = fake_api(mobile_page(rows))
    items = [item async for item in api.board('airforce', notice=True)]
    assert [item.id for item in items] == ['1']
    assert (items[0].author, items[0].view_count, items[0].voteup_count) == ('관리자', 20, 3)
    assert items[0].time.year == 2026


@pytest.mark.asyncio
async def test_mobile_notice_strips_badge_and_reads_hidden_register_date():
    # 실제 m.dcinside.com 공지 목록 구조: 배지 span + 숨은 등록일 input, 작성자 정보 없음.
    row = ('<li class="notice"><input id="notify_newday" name="notify_newday" type="hidden" value="2008,11,27">'
           '<div class="detail-top-lnk"><a href="https://m.dcinside.com/board/airforce/1" class="lt ">'
           '<span class="round ntc-line-orange"> 공지 </span>공군 갤러리 이용 안내 </a>'
           '<a href="https://m.dcinside.com/board/airforce/1#comment_box" class="rt"><span class="ct">340</span></a>'
           '</div></li>')
    survey = ('<li class="survey"><div class="detail-top-lnk"><a href="https://event.dcinside.com/survey/vote/?no=384" '
              'class="lt red"><span class="round ntc-line-blue"> 설문 </span>설문 제목</a></div></li>')
    api, _ = fake_api(mobile_page(survey + row))
    items = [item async for item in api.board('airforce', notice=True)]
    assert [item.id for item in items] == ['1']
    assert items[0].title == '공군 갤러리 이용 안내'
    assert items[0].author is None
    assert items[0].comment_count == 340
    assert (items[0].time.year, items[0].time.month, items[0].time.day) == (2008, 11, 27)


@pytest.mark.asyncio
async def test_pc_notice_includes_row_without_us_post_and_excludes_survey():
    rows = pc_row(334857, classes='ub-content us-post') + pc_row(1)
    rows += pc_row(2, board='other')
    rows += pc_row(3).replace('>공지</td>', '>설문</td>')
    rows += pc_row(4).replace('/board/view/?id=airforce', 'https://evil.example/board/view/?id=airforce')
    api, calls = fake_api(pc_page(rows), mobile=False)
    items = [item async for item in api.board('airforce', notice=True, num=1)]
    assert [item.id for item in items] == ['334857', '1']
    assert all(item.is_notice and item.author is None and item.time is None and item.voteup_count is None for item in items)
    assert query(calls[1])['exception_mode'] == ['notice']
    assert query(calls[1])['list_num'] == ['30']


@pytest.mark.parametrize('html', [mobile_page(), mobile_page('<li>등록된 공지가 없습니다.</li>'), pc_page('<tr><td>등록된 게시물이 없습니다.</td></tr>')])
@pytest.mark.asyncio
async def test_confirmed_empty_notice_page(html):
    api, _ = fake_api(html)
    pagination = {}
    assert [row async for row in api.board('airforce', notice=True, pagination_collector=pagination)] == []
    assert pagination['current_page'] == 1
    assert pagination['has_next'] is False


@pytest.mark.parametrize('html', [
    '<html>challenge</html>', '<p>등록된 게시물이 없습니다.</p>',
    mobile_page('<li>changed structure</li>'),
    mobile_page('<li class="notice">missing link</li>'),
    mobile_page('<div><a href="/board/airforce/1"></a></div>'),
    mobile_page(mobile_row().replace('/airforce/1', '/airforce/bad')),
    pc_page(pc_row().replace('no=1', 'no=bad')),
    pc_page('<tr><td class="gall_tit"><a href="#">changed</a></td></tr>'),
    mobile_page(mobile_row(), '<div id="pagination_div"><strong>?</strong></div>'),
    pc_page(pc_row(1) + pc_row(2, notice=False)),
])
@pytest.mark.asyncio
async def test_ambiguous_notice_page_is_error(html):
    api, _ = fake_api(html)
    with pytest.raises(BoardUnavailableError):
        _ = [row async for row in api.board('airforce', notice=True)]
    assert dc_api._BOARD_KIND_CACHE == {}


@pytest.mark.parametrize('recommend', [False, True])
@pytest.mark.parametrize('mobile', [False, True])
@pytest.mark.asyncio
async def test_normal_and_recommended_lists_exclude_pinned_notices(recommend, mobile):
    if mobile:
        html = '<ul class="gall-detail-lst">' + mobile_row(1) + mobile_row(2, classes='') + '</ul>'
    else:
        html = pc_page(pc_row(1, classes='ub-content us-post') + pc_row(2, classes='ub-content us-post', notice=False))
    api, _ = fake_api(html, mobile=mobile)
    items = [row async for row in api.board('airforce', recommend=recommend, num=30, max_scan_pages=1)]
    assert [row.id for row in items] == ['2']
    assert items[0].is_notice is False


@pytest.mark.parametrize('recommend', [False, True])
@pytest.mark.asyncio
async def test_pc_page_size_and_page_boundaries(recommend):
    api = API.__new__(API)
    calls = []

    async def request(method, url, **kwargs):
        calls.append(url)
        if urlparse(url).hostname == 'm.dcinside.com':
            return 200, {}, '<p>mobile unavailable</p>'
        params = query(url)
        assert params['list_num'] == ['30']
        assert params.get('exception_mode') == (['recommend'] if recommend else None)
        page = int(params['page'][0])
        rows = ''.join(pc_row(pid, notice=False, classes='ub-content us-post') for pid in range(91 - page * 30, 61 - page * 30, -1))
        pager = f'<div class="bottom_paging_box"><em>{page}</em><a href="/board/lists/?id=airforce&amp;page={page+1}">다음</a></div>'
        return 200, {}, pc_page(rows, pager)

    api._API__request_text = request
    first = [row.id async for row in api.board('airforce', start_page=1, recommend=recommend, num=30, max_scan_pages=1)]
    second = [row.id async for row in api.board('airforce', start_page=2, recommend=recommend, num=30, max_scan_pages=1)]
    assert first == [str(pid) for pid in range(61, 31, -1)]
    assert second == [str(pid) for pid in range(31, 1, -1)]
    assert not set(first) & set(second)


def test_notice_upstream_url_builders_and_pattern_cache_key():
    api = API.__new__(API)
    urls = api._API__build_list_urls('airforce', 3, notice=True, recommend=True, head_id='2', search_keyword='query')
    for url in urls:
        params = query(url)
        assert params['page'] == ['3']
        assert not {'recommend', 'headid', 'search_head', 'serval', 's_keyword', 's_type'} & params.keys()
        if urlparse(url).hostname == 'm.dcinside.com':
            assert params['notice'] == ['1']
        else:
            assert params['exception_mode'] == ['notice']
            assert params['list_num'] == ['30']
    assert api._API__board_kind_cache_key('airforce') != api._API__board_kind_cache_key('airforce', notice=True)
    assert core._board_index_cache_key(1, 'airforce', 0) != core._board_index_cache_key(1, 'airforce', 0, notice=True)


@pytest.mark.parametrize('http_redirect', [False, True])
@pytest.mark.asyncio
async def test_notice_survives_mobile_pc_redirects(http_redirect):
    api = API.__new__(API)
    start = 'https://m.dcinside.com/board/airforce?page=1&notice=1'
    target = 'https://gall.dcinside.com/board/lists/?id=airforce&page=1&recommend=1'
    expected = api._API__normalize_redirect_url(start, target)
    calls = []

    async def request(method, url, **kwargs):
        calls.append(url)
        if url == start:
            return (302, {'Location': target}, '') if http_redirect else (200, {}, f'<script>location.href="{target}";</script>')
        assert url == expected
        return 200, {}, pc_page(pc_row())

    api._API__request_text = request
    parsed, _, used = await api._API__fetch_parsed_from_urls(
        [start], validator=lambda p, t, u: api._notice_page_validator(p, t, u, 'airforce'))
    assert parsed is not None
    assert calls == [start, expected]
    assert used == expected
    assert query(expected)['exception_mode'] == ['notice']
    assert 'recommend' not in query(expected)
    reverse = api._API__normalize_redirect_url(expected, 'https://m.dcinside.com/board/airforce?page=1&recommend=1')
    assert query(reverse) == {'page': ['1'], 'notice': ['1']}


def test_notice_disables_automatic_http_redirect_following():
    # Verify request configuration, not just mocked fetch/redirect behavior.
    class Response:
        status = 302
        headers = {'Location': 'https://gall.dcinside.com/board/lists/?id=airforce'}
        async def text(self):
            return ''
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Session:
        def request(self, method, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            return Response()
    import asyncio
    api = API.__new__(API)
    api.session = Session()
    status, headers, _ = asyncio.run(api._API__request_text('GET', 'https://m.dcinside.com/board/airforce?notice=1'))
    assert status == 302 and 'Location' in headers


@pytest.fixture
def app():
    app = create_app()
    app.config.update(TESTING=True)
    return app


def test_public_notice_urls_clear_other_filters_and_keep_context(app):
    with app.test_request_context():
        url = routes.board_url('airforce', recommend=1, page=3, notice=1, head_id='10', search_keyword='query')
        assert query(url) == {'board': ['airforce'], 'recommend': ['0'], 'page': ['3'], 'notice': ['1']}
        url = routes.read_url('airforce', 1, recommend=1, source_page=3, notice=1, head_id='10', search_keyword='query')
        assert query(url) == {'board': ['airforce'], 'pid': ['1'], 'source_page': ['3'], 'notice': ['1']}


@pytest.mark.parametrize('path', ['/board?board=airforce&notice=1&page=3', '/read?board=airforce&pid=1&notice=1&source_page=3'])
def test_mixed_notice_urls_redirect_before_fetch(app, monkeypatch, path):
    def unexpected(*args, **kwargs):
        pytest.fail('must not fetch before normalizing URL')
    monkeypatch.setattr(routes, 'run_async', unexpected)
    response = app.test_client().get(path + '&recommend=1&headid=2&s_type=subject&serval=query')
    assert response.status_code == 302
    params = query(response.location)
    assert params['notice'] == ['1']
    assert not {'headid', 'serval', 's_type'} & params.keys()
    assert params.get('recommend', ['0']) == ['0']


def test_board_notice_context_and_single_page_redirect(app, monkeypatch):
    contexts = []
    async def payload(page, board, recommend, **kwargs):
        assert kwargs['notice'] is True
        assert recommend == 0
        kwargs['pagination_collector'].update(current_page=1, has_next=False)
        return [], []
    monkeypatch.setattr(routes, '_load_board_payload', payload)
    monkeypatch.setattr(routes, 'render_template', lambda template, **context: contexts.append((template, context)) or 'ok')
    response = app.test_client().get('/board?board=airforce&notice=1&page=5')
    assert response.status_code == 302
    assert query(response.location)['page'] == ['1']
    assert query(response.location)['notice'] == ['1']
    response = app.test_client().get(response.location)
    assert response.status_code == 200
    assert contexts[0][0] == 'board.html'
    assert contexts[0][1]['notice'] == 1
    assert contexts[0][1]['board_has_next'] is False


def test_read_notice_context_and_canonical_url(app, monkeypatch):
    contexts = []
    async def payload(*args, **kwargs):
        assert kwargs['notice'] is True
        return {'title': '공지', 'html': '', 'related_posts': [], '_related_has_more': False}, [], []
    monkeypatch.setattr(routes, 'async_read', payload)
    monkeypatch.setattr(routes, 'render_template', lambda template, **context: contexts.append((template, context)) or 'ok')
    response = app.test_client().get('/read?board=airforce&pid=1&notice=1&source_page=3')
    assert response.status_code == 200
    template, context = contexts[0]
    assert template == 'read.html'
    assert context['notice'] == 1
    assert context['source_page'] == 3
    assert context['related_has_more'] is False
    assert query(context['social_meta']['url'])['notice'] == ['1']


def test_notice_related_endpoint_makes_zero_upstream_calls(app, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('notice must not access upstream or related caches')
    monkeypatch.setattr(routes, 'run_async', unexpected)
    monkeypatch.setattr(routes, 'async_related_after_position', unexpected)
    response = app.test_client().get('/read/related?board=airforce&pid=1&notice=1')
    assert response.status_code == 200
    assert response.json == {'ok': True, 'items': [], 'has_more': False, 'disabled_reason': 'notice_context'}


@pytest.mark.asyncio
async def test_notice_core_cache_isolation_and_no_limit(monkeypatch):
    api, calls = fake_api(mobile_page(''.join(mobile_row(pid) for pid in range(1, 41))))
    @asynccontextmanager
    async def context():
        yield api
    monkeypatch.setattr(core, 'dc_api_context', context)
    rows, categories = await core.async_index_with_head_categories(1, 'airforce', 1, notice=True, force_refresh=True)
    assert len(rows) == 40 and categories == []
    assert len(calls) == 1
    again, _ = await core.async_index_with_head_categories(1, 'airforce', 0, notice=True, force_refresh=True)
    assert again == rows and len(calls) == 1
    assert core._LATEST_ID_CACHE == core._INITIAL_RELATED_CACHE == core._BOARD_PAGE_CACHE == {}
    assert len(core._BOARD_INDEX_CACHE) == len(core._BOARD_REFRESH_CACHE) == 1
    normal_key = core._board_index_cache_key(1, 'airforce', 0)
    assert normal_key not in core._BOARD_INDEX_CACHE
    assert core._claim_board_force_refresh(normal_key) is True


@pytest.mark.asyncio
async def test_notice_structure_error_never_cached_as_empty(monkeypatch):
    api, _ = fake_api('<p>challenge</p>')
    @asynccontextmanager
    async def context():
        yield api
    monkeypatch.setattr(core, 'dc_api_context', context)
    with pytest.raises(BoardUnavailableError):
        await core.async_index_with_head_categories(1, 'airforce', 0, notice=True, force_refresh=True)
    assert core._BOARD_INDEX_CACHE == {}


@pytest.mark.asyncio
async def test_notice_read_never_reads_or_writes_initial_related_cache(monkeypatch):
    async def load(*args, **kwargs):
        assert kwargs['notice'] is True
        return {'title': 'notice', 'related_posts': [{'id': '999'}]}, [], []
    monkeypatch.setattr(core, '_load_read_payload', load)
    def unexpected(*args, **kwargs):
        pytest.fail('notice must not access initial related cache')
    monkeypatch.setattr(core, '_initial_related_key', unexpected)
    for _ in range(2):
        data, _, _ = await core.async_read(1, 'airforce', notice=True)
        assert data['related_posts'] == []
        assert data['_related_has_more'] is False
    assert core._INITIAL_RELATED_CACHE == core._LATEST_ID_CACHE == core._BOARD_PAGE_CACHE == {}


@pytest.mark.asyncio
async def test_notice_document_does_not_parse_embedded_related_posts(monkeypatch):
    html = """<html><body><div class="gall-tit-box"><span class="tit">notice</span>
        <ul class="ginfo2"><li>운영자</li><li>2026.10.06 12:00</li></ul></div>
        <div class="thum-txtin"><p>body</p></div>
        <input id="reple_totalCnt" value="0">
        <ul id="view_next" class="gall-detail-lst"><li>notice order must not be parsed</li></ul>
        </body></html>"""
    api, calls = fake_api(html)
    def unexpected(*args, **kwargs):
        pytest.fail('must not parse embedded related posts in notice context')
    monkeypatch.setattr(api, '_API__parse_embedded_mobile_posts', unexpected)
    doc = await api.document('airforce', '1', notice=True, recommend=True, head_id='2')
    assert doc.title == 'notice'
    assert doc.related_posts == []
    assert len(calls) == 1
    assert query(calls[0]) == {'notice': ['1']}


@pytest.mark.asyncio
async def test_notice_item_document_callback_keeps_notice_context(monkeypatch):
    api, _ = fake_api(mobile_page(mobile_row()))
    items = [item async for item in api.board('airforce', notice=True)]
    async def document(*args, **kwargs):
        assert kwargs['notice'] is True
        return 'notice document'
    monkeypatch.setattr(api, 'document', document)
    assert await items[0].document() == 'notice document'


@pytest.mark.asyncio
async def test_confirmed_empty_notice_is_cached(monkeypatch):
    api, calls = fake_api(mobile_page())
    @asynccontextmanager
    async def context():
        yield api
    monkeypatch.setattr(core, 'dc_api_context', context)
    for _ in range(2):
        pagination = {}
        assert await core.async_index_with_head_categories(1, 'airforce', 0, notice=True, pagination_collector=pagination) == ([], [])
        assert pagination['has_next'] is False
    assert len(calls) == 1
