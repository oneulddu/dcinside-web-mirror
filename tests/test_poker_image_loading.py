from concurrent.futures import ThreadPoolExecutor
from html import escape
from email.message import Message
from io import BytesIO
import threading
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import poker_media as media
from app.services import pokergosu as pg
from app.services.media_proxy import ResolvedMediaTarget

SRC = 'https://www.ipokergosu.com/img2/example.webp'


@pytest.mark.parametrize('width,height,expected', [
    ('880', '656', True), ('10000', '1', True), ('0', '20', False),
    ('-1', '20', False), ('10px', '20', False), ('10', '', False),
    ('10001', '20', False), ('1e2', '20', False), ('10', '1" onload="evil', False),
])
def test_dimensions_reserve_only_valid_complete_image_sizes(width, height, expected):
    raw = f'<img src="{SRC}" width="{escape(width)}" height="{escape(height)}" style="width:100vw" onload="evil()">'
    with create_app().test_request_context('/'):
        img = BeautifulSoup(media.prepare_html(raw), 'lxml').img
    assert ('width' in img.attrs) == expected
    assert ('height' in img.attrs) == expected
    assert not img.has_attr('style') and not img.has_attr('onload')
    assert img['loading'] == 'lazy' and not img.has_attr('src')
    if expected:
        assert (img['width'], img['height']) == (width, height)


def test_body_and_appended_comments_use_signed_images_and_dimensions(monkeypatch, poker_upstream):
    fixtures = Path(__file__).parent / 'fixtures/pokergosu'
    raw = (fixtures / 'comments-page-1.html').read_bytes().replace(
        b'/img2/test.webp"', b'/img2/test.webp" width="120" height="90"')
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', poker_upstream(raw))
    monkeypatch.setattr(poker_routes, 'reader', reader)
    client = create_app().test_client()
    response = client.get('/poker/best/123')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'lxml')
    img = soup.select_one('.poker-comment-body img')
    assert (img['width'], img['height']) == ('120', '90')
    scripts = [n.get('src', '') for n in soup.select('script')]
    retry = next(i for i, s in enumerate(scripts) if 'poker_images.js' in s)
    assert retry < next(i for i, s in enumerate(scripts) if 'read_state.js' in s)
    assert soup.select('script')[retry + 1].get('src') is None  # comment hydration
    payload = client.get('/poker/best/123/comments?cpage=1').get_json()
    img = BeautifulSoup(payload['comments'][0]['html'], 'lxml').img
    assert (img['width'], img['height']) == ('120', '90')
    assert img['data-body-image-src'].startswith('/poker/media?')
    assert img.has_attr('hidden') and not img.has_attr('src')


def test_busy_response_is_temporary_and_does_not_start_dns(monkeypatch, caplog):
    slots = threading.BoundedSemaphore(4)
    monkeypatch.setattr(media, '_slots', slots)
    monkeypatch.setattr(media, 'resolve_media_target', lambda *a, **kw: pytest.fail('busy must not resolve'))
    for _ in range(4):
        assert slots.acquire(blocking=False)
    app = create_app()
    try:
        with app.test_request_context('/'):
            sig = media.signature(SRC)
            response = media.build_image_response(SRC, sig)
        assert response.status_code == 503
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['Retry-After'] == '2'
        assert 'reason=busy' in caplog.text and 'elapsed=' in caplog.text
        assert SRC not in caplog.text and sig not in caplog.text
    finally:
        for _ in range(4): slots.release()


def test_concurrent_requests_keep_four_slots_and_release_after_transport_failure(monkeypatch):
    slots = threading.BoundedSemaphore(4)
    monkeypatch.setattr(media, '_slots', slots)
    barrier = threading.Barrier(4)
    entered = []
    def resolve(*args, **kwargs):
        entered.append(1)
        barrier.wait(timeout=5)
        raise OSError('private-url-or-response')
    monkeypatch.setattr(media, 'resolve_media_target', resolve)
    app = create_app()
    def request():
        with app.test_request_context('/'):
            response = media.build_image_response(SRC, media.signature(SRC))
            return response.status_code, response.headers.get('Cache-Control')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: request(), range(4))) == [(502, 'no-store')] * 4
    assert len(entered) == 4
    for _ in range(4): assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)
    for _ in range(4): slots.release()


