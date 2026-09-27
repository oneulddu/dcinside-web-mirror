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

from .html_sanitizer import sanitize_html_tree
from .media_proxy import PinnedMediaAdapter, resolve_media_target

MEDIA_HOSTS = frozenset({'www.ipokergosu.com', 'ipokergosu.com'})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
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


def prepare_html(raw):
    soup = BeautifulSoup(raw, 'lxml')
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        # Never trust original layout classes or lazy-loading/proxy attributes.
        for attr in list(node.attrs):
            if attr == 'class' or attr.startswith('data-'):
                del node[attr]
        if node.name in {'iframe', 'video', 'audio', 'object', 'embed', 'source'}:
            node.decompose()
            continue
        if node.name == 'img':
            src = image_url(node.get('src'))
            if not src:
                node.decompose()
                continue
            node.attrs = {'class': 'body-image', 'data-body-image-src': url_for('poker.media', src=src, sig=signature(src)),
                          'alt': node.get('alt') or '본문 이미지', 'hidden': '', 'loading': 'lazy', 'decoding': 'async'}
        elif node.name == 'a' and not node.get('href'):
            node.unwrap()
        elif node.name == 'a' and node.get('href'):
            try:
                href = urljoin('https://www.pokergosu.com/', node['href'])
                parsed = urlsplit(href)
                match = re.fullmatch(r'/free/(\d+)', parsed.path)
                if parsed.scheme in ('http', 'https') and parsed.hostname in {'pokergosu.com', 'www.pokergosu.com'} and match:
                    href = url_for('poker.read', pid=int(match.group(1)))
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


def build_image_response(src, sig):
    normalized = image_url(src)
    if not normalized or not re.fullmatch(r'[0-9a-f]{64}', sig or '') or not hmac.compare_digest(signature(normalized), sig):
        return Response(status=400)
    if not _slots.acquire(timeout=1):
        return Response(status=503)
    try:
        target = resolve_media_target(normalized, require_allowed_media_host=False)
        if target is None:
            return Response(status=400)
        started = time.monotonic()
        with requests.Session() as session:
            session.trust_env = False
            session.mount('https://', PinnedMediaAdapter(target))
            with session.get(normalized, timeout=(5, 10), stream=True, allow_redirects=False,
                             headers={'Referer': 'https://www.pokergosu.com/', 'Accept-Encoding': 'identity'}) as upstream:
                if upstream.status_code != 200:
                    return Response(status=502)
                declared = upstream.headers.get('Content-Length')
                if declared and (not declared.isdigit() or int(declared) > MAX_IMAGE_BYTES):
                    return Response(status=413)
                body = bytearray()
                for chunk in upstream.iter_content(65536):
                    if time.monotonic() - started > 20:
                        return Response(status=504)
                    if len(body) + len(chunk) > MAX_IMAGE_BYTES:
                        return Response(status=413)
                    body.extend(chunk)
                mime = _image_type(body)
                if not mime or upstream.headers.get('Content-Type', '').split(';')[0].strip().lower() != mime:
                    return Response(status=415)
                return Response(bytes(body), mimetype=mime, headers={
                    'Cache-Control': 'public, max-age=300', 'X-Content-Type-Options': 'nosniff',
                    'Content-Security-Policy': "default-src 'none'; sandbox",
                })
    except (requests.RequestException, OSError, ValueError):
        return Response(status=502)
    finally:
        _slots.release()
