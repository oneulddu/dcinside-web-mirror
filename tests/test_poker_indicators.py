from pathlib import Path
import json
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg
from app.services.poker_boards import BOARDS

FIXTURES = Path(__file__).parent / 'fixtures/pokergosu'


def board_with_attachment():
    return (FIXTURES / 'board.html').read_bytes().replace(
        b'<img alt="new">',
        b'<img alt="new"><img alt="file" src="https://www.ipokergosu.com/files/pg/util/file.gif">',
    )


def test_attachment_icon_is_distinct_from_new_update_or_author_icons():
    data = pg.parse_board(board_with_attachment(), 2)
    assert data['posts'][0]['has_attachment'] is True
    assert data['posts'][0]['has_image'] is False  # Upstream only promises an attachment.
    assert data['posts'][1]['has_attachment'] is False
    original = pg.parse_board((FIXTURES / 'board.html').read_bytes(), 2)
    assert original['posts'][0]['has_attachment'] is False
    wrong_icon = board_with_attachment().replace(b'/util/file.gif', b'/util/update.gif')
    assert pg.parse_board(wrong_icon, 2)['posts'][0]['has_attachment'] is False
    author_icon = (FIXTURES / 'board.html').read_bytes().replace(
        '<button><p>독자</p></button>'.encode(),
        b'<img alt="file" src="https://www.ipokergosu.com/files/pg/util/file.gif">',
    )
    assert pg.parse_board(author_icon, 2)['posts'][1]['has_attachment'] is False


def test_news_real_thumbnail_is_image_but_placeholder_or_other_post_is_not():
    raw = (FIXTURES / 'news.html').read_bytes()
    data = pg.parse_board(raw, 2, 'news')
    assert all(post['has_image'] for post in data['posts'])
    assert not any(post['has_attachment'] for post in data['posts'])
    placeholder = raw.replace(b'https://www.ipokergosu.com/img2/example.webp', b'/pokergosu/placeholder.svg')
    assert pg.parse_board(placeholder, 2, 'news')['posts'][0]['has_image'] is False
    wrong_post = raw.replace(b'<a href="/news/456?page=2" aria-label="move"><div>', b'<a href="/news/999?page=2" aria-label="move"><div>')
    assert pg.parse_board(wrong_post, 2, 'news')['posts'][0]['has_image'] is False


def test_board_and_footer_render_attachment_and_read_status_hook(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'board', lambda *a, **k: pg.parse_board(board_with_attachment(), 2))
    client = create_app().test_client()
    listing = BeautifulSoup(client.get('/poker/free?page=2').data, 'html.parser')
    footer = BeautifulSoup(client.get('/poker/free/list?page=2').get_json()['html'], 'html.parser')
    for soup in (listing, footer):
        first = soup.select_one('a.feed-item')
        assert first.select_one('[aria-label="이미지·파일 첨부"]')
        assert first.select_one('[data-poker-read-label]')
        assert not soup.select('img')  # Own vector marker, never fetch upstream badge images.


def test_success_page_exposes_read_id_but_error_page_does_not(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'post', lambda *a, **k: pg.parse_post((FIXTURES / 'post.html').read_bytes(), 123))
    client = create_app().test_client()
    soup = BeautifulSoup(client.get('/poker/free/123').data, 'html.parser')
    assert soup.select_one('#article-body')['data-poker-post-id'] == '123'

    def fail(*args, **kwargs):
        raise pg.PokerError('글을 찾을 수 없어요.', 404)

    monkeypatch.setattr(poker_routes.reader, 'post', fail)
    response = client.get('/poker/free/123')
    assert response.status_code == 404
    assert not BeautifulSoup(response.data, 'html.parser').select('[data-poker-post-id]')


def test_read_state_behavior():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run([node, str(Path(__file__).parent / 'javascript/poker_read_state.test.cjs'), json.dumps(list(BOARDS))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
