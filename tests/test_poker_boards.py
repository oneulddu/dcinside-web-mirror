"""Multi-board contracts, including unavailable boards and news markup."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg, poker_media

FIXTURES = Path(__file__).parent / 'fixtures/pokergosu'
PUBLIC_BOARDS = {
    'notice': '공지사항', 'free': '자유 게시판', 'hand': '핸드 게시판',
    'best': '추천 게시판', 'grinding': '그라인딩 게시판',
    'strategy': '전략/번역 게시판', 'buyboard': '공구 게시판', 'news': '뉴스 게시판',
}


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch, tmp_path):
    monkeypatch.setenv('MIRROR_POKER_STATE_FILE', str(tmp_path / 'upstream.json'))


def board_html(board_id):
    raw = (FIXTURES / 'board.html').read_text()
    return raw.replace('자유 게시판', PUBLIC_BOARDS[board_id]).replace('/free/', '/' + board_id + '/').encode()


@pytest.mark.parametrize('board_id', [b for b in PUBLIC_BOARDS if b != 'news'])
def test_table_boards_filter_other_boards_but_notice_keeps_own_posts(board_id):
    data = pg.parse_board(board_html(board_id), 2, board_id)
    assert [p['id'] for p in data['posts']] == ([100, 123, 122] if board_id == 'notice' else [123, 122])
    assert all(p['board_id'] == board_id for p in data['posts'])
    assert data['has_next']


def test_news_cards_are_not_duplicated_and_do_not_fabricate_metadata():
    data = pg.parse_board((FIXTURES / 'news.html').read_bytes(), 2, 'news')
    assert [p['id'] for p in data['posts']] == [456, 455]
    assert data['posts'][0]['title'] == '새로운 뉴스'
    assert data['posts'][0]['comment_count'] == 4
    assert data['posts'][1]['comment_count'] == 0
    for post in data['posts']:
        assert all(post[k] is None for k in ('author', 'time', 'view_count', 'voteup_count'))
    assert data['has_next']


def test_board_title_mismatch_is_not_success():
    with pytest.raises(pg.PokerError):
        pg.parse_board(board_html('free'), 1, 'hand')


def test_comment_count_ignores_pagination_buttons():
    data = pg.parse_post((FIXTURES / 'paged-comments.html').read_bytes(), 123)
    assert data['comment_count'] == 68
    assert data['comments_partial']


def test_news_author_does_not_become_copy_url():
    raw = (FIXTURES / 'post.html').read_text().replace('<button><p>테스트 작성자</p></button>', '')
    raw = raw.replace('주소 복사', '<p>https://www.pokergosu.com/news/123</p>')
    raw = raw.replace('<body>', '<body><div class="boarddocument">').replace('</body>', '</div></body>')
    assert pg.parse_post(raw.encode(), 123)['author'] is None


def test_board_and_post_caches_are_isolated_by_board(monkeypatch):
    reader = pg.Reader()
    calls = []
    def fetch(path):
        calls.append(path)
        board = urlsplit(path).path.split('/')[1]
        if '?page=' in path:
            return board_html(board)
        return (FIXTURES / 'post.html').read_bytes().replace('이미지 있는 글'.encode(), board.encode())
    monkeypatch.setattr(reader, '_fetch', fetch)
    assert reader.board(1, 'free')['posts'][0]['board_id'] == 'free'
    assert reader.board(1, 'hand')['posts'][0]['board_id'] == 'hand'
    assert reader.post(123, 'free')['title'] == 'free'
    assert reader.post(123, 'hand')['title'] == 'hand'
    reader.board(1, 'hand'); reader.post(123, 'free')
    assert len(calls) == 4


@pytest.mark.parametrize('board_id', ['unknown', '..', 'media', 'free/123', 'https://evil.test'])
def test_invalid_board_rejected_at_reader_boundary(monkeypatch, board_id):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda p: pytest.fail('upstream called'))
    with pytest.raises(pg.PokerError): reader.board(1, board_id)
    with pytest.raises(pg.PokerError): reader.post(123, board_id)


def test_login_redirect_does_not_trigger_global_challenge_cooldown(monkeypatch):
    class Session:
        def __init__(self, **kw): pass
        def close(self): pass
        def get(self, url, **kw):
            assert kw['allow_redirects'] is False
            return SimpleNamespace(status_code=302, headers={'location': '/login?to=/groupbuy'})
    monkeypatch.setattr(pg.requests, 'Session', Session)
    reader = pg.Reader()
    with pytest.raises(pg.PokerError) as error: reader.board(1, 'groupbuy')
    assert error.value.status == 403
    assert '로그인' in str(error.value)
    assert reader.cooldown_until == 0


@pytest.mark.parametrize('board_id', PUBLIC_BOARDS)
def test_routes_use_board_in_urls_and_navigation(monkeypatch, board_id):
    def board(page, board_id='free'):
        raw = (FIXTURES/'news.html').read_bytes() if board_id=='news' else board_html(board_id)
        return pg.parse_board(raw, page, board_id)
    monkeypatch.setattr(poker_routes.reader, 'board', board)
    monkeypatch.setattr(poker_routes.reader, 'post',
                        lambda pid, board_id='free', prepare=None:
                        pg.Reader._prepare(pg.parse_post((FIXTURES/'post.html').read_bytes(), pid), prepare))
    client = create_app().test_client()
    r = client.get('/poker/'+board_id+'?page=2')
    assert r.status_code == 200
    soup = BeautifulSoup(r.data, 'html.parser')
    link = soup.select_one('a.feed-item')['href']
    assert link.startswith('/poker/'+board_id+'/') and 'page=2' in link
    r = client.get(link)
    assert r.status_code == 200
    soup = BeautifulSoup(r.data, 'html.parser')
    assert soup.find('a', href='/poker/'+board_id+'?page=2')
    for b in [*PUBLIC_BOARDS, 'groupbuy', 'qna']:
        assert soup.find('a', href='/poker/'+b+'?page=1')


@pytest.mark.parametrize('board_id', ['groupbuy','qna'])
def test_restricted_board_has_honest_message_and_navigation(monkeypatch, board_id):
    def restricted(*args, **kw): raise pg.PokerError('원본 로그인이 필요한 게시판이에요.', 403)
    monkeypatch.setattr(poker_routes.reader, 'board', restricted)
    client = create_app().test_client()
    r = client.get('/poker/'+board_id)
    assert r.status_code == 403
    soup = BeautifulSoup(r.data,'html.parser')
    assert '로그인' in soup.h1.get_text()
    assert soup.find('a', href='https://www.pokergosu.com/'+board_id+'?page=1')
    assert soup.find('a', href='/poker/free?page=1')


def test_unknown_routes_never_fetch(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, '_fetch', lambda p: pytest.fail('upstream called'))
    client=create_app().test_client()
    assert client.get('/poker/unknown').status_code == 404
    assert client.get('/poker/unknown/123').status_code == 404
    assert client.get('/poker').status_code in (301,302,308)


def test_cross_board_links_preserve_safe_page_and_comments():
    with create_app().test_request_context('/'):
        raw='<a href="/hand/123?page=2#comment">핸드</a><a href="/news?page=3">뉴스</a><a href="/qna/45">문의</a><a href="https://evil.test/free/12">외부</a><a href="https://[">오류</a>'
        soup=BeautifulSoup(poker_media.prepare_html(raw), 'html.parser')
        assert soup.find('a',string='핸드')['href']=='/poker/hand/123?page=2#comment'
        assert soup.find('a',string='뉴스')['href']=='/poker/news?page=3'
        assert soup.find('a',string='문의')['href']=='/poker/qna/45'
        assert soup.find('a',string='외부')['href']=='https://evil.test/free/12'
        assert not soup.find('a',string='오류').has_attr('href')


def test_news_empty_grid_and_structure_error_are_distinct():
    empty='<title>뉴스 게시판 - 포커고수</title><div class="grid grid-cols-2 gap-y-4"></div>'.encode()
    assert pg.parse_board(empty, 1, 'news') == {'posts': [], 'has_next': False}
    with pytest.raises(pg.PokerError):
        pg.parse_board(b'<title>Unknown</title>', 1, 'news')
    broken=empty.replace(b'</div>', b'<div><a>broken</a></div></div>')
    with pytest.raises(pg.PokerError):
        pg.parse_board(broken, 1, 'news')


@pytest.mark.parametrize('location,allowed', [('/login?to=/qna',True),
    ('https://www.pokergosu.com/login?to=/free/1',True),
    ('https://evil.test/login',False),('//evil.test/login',False),
    ('https://user@www.pokergosu.com/login',False),('/other',False),('https://[',False)])
def test_login_redirect_exact_destination(location,allowed):
    from app.services.poker_boards import is_login_redirect
    assert is_login_redirect(location) is allowed


def test_nginx_matches_board_catalog_and_excludes_media():
    import re
    from app.services.poker_boards import BOARDS
    config=(Path(__file__).parents[1]/'ops/nginx/mirror-protection.conf.example').read_text()
    pattern=re.search(r'location ~ (\^/poker/\S+) \{',config)[1]
    assert set(pattern.split('(?:', 1)[1].split(')', 1)[0].split('|')) == set(BOARDS)
    for board in BOARDS:
        assert re.match(pattern,'/poker/'+board)
        assert re.match(pattern,'/poker/'+board+'/123')
    assert not re.match(pattern,'/poker/media')
    assert not re.match(pattern,'/poker/not-supported')


def test_malformed_list_link_does_not_drop_valid_posts():
    raw=board_html('hand')
    raw=raw.replace(b'<td><a href="/hand/122?page=2">', b'<td><a href="https://[">bad</a><a href="/hand/122?page=2">')
    assert len(pg.parse_board(raw,1,'hand')['posts'])==2


def test_news_render_omits_unknown_metadata(monkeypatch):
    listing=pg.parse_board((FIXTURES/'news.html').read_bytes(),1,'news')
    monkeypatch.setattr(poker_routes.reader,'board',lambda *a,**k: listing)
    data=pg.parse_post((FIXTURES/'post.html').read_bytes(),456)
    data.update(author=None,time=None,view_count=None,voteup_count=None)
    monkeypatch.setattr(poker_routes.reader,'post',lambda *a,**k: data)
    client=create_app().test_client()
    listing_soup=BeautifulSoup(client.get('/poker/news').data,'html.parser')
    assert not listing_soup.select('.poker-feed .feed-meta-row')
    soup=BeautifulSoup(client.get('/poker/news/456').data,'html.parser')
    assert not soup.select('.article-head .article-meta')
    assert 'None' not in soup.select_one('.article-head').get_text()
    assert soup.select_one('#comment') is not None
