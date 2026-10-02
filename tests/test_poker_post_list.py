from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg
from app.services.poker_boards import BOARDS

ROOT = Path(__file__).parent


def listing(board_id='free', has_next=True):
    return {
        'posts': [
            dict(id=123, board_id=board_id, title='<script>unsafe</script>',
                 author='독자', time='2026-09-28', comment_count=3,
                 view_count=50, voteup_count=1),
            dict(id=122, board_id=board_id, title='다른 글', author=None,
                 time=None, comment_count=0, view_count=None, voteup_count=None),
        ],
        'has_next': has_next,
    }


def test_board_order():
    assert list(BOARDS) == [
        'best', 'free', 'hand', 'grinding', 'strategy',
        'news', 'buyboard', 'notice', 'groupbuy', 'qna',
    ]


def test_list_fragment_preserves_board_page_and_current_post(monkeypatch):
    calls = []

    def board(page, board_id='free'):
        calls.append((page, board_id))
        return listing(board_id)

    monkeypatch.setattr(poker_routes.reader, 'board', board)
    response = create_app().test_client().get('/poker/hand/list?page=2&current_pid=123')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert calls == [(2, 'hand')]
    soup = BeautifulSoup(response.get_json()['html'], 'html.parser')
    assert not soup.select('script')
    active = soup.select_one('.feed-item[aria-current="page"]')
    assert active and active['href'] == '/poker/hand/123?page=2'
    assert '읽는 글' in active.get_text()
    assert soup.find('a', href='/poker/hand/122?page=2')
    assert soup.select_one('[data-list-page="1"]')['href'] == '/poker/hand?page=1'
    assert soup.select_one('[data-list-page="3"]')['href'] == '/poker/hand?page=3'
    assert not soup.find('a', href='/')


def test_last_empty_and_unknown_metadata(monkeypatch):
    client = create_app().test_client()
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **k: listing('news', False))
    response = client.get('/poker/news/list?current_pid=999')
    soup = BeautifulSoup(response.get_json()['html'], 'html.parser')
    assert not soup.select('.feed-item[aria-current]')
    assert 'None' not in soup.get_text()
    assert not soup.select('[data-list-page]')  # First and final page.
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **k: {'posts': [], 'has_next': False})
    response = client.get('/poker/news/list?page=2')
    soup = BeautifulSoup(response.get_json()['html'], 'html.parser')
    assert '게시글이 없어요' in soup.get_text()
    assert not soup.select('.feed-item')
    assert soup.select_one('[data-list-page="1"]')['href'] == '/poker/news?page=1'
    assert not soup.find('a', href='/')


@pytest.mark.parametrize('path,status', [
    ('/poker/unknown/list', 404),
    ('/poker/free/list?page=0', 400),
    ('/poker/free/list?page=10001', 400),
    ('/poker/free/list?current_pid=0', 400),
    ('/poker/free/list?current_pid=', 400),
    ('/poker/free/list?current_pid=9999999999999', 400),
    ('/poker/free/list?current_pid=hello', 400),
])
def test_invalid_fragment_requests_never_fetch(monkeypatch, path, status):
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **k: pytest.fail('unexpected upstream call'))
    response = create_app().test_client().get(path)
    assert response.status_code == status
    assert response.is_json and response.get_json()['error']


@pytest.mark.parametrize('status', [403, 404, 502, 503])
def test_failure_is_json_and_not_an_empty_list(monkeypatch, status):
    def fail(*args, **kwargs):
        raise pg.PokerError('목록을 가져오지 못했어요', status)

    monkeypatch.setattr(poker_routes.reader, 'board', fail)
    response = create_app().test_client().get('/poker/free/list')
    assert response.status_code == status
    assert response.get_json()['error'] == '목록을 가져오지 못했어요'
    assert 'html' not in response.get_json()
    assert response.headers['Cache-Control'] == 'no-store'
    if status == 503:
        assert response.headers['Retry-After'] == '60'


def test_read_never_waits_for_board_and_exposes_canonical_fallback(monkeypatch):
    data = pg.parse_post((ROOT / 'fixtures/pokergosu/post.html').read_bytes(), 123)
    monkeypatch.setattr(poker_routes.reader, 'post', lambda *a, **k: data)
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **k: pytest.fail('read route must not fetch list'))
    response = create_app().test_client().get('/poker/hand/123?page=3')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    section = soup.select_one('#poker-post-list')
    assert section is not None
    assert '/poker/hand/list' in section['data-list-url']
    assert section['data-page'] == '3' and section['data-current-pid'] == '123'
    assert soup.select_one('#article-body')
    assert soup.select_one('noscript a')['href'] == '/poker/hand?page=3'


def test_bottom_list_browser_logic():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run(
        [node, str(ROOT / 'javascript/poker_post_list.test.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
