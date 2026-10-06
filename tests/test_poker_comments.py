from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import threading

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg

FIXTURES = Path(__file__).parent / 'fixtures/pokergosu'


def page_fixture(page):
    return (FIXTURES / f'comments-page-{page}.html').read_bytes()


def api_fixture(**changes):
    data = json.loads((FIXTURES / 'comments-api-page-1.json').read_text())
    data.update(changes)
    return json.dumps(data, ensure_ascii=False).encode()


def api_path(board, page, pid=123):
    return f'/api2/board/getcommnet/{board}/{pid}/{page}/25/xpage/20/undefined/undefined/0'


def test_comment_pages_use_only_comment_pagination_and_preserve_replies():
    older = pg.parse_post(page_fixture(1), 123)
    latest = pg.parse_post(page_fixture(2), 123)
    assert older['comment_page'] == 1 and older['comments_next_page'] is None
    assert latest['comment_page'] == 2 and latest['comments_next_page'] == 1
    assert len(older['comments']) == 28 and len(latest['comments']) == 21
    assert sum(not c['is_reply'] for c in older['comments']) == 25
    assert older['comments'][2]['parent_id'] == 'C2'
    assert older['comment_count'] == latest['comment_count'] == 49
    assert len({c['id'] for c in older['comments'] + latest['comments']}) == 49


def test_missing_or_ambiguous_pagination_does_not_invent_pages():
    raw = page_fixture(2).replace(b'Page Page-active', b'Page')
    data = pg.parse_post(raw, 123)
    assert data['comment_page'] is None and data['comments_next_page'] is None
    assert data['comments_partial']
    complete = pg.parse_post((FIXTURES / 'post.html').read_bytes(), 123)
    assert complete['comment_page'] == 1 and complete['comments_next_page'] is None
    duplicate_active = page_fixture(2).replace(b'class="Page"', b'class="Page Page-active"')
    assert pg.parse_post(duplicate_active, 123)['comment_page'] is None


def test_comment_reader_preserves_post_cache_and_keys_by_page_and_board(monkeypatch):
    reader = pg.Reader()
    calls = []

    def fetch(path):
        calls.append(path)
        return api_fixture() if path.startswith('/api2/') else page_fixture(2)

    monkeypatch.setattr(reader, '_fetch', fetch)
    assert len(reader.post(123, 'free')['comments']) == 21
    older = reader.comment_page(123, 1, 'free')
    assert len(older['comments']) == 28
    older['comments'].clear()
    assert len(reader.comment_page(123, 1, 'free')['comments']) == 28
    reader.comment_page(123, 2, 'free')
    reader.comment_page(123, 1, 'hand')
    assert calls == ['/free/123', api_path('free', 1), api_path('free', 2), api_path('hand', 1)]
    assert len(reader.post(123, 'free')['comments']) == 21


@pytest.mark.parametrize('raw,page', [
    (api_fixture(), 3),                                   # 원본 댓글 페이지 수(46개 → 2쪽)를 넘는다
    (api_fixture(postinfo2={'postinfo': [{'post_srl': 999, 'comment_count': 49}]}), 1),  # 다른 글
    (api_fixture(comments='bad'), 1),
    (api_fixture(comments=[]), 1),                        # 댓글이 있다는데 비어 있다
    (b'<html>challenge</html>', 1),                       # JSON이 아닌 응답
])
def test_comment_reader_rejects_unverified_api_page(monkeypatch, raw, page):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: raw)
    with pytest.raises(pg.PokerError) as exc:
        reader.comment_page(123, page, 'free')
    assert exc.value.status == 502


def test_comment_api_matches_html_rows_and_hides_what_upstream_hides():
    html_rows = pg.parse_post(page_fixture(1), 123)['comments']
    api = pg.parse_comment_api(api_fixture(), 123, 1)
    assert api['comment_page'] == 1 and api['comments_next_page'] is None and api['comment_count'] == 49
    assert api['comments'] == [dict(row, html=api_row['html']) for row, api_row in zip(html_rows, api['comments'])]
    hidden = json.loads(api_fixture())
    first = hidden['comments'][0]
    first['uploaded_count'] = 9998                        # 삭제된 댓글: 원본도 본문을 보이지 않는다
    hidden['comments'][1]['blind'] = 1
    rows = pg.parse_comment_api(json.dumps(hidden).encode(), 123, 2)['comments']
    ids = [row['id'] for row in rows]
    blind_id = 'C%d' % hidden['comments'][1]['comment_srl']
    assert 'C1' not in ids and blind_id not in ids and len(rows) == 26
    assert rows[0]['id'] == 'C2' and rows[0]['parent_id'] == 'C1'   # 답글은 남고 부모 관계도 그대로
    assert pg.parse_comment_api(api_fixture(), 123, 2)['comments_next_page'] == 1


@pytest.mark.parametrize('pid,page', [(0, 1), (123, 0), (123, 10001), (True, 1), (123, '1')])
def test_comment_reader_validates_before_transport(monkeypatch, pid, page):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: pytest.fail('unexpected fetch'))
    with pytest.raises(pg.PokerError) as exc:
        reader.comment_page(pid, page)
    assert exc.value.status == 400