def test_browser_image_retry_state_machine():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run([node, str(Path(__file__).parent / 'javascript/poker_images.test.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_slow_dns_spends_the_image_budget_before_http(monkeypatch):
    now = [0]
    def resolve(*args, **kwargs):
        now[0] += media.IMAGE_DEADLINE_SECONDS
        return ResolvedMediaTarget('https', 'ipokergosu.com', 443, 'ipokergosu.com', ('8.8.8.8',))
    monkeypatch.setattr(media, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(media, 'resolve_media_target', resolve)
    monkeypatch.setattr(media._MediaSession, 'get', lambda *a, **kw: pytest.fail('deadline passed'))
    with create_app().test_request_context('/'):
        response = media.build_image_response(SRC, media.signature(SRC))
    assert response.status_code == 504 and response.headers['Cache-Control'] == 'no-store'


def test_each_pinned_attempt_spends_the_remaining_budget(monkeypatch):
    now, attempts = [0], []
    def resolve(url, **kwargs):
        now[0] = 18
        host = urlsplit(url).hostname
        return ResolvedMediaTarget('https', host, 443, host, ('8.8.8.8', '1.1.1.1', '8.8.4.4'))
    def send(adapter, request, **kwargs):
        attempts.append(kwargs['timeout'].total)
        now[0] += 1
        raise media.requests.Timeout()
    monkeypatch.setattr(media, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(media, 'resolve_media_target', resolve)
    monkeypatch.setattr(media.requests.adapters.HTTPAdapter, 'send', send)
    with create_app().test_request_context('/'):
        assert media.build_image_response(SRC, media.signature(SRC)).status_code == 504
    assert attempts == [2, 1]


def test_pooled_images_repin_each_request_and_isolate_upstream_cookies(monkeypatch):
    # Replace only DNS and socket I/O; requests must still prepare the GET and select the TLS pool.
    addresses = iter(['8.8.8.8', '1.1.1.1', '1.1.1.1', None])
    def resolve(url, *, require_allowed_media_host):
        assert not require_allowed_media_host
        host = urlsplit(url).hostname
        ip = next(addresses)
        return ResolvedMediaTarget('https', host, 443, host, (ip,)) if ip else None
    seen, pools = [], []
    def send(adapter, request, **kwargs):
        assert kwargs['verify'] and not kwargs['proxies']
        pool = adapter.get_connection_with_tls_context(request, verify=True)
        pools.append(pool)
        seen.append((adapter, request.headers['Host'], pool.host, pool.assert_hostname,
                     pool.conn_kw['server_hostname'], request.headers.get('Cookie')))
        response = media.requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'image/png'
        response.raw = BytesIO(b'\x89PNG\r\n\x1a\nimage')
        headers = Message()
        headers['Set-Cookie'] = 'private=first-user; Domain=ipokergosu.com; Path=/'
        response.raw._original_response = SimpleNamespace(msg=headers)
        return response
    monkeypatch.setattr(media, 'resolve_media_target', resolve)
    monkeypatch.setattr(media.requests.adapters.HTTPAdapter, 'send', send)
    with create_app().test_request_context('/'):
        for url, status in [(SRC, 200), (SRC, 200), (SRC.replace('www.', ''), 200), (SRC, 400)]:
            assert media.build_image_response(url, media.signature(url)).status_code == status
    assert seen[0][0] is seen[1][0] is seen[2][0]
    assert len(set(pools)) == 3
    assert [r[1:] for r in seen] == [
        ('www.ipokergosu.com', '8.8.8.8', 'www.ipokergosu.com', 'www.ipokergosu.com', None),
        ('www.ipokergosu.com', '1.1.1.1', 'www.ipokergosu.com', 'www.ipokergosu.com', None),
        ('ipokergosu.com', '1.1.1.1', 'ipokergosu.com', 'ipokergosu.com', None)]
