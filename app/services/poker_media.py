"""Signed, image-only proxy with a Pokergosu-specific host policy."""
import hashlib
import hmac
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from flask import Response, current_app, url_for
import requests

from .html_sanitizer import YOUTUBE_IFRAME_HOSTS, normalize_safe_iframe_src, sanitize_html_tree
from .poker_boards import BASE_URL, poker_link
from .media_proxy import MEDIA_CACHE_MAX_AGE, PinnedMediaAdapter, resolve_media_target

MEDIA_HOSTS = frozenset({'www.ipokergosu.com', 'ipokergosu.com'})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_DEADLINE_SECONDS = 20
_slots = threading.BoundedSemaphore(4)


def image_url(value):
    try:
        value = urljoin('https://www.pokergosu.com/', str(value or '').strip())
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in MEDIA_HOSTS
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment
                or re.search(r'[\x00-\x20\\]', value)):
            return None
        return value
    except ValueError:
        return None


def signature(src):
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, ('poker-media\n' + src).encode(), hashlib.sha256).hexdigest()


def youtube_iframe_src(value):
    """Poker accepts only exact embed URLs, before and after shared normalization."""
    def valid(url):
        parsed = urlsplit(url)
        return (parsed.scheme in ('', 'https') and parsed.hostname in YOUTUBE_IFRAME_HOSTS
                and parsed.netloc.lower() in YOUTUBE_IFRAME_HOSTS
                and parsed.username is None and parsed.password is None and parsed.port is None
                and re.fullmatch(r'/embed/[A-Za-z0-9_-]{11}', parsed.path))

    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f\\]', value):
        return None
    try:
        if not valid(value):
            return None
        normalized = normalize_safe_iframe_src(value)
        if normalized and valid(normalized):
            return 'https://www.youtube-nocookie.com' + urlsplit(normalized).path
    except ValueError:
        pass
    return None


def prepare_html(raw, *, base_url=BASE_URL + '/', allow_youtube=False):
    soup = BeautifulSoup(raw, 'lxml')
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        # Never trust original layout classes or lazy-loading/proxy attributes.
        for attr in list(node.attrs):
            if attr == 'class' or attr.startswith('data-'):
                del node[attr]
        if node.name == 'iframe' and allow_youtube and (src := youtube_iframe_src(node.get('src'))):
            node.clear()
            node.attrs = {'src': src, 'loading': 'lazy', 'title': 'YouTube 동영상',
                          'referrerpolicy': 'strict-origin-when-cross-origin', 'allowfullscreen': ''}
        elif node.name in {'iframe', 'video', 'audio', 'object', 'embed', 'source'}:
            node.decompose()
            continue
        if node.name == 'img':
            src = image_url(node.get('src'))
            if not src:
                node.decompose()
                continue
            # Preserve layout so offscreen lazy images do not all load at once.
            dimensions = {key: node.get(key, '') for key in ('width', 'height')}
            if not all(re.fullmatch(r'[0-9]{1,5}', value) and 0 < int(value) <= 10000
                       for value in dimensions.values()):
                dimensions = {}
            node.attrs = {'class': 'body-image', 'data-body-image-src': url_for('poker.media', src=src, sig=signature(src)),
                          'alt': node.get('alt') or '본문 이미지', 'hidden': '', 'loading': 'lazy', 'decoding': 'async'}
            node.attrs.update(dimensions)
        elif node.name == 'a' and not node.get('href'):
            node.unwrap()
        elif node.name == 'a' and node.get('href'):
            try:
                href = urljoin(base_url, node['href'])
                link = poker_link(href)
                if link:
                    params = {'board_id': link['board_id']}
                    if link['page'] is not None:
                        params['page'] = link['page']
                    if link['pid'] is not None:
                        params['pid'] = link['pid']
                    if link['fragment']:
                        params['_anchor'] = link['fragment']
                    href = url_for('poker.read' if link['pid'] else 'poker.board', **params)
            except ValueError:
                href = ''
            node['href'] = href
    sanitize_html_tree(soup, media_prefixes=('/poker/media?',))
    return str(soup)


def _image_type(body):
    if body.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if body[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _image_error(status, reason, *, src='', started=None, upstream_status=None):
    # Never log signed queries, paths, exception messages, or upstream bodies.
    if status >= 500 or status in (413, 415):
        current_app.logger.warning(
            'poker media rejected reason=%s status=%s host=%s upstream_status=%s elapsed=%.3f',
            reason, status, urlsplit(src).hostname or '', upstream_status,
            time.monotonic() - started if started is not None else 0,
        )
    headers = {'Cache-Control': 'no-store'}
    if status == 503:
        headers['Retry-After'] = '2'
    return Response(status=status, headers=headers)


def build_image_response(src, sig):
    normalized = image_url(src)
    if not normalized or not re.fullmatch(r'[0-9a-f]{64}', sig or '') or not hmac.compare_digest(signature(normalized), sig):
        return _image_error(400, 'invalid_signature')
    started = time.monotonic()
    if not _slots.acquire(timeout=1):
        return _image_error(503, 'busy', src=normalized, started=started)
    try:
        target = resolve_media_target(normalized, require_allowed_media_host=False)
        if target is None:
            return _image_error(400, 'invalid_target')
        fetch_started = time.monotonic()
        with requests.Session() as session:
            session.trust_env = False
            session.mount('https://', PinnedMediaAdapter(target))
            with session.get(normalized, timeout=(5, 10), stream=True, allow_redirects=False,
                             headers={'Referer': 'https://www.pokergosu.com/', 'Accept-Encoding': 'identity'}) as upstream:
                response = _read_image(upstream, fetch_started)
                if response.status_code != 200:
                    reason = {413: 'too_large', 415: 'invalid_image', 504: 'deadline'}.get(
                        response.status_code, 'upstream_status')
                    return _image_error(response.status_code, reason, src=normalized,
                                        started=started, upstream_status=upstream.status_code)
                return response
    except (requests.RequestException, OSError, ValueError):
        return _image_error(502, 'transport', src=normalized, started=started)
    finally:
        _slots.release()


def _chunks(upstream):
    """Yield bytes as soon as each socket read returns.

    iter_content() keeps reading until a whole chunk is filled, so an upstream that trickles one
    byte at a time never reaches the deadline check. read1() returns after one successful recv,
    which bounds the wait between checks by the 10-second read timeout.
    """
    read1 = getattr(getattr(upstream, 'raw', None), 'read1', None)
    if read1 is None:
        yield from upstream.iter_content(65536)
        return
    while True:
        chunk = read1(65536)
        if not chunk:
            return
        yield chunk


def _read_image(upstream, started):
    if upstream.status_code != 200:
        return Response(status=502)
    declared = upstream.headers.get('Content-Length')
    if declared and (not declared.isdigit() or int(declared) > MAX_IMAGE_BYTES):
        return Response(status=413)
    body = bytearray()
    for chunk in _chunks(upstream):
        if time.monotonic() - started > IMAGE_DEADLINE_SECONDS:
            return Response(status=504)
        if len(body) + len(chunk) > MAX_IMAGE_BYTES:
            return Response(status=413)
        body.extend(chunk)
    mime = _image_type(body)
    if not mime or upstream.headers.get('Content-Type', '').split(';')[0].strip().lower() != mime:
        return Response(status=415)
    return Response(bytes(body), mimetype=mime, headers={
        'Cache-Control': f'public, max-age={MEDIA_CACHE_MAX_AGE}', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
    })
