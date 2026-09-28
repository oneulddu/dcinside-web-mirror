"""Search parser, cache, route contexts, and Poker views staying out of DC recent history."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import template_rendered, url_for
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg, recent

FIXTURES = Path(__file__).parent / 'fixtures/pokergosu'


def fixture(name):
    return (FIXTURES / (name + '.html')).read_bytes()


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv('MIRROR_POKER_STATE_FILE', str(tmp_path / 'upstream.json'))
    monkeypatch.setattr(recent, 'RECENT_SERVER_CACHE', {})
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def contexts(app):
    rendered = []
    def record(sender, template, context, **extra):
        rendered.append((template.name, context))
    template_rendered.connect(record, app)
    yield rendered
    template_rendered.disconnect(record, app)


@pytest.mark.parametrize('board,name,ids', [('free', 'search', [123, 122]),
    ('news', 'search-news', [456, 455]), ('free', 'search-empty', []), ('news', 'search-news-empty', [])])
def test_search_structures_and_next_page(board, name, ids):
    data = pg.parse_search(fixture(name), 2, board)
    assert [post['id'] for post in data['posts']] == ids
    assert data['has_next'] is bool(ids)
    assert not pg.parse_search(fixture(name), 3, board)['has_next']


@pytest.mark.parametrize('name,board,old,new', [
    ('search', 'free', b'/free/122?page=2', b'/free/not-a-post'),
    ('search', 'free', b'</table>', b'<tr><td>broken</td></tr></table>'),
    ('search-news', 'news', b'/news/455?page=2', b'/news/not-a-post'),
    ('search-news', 'news', b'grid-cols-2', b'unknown'),
    ('search', 'free', '검색 - 포커고수'.encode(), b'Login'),
])
def test_broken_results_are_errors_even_with_other_valid_rows(name, board, old, new):
    with pytest.raises(pg.PokerError):
        pg.parse_search(fixture(name).replace(old, new), 1, board)


def test_search_encoding_cache_isolation_and_empty_warning(monkeypatch, app, caplog):
    reader, calls = pg.Reader(), []
    def fetch(path):
        calls.append(path)
        return fixture('search-empty' if '/search?' in path else 'board')
    monkeypatch.setattr(reader, '_fetch', fetch)
    keyword = '한 글&+%?#😀'
    for board, s, v, page in [('free', 1, keyword, 1), ('free', 2, keyword, 1),
                              ('hand', 1, keyword, 1), ('free', 1, '다른 글', 1), ('free', 1, keyword, 2)]:
        assert reader.search(page, board_id=board, s=s, v=v) == {'posts': [], 'has_next': False}
    reader.search(1, board_id='free', s=1, v=' ' + keyword + ' ')
    reader.board(1)
    assert len(calls) == 6
    assert parse_qs(urlsplit(calls[0]).query) == {'s': ['1'], 'v': [keyword], 'page': ['1']}
    assert ('search', 'free', 1, keyword, 1) in reader.cache
    assert ('board', 'free', 1) in reader.cache
    assert not caplog.records


def test_search_singleflight(monkeypatch, app):
    reader, calls = pg.Reader(), []
    entered, release = threading.Event(), threading.Event()
    def fetch(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        return fixture('search')
    monkeypatch.setattr(reader, '_fetch', fetch)
    with ThreadPoolExecutor(4) as pool:
        first = pool.submit(reader.search, 1, board_id='free', s=1, v='검색')
        assert entered.wait(2)
        rest = [pool.submit(reader.search, 1, board_id='free', s=1, v='검색') for _ in range(3)]
        release.set()
        assert all(len(f.result(3)['posts']) == 2 for f in [first] + rest)
    assert len(calls) == 1 and not reader.flights


def test_search_stale_and_failed_parser_negative_cache(monkeypatch, app):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: fixture('search'))
    reader.search(1, board_id='free', s=1, v='검색')
    entry = reader.cache[('search', 'free', 1, '검색', 1)]
    entry.fresh_until = 0
    calls = []
    monkeypatch.setattr(reader, '_fetch', lambda path: calls.append(path) or b'<p>broken</p>')
    assert reader.search(1, board_id='free', s=1, v='검색')['stale'] is True
    assert reader.search(1, board_id='free', s=1, v='검색')['stale'] is True
    for _ in range(2):
        with pytest.raises(pg.PokerError):
            reader.search(2, board_id='free', s=1, v='검색')
    assert len(calls) == 2
    assert reader.cache[('search', 'free', 1, '검색', 2)].value is None


@pytest.mark.parametrize('query', ['', 'v=a', 'v=' + '가' * 21, 'v=ab&s=0', 'v=ab&s=6',
    'v=ab&s=01', 'v=ab&s=２', 'v=ab&s=', 'v=ab&s=1&s=2', 'v=ab&v=cd',
    'v=ab&page=1&page=2', 'v=ab&page=0', 'v=ab&page=10001', 'v=ab&page=２',
    'v=ab&page=1.0', 'v=ab&page=', 'v=ab&page=-1'])
def test_invalid_search_renders_preserved_context_without_fetch_or_recent(monkeypatch, app, contexts, query):
    monkeypatch.setattr(poker_routes.reader, 'search', lambda *a, **k: pytest.fail('unexpected fetch'))
    client = app.test_client()
    response = client.get('/poker/free/search?' + query)
    assert response.status_code == 400
    template, context = contexts[-1]
    assert template == 'poker/board.html'
    assert context['data'] == {'posts': [], 'has_next': False}
    assert context['search_error']
    assert context['search']['v'] == parse_qs(query, keep_blank_values=True).get('v', [''])[0].strip()
    assert context['search']['s'] in range(1, 6)
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None


@pytest.mark.parametrize('board,s,expected', [('free', 'bad', 1), ('news', '5', 1), ('free', '2', 2)])
def test_invalid_search_range_and_length_message(monkeypatch, app, contexts, board, s, expected):
    monkeypatch.setattr(poker_routes.reader, 'search', lambda *a, **k: pytest.fail('unexpected fetch'))
    response = app.test_client().get(f'/poker/{board}/search', query_string={'s': s, 'v': ' <bad> ' * 6})
    assert response.status_code == 400
    context = contexts[-1][1]
    assert context['search']['s'] == expected
    assert context['search']['v'] == (' <bad> ' * 6).strip()
    assert [item['value'] for item in context['search_types']] == list(range(1, 5 if board == 'news' else 6))
    if s == '2':
        assert context['search_error'] == '검색어는 2~20자로 입력해 주세요.'


@pytest.mark.parametrize('v', [' 가나 ', '😀😀', '😀' * 20])
def test_search_defaults_unicode_and_frozen_context(monkeypatch, app, contexts, v):
    calls = []
    monkeypatch.setattr(poker_routes.reader, 'search', lambda page, **kw: calls.append((page, kw)) or
                        {'posts': [], 'has_next': False, 'stale': True})
    client = app.test_client()
    response = client.get('/poker/news/search', query_string={'v': v})
    assert response.status_code == 200
    assert calls == [(1, {'board_id': 'news', 's': 1, 'v': v.strip()})]
    template, context = contexts[-1]
    assert template == 'poker/board.html'
    assert context['search'] == {'s': 1, 'v': v.strip(), 'label': '제목+내용'}
    assert context['search_error'] is None and context['data']['stale']
    assert [row['value'] for row in context['search_types']] == [1, 2, 3, 4]
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None
    with app.test_request_context():
        assert url_for('poker.search', board_id='news') == '/poker/news/search'


@pytest.mark.parametrize('board,status', [('unknown', 404), ('groupbuy', 403), ('qna', 403)])
def test_unavailable_search_never_fetches_or_records(monkeypatch, app, board, status):
    monkeypatch.setattr(poker_routes.reader, '_fetch', lambda *a: pytest.fail('unexpected fetch'))
    client = app.test_client()
    assert client.get(f'/poker/{board}/search?v=검색').status_code == status
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None


@pytest.mark.parametrize('status', [502, 503])
@pytest.mark.parametrize('path,method', [('/poker/free/search', 'search'), ('/poker/free/123', 'post')])
def test_error_retry_and_return_preserve_search(monkeypatch, app, contexts, status, path, method):
    def fail(*a, **kw):
        raise pg.PokerError('원본 오류', status)
    monkeypatch.setattr(poker_routes.reader, method, fail)
    client = app.test_client()
    response = client.get(path, query_string={'page': 3, 's': 2, 'v': '한 글&+'})
    assert response.status_code == status
    assert response.headers['Cache-Control'] == 'no-store'
    if status == 503:
        assert response.headers['Retry-After'] == '60'
    context = contexts[-1][1]
    for key in ('retry_url', 'return_url'):
        assert parse_qs(urlsplit(context[key]).query) == {'page': ['3'], 's': ['2'], 'v': ['한 글&+']}
    assert urlsplit(context['retry_url']).path == path
    assert urlsplit(context['return_url']).path == '/poker/free/search'
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None


@pytest.mark.parametrize('suffix,expected', [('?page=2&s=2&v=검색', {'s': 2, 'v': '검색', 'label': '제목'}), ('', None)])
def test_read_context_keeps_valid_search_without_fetching_list(monkeypatch, app, contexts, suffix, expected):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: fixture('post'))
    monkeypatch.setattr(reader, 'search', lambda *a, **k: pytest.fail('read must not wait for list'))
    monkeypatch.setattr(poker_routes, 'reader', reader)
    client = app.test_client()
    assert client.get('/poker/free/123' + suffix).status_code == 200
    assert contexts[-1][1]['search'] == expected
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None


@pytest.mark.parametrize('suffix', ['?s=6&v=검색', '?s=1&v=a', '?s=1&s=2&v=검색', '?v=ab&v=cd', '?page=4&s=1&v=a'])
def test_read_with_invalid_search_redirects_before_fetch(monkeypatch, app, suffix):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: pytest.fail('invalid search must not fetch'))
    monkeypatch.setattr(poker_routes, 'reader', reader)
    response = app.test_client().get('/poker/free/123' + suffix)
    assert response.status_code == 302
    target = urlsplit(response.headers['Location'])
    assert target.path == '/poker/free/123'
    assert parse_qs(target.query) == {'page': ['4' if 'page=4' in suffix else '1']}


def test_search_fragment_context_stale_and_no_recent(monkeypatch, app, contexts):
    calls = []
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **kw: pytest.fail('wrong reader method'))
    monkeypatch.setattr(poker_routes.reader, 'search', lambda page, **kw: calls.append((page, kw)) or
                        {'posts': [], 'has_next': False, 'stale': True})
    client = app.test_client()
    response = client.get('/poker/hand/list?page=3&s=4&v=검색&current_pid=123')
    assert response.status_code == 200
    assert response.json['page'] == 3 and response.json['stale'] is True and 'html' in response.json
    assert calls == [(3, {'board_id': 'hand', 's': 4, 'v': '검색'})]
    template, context = contexts[-1]
    assert template == 'poker/_post_list.html'
    assert context['search'] == {'s': 4, 'v': '검색', 'label': '댓글'}
    assert context['current_pid'] == 123
    assert client.get_cookie(recent.RECENT_COOKIE_NAME) is None


@pytest.mark.parametrize('query', ['s=1', 'v=a', 'v=ab&s=9', 'v=ab&s=5', 'v=ab&v=cd',
                                  'v=ab&s=1&s=2', 'v=ab&page=2&page=3'])
def test_invalid_fragment_search_is_json_without_fetch(monkeypatch, app, query):
    monkeypatch.setattr(poker_routes.reader, '_fetch', lambda *a: pytest.fail('unexpected fetch'))
    response = app.test_client().get('/poker/news/list?' + query)
    assert response.status_code == 400 and response.json['error']


def test_poker_pages_never_touch_dc_recent_history(monkeypatch, app, contexts):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: fixture('post'))
    monkeypatch.setattr(reader, 'board', lambda *a, **kw: {'posts': [], 'has_next': False, 'stale': True})
    monkeypatch.setattr(reader, 'search', lambda *a, **kw: {'posts': [], 'has_next': False})
    monkeypatch.setattr(poker_routes, 'reader', reader)
    client = app.test_client()
    for path in ['/poker/free', '/poker/free/search?v=검색', '/poker/free/123']:
        response = client.get(path)
        assert response.status_code == 200
        assert not response.headers.getlist('Set-Cookie')
    board_context = contexts[0][1]
    assert board_context['search'] is None and board_context['search_error'] is None
    assert len(board_context['search_types']) == 5
    for path in ['/poker/free/list', '/poker/free/123/comments?cpage=1', '/poker/free/search?v=a', '/poker/media']:
        response = client.get(path)
        assert not response.headers.getlist('Set-Cookie')
