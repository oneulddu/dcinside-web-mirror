from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from urllib.parse import urlsplit, parse_qs

from bs4 import BeautifulSoup
import pytest

from app import create_app
from app import poker_routes
from app.services import pokergosu as pg, poker_media as media
from app.services.html_sanitizer import sanitize_html_fragment
from app.services.link_preview import preview_image_signature

FIXTURES = Path(__file__).parent / 'fixtures' / 'pokergosu'


@pytest.mark.parametrize('host', ['youtube.com', 'www.youtube.com', 'youtube-nocookie.com',
                                  'www.youtube-nocookie.com'])
@pytest.mark.parametrize('scheme', ['https:', ''])
def test_youtube_body_normalization_and_comment_exclusion(host, scheme):
    raw = (f'<iframe src="{scheme}//{host}/embed/Abcdef_12-3?autoplay=1&amp;list=x#fragment" '
           'srcdoc="evil" allow="autoplay" onload="evil()" style="display:none" width="1000" '
           'class="bad" data-secret="bad"></iframe>')
    with create_app().test_request_context('/'):
        soup = BeautifulSoup(media.prepare_html(raw, allow_youtube=True), 'html.parser')
        assert soup.iframe.attrs == {
            'src': 'https://www.youtube-nocookie.com/embed/Abcdef_12-3', 'loading': 'lazy',
            'title': 'YouTube 동영상', 'referrerpolicy': 'strict-origin-when-cross-origin',
            'allowfullscreen': '',
        }
        assert not BeautifulSoup(media.prepare_html(raw), 'html.parser').iframe


@pytest.mark.parametrize('src', [
    'http://www.youtube.com/embed/Abcdef_12-3', 'https://www.youtube.com:443/embed/Abcdef_12-3',
    'https://www.youtube.com:/embed/Abcdef_12-3', 'https://u:p@www.youtube.com/embed/Abcdef_12-3',
    'https://www.youtube.com.evil.test/embed/Abcdef_12-3', 'https://www.youtube.com./embed/Abcdef_12-3',
    'https://youtu.be/Abcdef_12-3', 'https://www.youtube.com/shorts/Abcdef_12-3',
    'https://www.youtube.com/embed/short', 'https://www.youtube.com/embed/Abcdef_12-3/',
    'https://www.youtube.com/embed/Abcdef_12-3/extra', 'https://www.youtube.com/embed/Abcdef_12-3%2f',
    '/embed/Abcdef_12-3', 'https://www.you\ntube.com/embed/Abcdef_12-3',
    'https://www.youtube.com\\@evil.test/embed/Abcdef_12-3',
    'https://www.youtube.com/embed/Abcdef_12-3?x=\x7f', 'https://[',
    'https://m.dcinside.com/poll?id=1', 'https://platform.twitter.com/embed/Tweet.html?id=1',
])
def test_poker_youtube_rejects_unsafe_or_non_embed_urls(src):
    from html import escape
    assert media.youtube_iframe_src(src) is None
    raw = '<iframe src="' + escape(src, quote=True) + '"></iframe>'
    with create_app().test_request_context('/'):
        assert not BeautifulSoup(media.prepare_html(raw, allow_youtube=True), 'html.parser').iframe
    raw_post = fixture('post').decode().replace('<p>첫 문단</p>', raw)
    assert pg.parse_post(raw_post, 123)['unsupported_media'] is True


@pytest.mark.parametrize('other,unsupported', [
    ('', False), ('<a href="https://example.org/read">일반 외부 링크</a>', False),
    ('<video src="https://example.org/a.mp4"></video>', True), ('<audio></audio>', True),
    ('<object data="x"></object>', True), ('<embed src="x">', True),
    ('<iframe src="https://evil.test/embed/Abcdef_12-3"></iframe>', True),
    ('<a download href="https://example.org/file.pdf">첨부</a>', True),
    ('<a href="/file"><img alt="file" src="https://www.ipokergosu.com/files/pg/util/file.gif"></a>', True),
])
def test_post_unsupported_media_uses_same_youtube_policy(other, unsupported):
    youtube = '<iframe src="//www.youtube.com/embed/Abcdef_12-3"></iframe>'
    raw = fixture('post').decode().replace('<p>첫 문단</p>', youtube + other)
    assert pg.parse_post(raw, 123)['unsupported_media'] is unsupported