def test_inconsistent_comment_count_stays_partial():
    raw = (FIXTURES / 'post.html').read_bytes().replace(b'<span>3</span>', b'<span>2</span>')
    data = pg.parse_post(raw, 123)
    assert len(data['comments']) == 3 and data['comment_count'] == 2
    assert data['comments_partial'] and data['comment_page'] is None


def test_comment_requests_coalesce(monkeypatch):
    reader = pg.Reader()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        return api_fixture()

    monkeypatch.setattr(reader, '_fetch', fetch)
    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(reader.comment_page, 123, 1, 'free')
        assert entered.wait(2)
        rest = [pool.submit(reader.comment_page, 123, 1, 'free') for _ in range(2)]
        release.set()
        assert all(len(f.result(3)['comments']) == 28 for f in [first] + rest)
    assert len(calls) == 1


def test_comment_endpoint_sanitizes_every_row_and_preserves_cursor(monkeypatch):
    calls = []

    reader = pg.Reader()

    def fetch(path):
        calls.append(path)
        return api_fixture()

    monkeypatch.setattr(reader, '_fetch', fetch)
    monkeypatch.setattr(poker_routes, 'reader', reader)
    response = create_app().test_client().get('/poker/hand/123/comments?cpage=1')
    assert response.status_code == 200
    assert calls == [api_path('hand', 1)]
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    data = response.get_json()
    assert data['page'] == 1 and data['next_page'] is None and data['total'] == 49
    assert len(data['comments']) == 28
    rows = BeautifulSoup(''.join(c['html'] for c in data['comments']), 'html.parser')
    assert len(rows.select('li[data-comment-id]')) == 28
    assert not rows.select('script,[onerror]')
    image = rows.select_one('img')
    assert not image.has_attr('src') and image['data-body-image-src'].startswith('/poker/media?')
    assert rows.select_one('[data-comment-id="C3"]')['data-parent-id'] == 'C2'


@pytest.mark.parametrize('path,status', [
    ('/poker/unknown/123/comments?cpage=1', 404),
    ('/poker/free/0/comments?cpage=1', 400),
    ('/poker/free/9999999999999/comments?cpage=1', 400),
    ('/poker/free/123/comments', 400),
    ('/poker/free/123/comments?cpage=0', 400),
    ('/poker/free/123/comments?cpage=10001', 400),
    ('/poker/free/123/comments?cpage=abc', 400),
])
def test_invalid_comment_request_does_not_fetch(monkeypatch, path, status):
    monkeypatch.setattr(poker_routes.reader, 'comment_page', lambda *a, **k: pytest.fail('unexpected fetch'))
    response = create_app().test_client().get(path)
    assert response.status_code == status and response.get_json()['error']


@pytest.mark.parametrize('status', [403, 404, 502, 503])
def test_comment_fetch_failure_is_json(monkeypatch, status):
    def fail(*args, **kwargs):
        raise pg.PokerError('댓글을 가져오지 못했어요.', status)

    monkeypatch.setattr(poker_routes.reader, 'comment_page', fail)
    response = create_app().test_client().get('/poker/free/123/comments?cpage=1')
    assert response.status_code == status
    assert response.get_json() == {'error': '댓글을 가져오지 못했어요.'}
    if status == 503:
        assert response.headers['Retry-After'] == '60'


def test_initial_read_fetches_no_extra_comment_pages(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'post', lambda *a, **k: pg.parse_post(page_fixture(2), 123))
    monkeypatch.setattr(poker_routes.reader, 'comments_blocked', lambda: False)
    monkeypatch.setattr(poker_routes.reader, 'comment_page', lambda *a, **k: pytest.fail('must be asynchronous'))
    response = create_app().test_client().get('/poker/free/123?page=3')
    soup = BeautifulSoup(response.data, 'html.parser')
    assert response.status_code == 200
    section = soup.select_one('#comment')
    assert section['data-comments-url'] == '/poker/free/123/comments'
    assert section['data-next-page'] == '1'
    assert section['data-total'] == '49'
    assert len(soup.select('#poker-comment-list [data-comment-id]')) == 21
    assert soup.select_one('#article-body')


def test_blocked_comment_pages_skip_automatic_collection(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'post', lambda *a, **k: pg.parse_post(page_fixture(2), 123))
    monkeypatch.setattr(poker_routes.reader, 'comments_blocked', lambda: True)
    monkeypatch.setattr(poker_routes.reader, 'comment_page', lambda *a, **k: pytest.fail('must not fetch'))
    soup = BeautifulSoup(create_app().test_client().get('/poker/free/123').data, 'html.parser')
    section = soup.select_one('#comment')
    assert section['data-next-page'] == ''
    assert section.select_one('[data-poker-comments-notice]') is not None
    assert section.select_one('[data-poker-comments-retry]') is None


def test_blocked_comment_endpoint_reports_code(monkeypatch):
    def blocked(*args, **kwargs):
        raise pg.PokerError(pg.COMMENTS_BLOCKED_MESSAGE, 503)

    monkeypatch.setattr(poker_routes.reader, 'comment_page', blocked)
    response = create_app().test_client().get('/poker/free/123/comments?cpage=1')
    assert response.status_code == 503
    assert response.get_json() == {'error': pg.COMMENTS_BLOCKED_MESSAGE, 'code': 'comments_blocked'}


def test_automatic_comment_loader_state_machine():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run([node, str(Path(__file__).parent / 'javascript/poker_comments.test.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
