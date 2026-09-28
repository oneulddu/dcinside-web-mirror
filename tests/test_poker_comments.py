from concurrent.futures import ThreadPoolExecutor
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
        return page_fixture(1 if 'cpage=1' in path else 2)

    monkeypatch.setattr(reader, '_fetch', fetch)
    assert len(reader.post(123, 'free')['comments']) == 21
    older = reader.comment_page(123, 1, 'free')
    assert len(older['comments']) == 28
    older['comments'].clear()
    assert len(reader.comment_page(123, 1, 'free')['comments']) == 28
    reader.comment_page(123, 2, 'free')
    reader.comment_page(123, 1, 'hand')
    assert calls == ['/free/123', '/free/123?cpage=1', '/free/123?cpage=2', '/hand/123?cpage=1']
    assert len(reader.post(123, 'free')['comments']) == 21


def test_comment_reader_rejects_ignored_page_parameter(monkeypatch):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: page_fixture(2))
    with pytest.raises(pg.PokerError):
        reader.comment_page(123, 1, 'free')


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
        return page_fixture(1)

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
        return page_fixture(1)

    monkeypatch.setattr(reader, '_fetch', fetch)
    monkeypatch.setattr(poker_routes, 'reader', reader)
    response = create_app().test_client().get('/poker/hand/123/comments?cpage=1')
    assert response.status_code == 200
    assert calls == ['/hand/123?cpage=1']
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


def test_automatic_comment_loader_state_machine():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run([node, str(Path(__file__).parent / 'javascript/poker_comments.test.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