def test_prepared_body_comment_and_raw_caches_remain_separate(monkeypatch):
    from functools import partial
    youtube = '<iframe src="https://www.youtube.com/embed/Abcdef_12-3"></iframe>'
    raw = fixture('post').decode().replace('<p>첫 문단</p>', youtube).replace('<p>첫 댓글</p>', youtube)
    reader, calls = pg.Reader(), []
    monkeypatch.setattr(reader, '_fetch', lambda path: calls.append(path) or raw.encode())
    app = create_app()
    with app.test_request_context('/'):
        unprepared = reader.post(123)
        blocked = reader.post(123, prepare=media.prepare_html)
        split = (partial(media.prepare_html, allow_youtube=True), media.prepare_html)
        prepared = reader.post(123, prepare=split)
        again = reader.post(123, prepare=split)
    assert len(calls) == 3
    assert 'www.youtube.com/embed/' in unprepared['html']
    assert not BeautifulSoup(blocked['html'], 'html.parser').iframe
    assert 'www.youtube-nocookie.com/embed/' in prepared['html']
    assert prepared == again
    assert not any(BeautifulSoup(comment['html'], 'html.parser').iframe for comment in prepared['comments'])


def test_routes_keep_youtube_only_in_body_and_never_fetch_auxiliary_media(monkeypatch):
    from flask import template_rendered
    youtube = '<iframe src="https://www.youtube.com/embed/Abcdef_12-3?autoplay=1"></iframe>'
    raw = fixture('post').decode().replace('<p>첫 문단</p>', youtube).replace('<p>첫 댓글</p>', youtube)
    reader, calls, contexts = pg.Reader(), [], []
    monkeypatch.setattr(reader, '_fetch', lambda path: calls.append(path) or raw.encode())
    monkeypatch.setattr(poker_routes, 'reader', reader)
    monkeypatch.setattr(media, 'resolve_media_target', lambda *a, **k: pytest.fail('auxiliary media request'))
    app = create_app()
    def record(sender, template, context, **extra):
        contexts.append((template.name, context))
    with template_rendered.connected_to(record, app):
        client = app.test_client()
        assert client.get('/poker/free/123').status_code == 200
        data = contexts[-1][1]['data']
        assert BeautifulSoup(data['html'], 'html.parser').iframe
        assert data['unsupported_media'] is False
        assert not any(BeautifulSoup(c['html'], 'html.parser').iframe for c in data['comments'])
        response = client.get('/poker/free/123/comments?cpage=1')
    assert response.status_code == 200
    assert not any(BeautifulSoup(c['html'], 'html.parser').iframe for c in response.json['comments'])
    assert calls == ['/free/123', '/free/123?cpage=1']


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch, tmp_path):
    monkeypatch.setenv('MIRROR_POKER_STATE_FILE', str(tmp_path / 'upstream.json'))


def fixture(name):
    return (FIXTURES / (name + '.html')).read_bytes()


def test_board_real_structure_and_page():
    data = pg.parse_board(fixture('board'), 2)
    assert [p['id'] for p in data['posts']] == [123, 122]
    assert data['posts'][0]['comment_count'] == 3
    assert data['posts'][0]['author'] == '테스트 작성자'
    assert data['posts'][0]['view_count'] == 42
    assert data['has_next']
    assert not pg.parse_board(fixture('board'), 3)['has_next']


def test_post_preserves_reply_order_and_metadata():
    data = pg.parse_post(fixture('post'), 123)
    assert data['title'] == '이미지 있는 글'
    assert data['author'] == '테스트 작성자'
    assert data['time'] == '2026.09.27 13:22:02'
    assert data['view_count'] == 42
    assert [c['id'] for c in data['comments']] == ['C1', 'C2', 'C3']
    assert data['comments'][1]['parent_id'] == 'C1'
    assert [c['is_reply'] for c in data['comments']] == [False, True, False]
    assert not data['comments_partial']
    partial = fixture('post').replace(b'<span>3</span>', b'<span>100</span>')
    assert pg.parse_post(partial, 123)['comments_partial']


@pytest.mark.parametrize('raw', [b'', b'<title>Just a moment...</title>', b'<title>Login</title><form></form>'])
def test_invalid_html_is_not_empty_success(raw):
    with pytest.raises(pg.PokerError):
        pg.parse_board(raw, 1)
    with pytest.raises(pg.PokerError):
        pg.parse_post(raw, 123)


def test_cache_copy_expiry_and_bounded_capacity(monkeypatch):
    r = pg.Reader()
    calls = []
    monkeypatch.setattr(r, '_fetch', lambda path: calls.append(path) or fixture('board'))
    data = r.board(1)
    data['posts'].clear()
    assert len(r.board(1)['posts']) == 2
    assert len(calls) == 1
    r.cache[('board', 'free', 1)].fresh_until = 0
    assert len(r.board(1)['posts']) == 2
    assert len(calls) == 2
    monkeypatch.setattr(pg, 'CACHE_LIMIT', 2)
    r.board(2); r.board(3)
    assert len(r.cache) == 2


def test_duplicate_requests_share_one_fetch(monkeypatch):
    r = pg.Reader()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        return fixture('board')
    monkeypatch.setattr(r, '_fetch', fetch)
    with ThreadPoolExecutor(6) as pool:
        first = pool.submit(r.board, 1)
        assert entered.wait(2)
        rest = [pool.submit(r.board, 1) for _ in range(5)]
        release.set()
        results = [f.result(3) for f in [first] + rest]
    assert len(calls) == 1
    assert all(len(v['posts']) == 2 for v in results)
    assert not r.flights


def install_upstream(monkeypatch, status=200, headers=None, payload=None):
    calls = []
    class Session:
        def __init__(self, **kwargs):
            assert kwargs == {'impersonate': 'chrome', 'trust_env': False}
        def close(self): pass
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            chunk = payload if payload is not None else fixture('board')
            accepted = kwargs['content_callback'](chunk)
            if accepted != len(chunk):
                raise pg.requests.RequestsError('write aborted')
            return SimpleNamespace(status_code=status, headers=headers or {})
    monkeypatch.setattr(pg.requests, 'Session', Session)
    return calls


@pytest.mark.parametrize('status,header', [(403, {}), (429, {}), (200, {'cf-mitigated': 'challenge'})])
def test_challenge_stops_other_requests_and_is_not_cached_as_data(monkeypatch, status, header):
    calls = install_upstream(monkeypatch, status, header)
    r = pg.Reader()
    for page in [1, 1, 2]:
        with pytest.raises(pg.PokerError) as exc:
            r.board(page)
        assert exc.value.status == 503
    assert len(calls) == 1
    assert all(entry.value is None for entry in r.cache.values())


@pytest.mark.parametrize('status,expected', [(404, 404), (410, 404), (302, 502), (500, 502)])
def test_upstream_status_and_negative_cache(monkeypatch, status, expected):
    calls = install_upstream(monkeypatch, status)
    r = pg.Reader()
    for _ in range(2):
        with pytest.raises(pg.PokerError) as exc:
            r.board(1)
        assert exc.value.status == expected
    assert len(calls) == 1
    assert calls[0][1]['allow_redirects'] is False


def test_size_guard_rejects_during_receive(monkeypatch):
    monkeypatch.setattr(pg, 'MAX_HTML_BYTES', 2)
    install_upstream(monkeypatch, payload=b'123')
    with pytest.raises(pg.PokerError):
        pg.Reader().board(1)


@pytest.mark.parametrize('url', ['https://www.ipokergosu.com.evil.test/x', 'http://www.ipokergosu.com/x',
    'https://www.ipokergosu.com:8443/x', 'https://u:p@www.ipokergosu.com/x', 'https://127.0.0.1/x',
    'file:///etc/passwd', 'https://www.ipokergosu.com/\\evil', '//evil.test/x'])
def test_media_allowlist(url):
    assert media.image_url(url) is None


def test_sanitizer_strips_active_content_and_signs_only_approved_images():
    app = create_app()
    with app.test_request_context('/'):
        raw = '''<script>alert(1)</script><style>body{display:none}</style>
        <p class="hidden" style="color:red" onclick="evil()">안전한 글</p>
        <img src="https://www.ipokergosu.com/img2/test.webp" onerror="evil()" data-dccon-src="/media?src=evil">
        <img src="https://evil.test/track"><iframe src="https://evil.test"></iframe>
        <a aria-label="title"><span>링크가 아닌 댓글</span></a><a href="javascript:alert(1)">bad</a><a href="/free/123">내부 글</a>
        <a href="/strategy/123">다른 게시판</a>'''
        soup = BeautifulSoup(media.prepare_html(raw), 'html.parser')
        assert not soup.select('script,style,iframe,[onclick],[onerror],[style]')
        assert not soup.select('a[aria-label=title]')
        assert len(soup.select('img')) == 1
        img = soup.img
        assert not img.has_attr('src') and img.has_attr('hidden')
        assert not img.has_attr('data-dccon-src')
        q = parse_qs(urlsplit(img['data-body-image-src']).query)
        assert q['sig'][0] == media.signature(q['src'][0])
        assert soup.find('a', string='내부 글')['href'] == '/poker/free/123'
        assert soup.find('a', string='다른 게시판')['href'] == '/poker/strategy/123'
        assert not soup.find('a', string='bad').has_attr('href')
        assert not BeautifulSoup(sanitize_html_fragment('<img src="/poker/media?src=x">'), 'html.parser').img


def test_media_rejects_bad_signature_and_cross_purpose_token_before_dns(monkeypatch):
    def unexpected(*args, **kwargs): raise AssertionError('must not resolve')
    monkeypatch.setattr(media, 'resolve_media_target', unexpected)
    app = create_app()
    with app.test_request_context('/'):
        src = 'https://www.ipokergosu.com/img2/test.webp'
        assert media.build_image_response(src, '').status_code == 400
        assert media.build_image_response(src, preview_image_signature(src, app.secret_key)).status_code == 400


def test_media_rejects_nonpublic_dns(monkeypatch):
    monkeypatch.setattr(media, 'resolve_media_target', lambda *a, **k: None)
    app = create_app()
    with app.test_request_context('/'):
        src = 'https://www.ipokergosu.com/img2/test.webp'
        assert media.build_image_response(src, media.signature(src)).status_code == 400


def test_media_redirect_mime_and_size_guards(monkeypatch):
    state = {'status': 200, 'headers': {'Content-Type': 'image/webp'}, 'body': b'RIFF1234WEBPimage'}
    class Upstream:
        def __init__(self): self.status_code = state['status']; self.headers = state['headers']
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, *a): yield state['body']
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def mount(self, *a): pass
        def get(self, url, **kw):
            assert kw['allow_redirects'] is False
            return Upstream()
    monkeypatch.setattr(media, 'resolve_media_target', lambda *a, **k: object())
    monkeypatch.setattr(media, 'PinnedMediaAdapter', lambda *a: object())
    monkeypatch.setattr(media.requests, 'Session', Session)
    app = create_app()
    with app.test_request_context('/'):
        src = 'https://www.ipokergosu.com/img2/test.webp'
        def call(): return media.build_image_response(src, media.signature(src))
        assert call().status_code == 200
        state['status'] = 302
        assert call().status_code == 502
        state['status'] = 200; state['body'] = b'<svg onload="evil()">'
        assert call().status_code == 415
        state['headers']['Content-Length'] = '999999999'
        assert call().status_code == 413
        state['headers'].pop('Content-Length')
        monkeypatch.setattr(media, 'MAX_IMAGE_BYTES', 2)
        assert call().status_code == 413


def test_media_deadline_stops_trickling_upstream(monkeypatch):
    # 바이트가 조금씩 와서 읽기 제한이 계속 연장돼도 소켓 읽기마다 마감을 확인해 504로 끝낸다.
    reads = []
    class Raw:
        def read1(self, size):
            reads.append(size)
            time.sleep(0.05)
            return b'RIFF' if len(reads) == 1 else b'x'
    class Upstream:
        status_code = 200
        headers = {'Content-Type': 'image/webp'}
        raw = Raw()
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, *a):
            raise AssertionError('read1 must be used when the raw stream supports it')
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def mount(self, *a): pass
        def get(self, url, **kw): return Upstream()
    monkeypatch.setattr(media, 'resolve_media_target', lambda *a, **k: object())
    monkeypatch.setattr(media, 'PinnedMediaAdapter', lambda *a: object())
    monkeypatch.setattr(media.requests, 'Session', Session)
    monkeypatch.setattr(media, 'IMAGE_DEADLINE_SECONDS', 0.3)
    app = create_app()
    with app.test_request_context('/'):
        src = 'https://www.ipokergosu.com/img2/test.webp'
        started = time.monotonic()
        response = media.build_image_response(src, media.signature(src))
    assert response.status_code == 504
    assert time.monotonic() - started < 2
    assert 3 <= len(reads) < 40
    assert media._slots.acquire(timeout=0.1)
    media._slots.release()


@pytest.mark.parametrize('path', ['/poker/free?page=0', '/poker/free?page=10001', '/poker/free?page=oops',
    '/poker/free?page=-1', '/poker/free/0', '/poker/free/9999999999999'])
def test_routes_validate_before_fetch(monkeypatch, path):
    monkeypatch.setattr(poker_routes.reader, '_fetch', lambda *a: pytest.fail('unexpected fetch'))
    assert create_app().test_client().get(path).status_code == 400


def test_routes_render_and_return_to_source_page(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'board', lambda page, board_id='free': pg.parse_board(fixture('board'), page))
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: fixture('post'))
    monkeypatch.setattr(poker_routes, 'reader', reader)
    monkeypatch.setattr(reader, 'board', lambda page, board_id='free': pg.parse_board(fixture('board'), page))
    client = create_app().test_client()
    board = client.get('/poker/free?page=2')
    assert board.status_code == 200
    soup = BeautifulSoup(board.data, 'html.parser')
    assert soup.find('a', href='/poker/free/123?page=2')
    read = client.get('/poker/free/123?page=2')
    assert read.status_code == 200
    soup = BeautifulSoup(read.data, 'html.parser')
    assert soup.find('a', href='/poker/free?page=2')
    assert soup.select('#article-body img[data-body-image-src]')
    assert not soup.select('#article-body script')


def test_route_errors_and_partial_comments(monkeypatch):
    client = create_app().test_client()
    def fail(page, board_id='free'): raise pg.PokerError('잠시 제한됐어요', 503)
    monkeypatch.setattr(poker_routes.reader, 'board', fail)
    response = client.get('/poker/free')
    assert response.status_code == 503
    assert response.headers['Retry-After'] == '60'
    assert response.headers['Cache-Control'] == 'no-store'
    soup = BeautifulSoup(client.get('/poker/free?page=2').data, 'html.parser')
    assert soup.find('a', string='돌아가기')['href'] == '/poker/free?page=1'
    data = pg.parse_post(fixture('post'), 123)
    data['comments_partial'] = True; data['comment_count'] = 100
    monkeypatch.setattr(poker_routes.reader, 'post',
                        lambda pid, board_id='free', prepare=None: pg.Reader._prepare(data, prepare))
    response = client.get('/poker/free/123')
    assert response.status_code == 200
    assert '일부'.encode() in response.data
    assert b'https://www.pokergosu.com/free/123#comment' in response.data


def test_empty_list_and_changed_markup_are_distinct():
    raw = '<title>자유 게시판 - 포커고수</title><table><tr><th>제목</th><th>글쓴이</th><th>날짜</th></tr></table>'.encode()
    assert pg.parse_board(raw, 1) == {'posts': [], 'has_next': False}
    with pytest.raises(pg.PokerError):
        pg.parse_board('<title>자유 게시판</title><table><tr><td>다른 표</td></tr></table>'.encode(), 1)


def test_real_curl_callback_aborts_oversize_without_caching_success(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    payload = fixture('board') + b' ' * 262144
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(pg, 'BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(pg, 'MAX_HTML_BYTES', len(fixture('board')) + 1)
    reader = pg.Reader()
    try:
        with pytest.raises(pg.PokerError):
            reader.board(1)
        assert reader.cache[('board', 'free', 1)].value is None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_malformed_body_and_comment_links_keep_text(monkeypatch):
    data = pg.parse_post(fixture('post'), 123)
    data['html'] = '<p>안전한 본문 <a href="https://[">잘못된 링크</a></p>'
    data['comments'][0]['html'] = '<a href="https://[">댓글 링크</a>'
    monkeypatch.setattr(poker_routes.reader, 'post',
                        lambda pid, board_id='free', prepare=None: pg.Reader._prepare(data, prepare))
    response = create_app().test_client().get('/poker/free/123')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    assert '안전한 본문' in soup.select_one('#article-body').get_text()
    assert '댓글 링크' in soup.select_one('.comment-list').get_text()
    assert not soup.select('a[href="https://["]')
