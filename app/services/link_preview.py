import hashlib
import html
import http.client
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .cache_utils import cache_get, cache_set_after_insert, env_int
from .media_proxy import PC_USER_AGENT, resolve_media_target


PREVIEW_MAX_BYTES = 131072
PREVIEW_TIMEOUT = (5, 5)
PREVIEW_CACHE_TTL = 24 * 3600
PREVIEW_FAILURE_CACHE_TTL = 300
PREVIEW_CACHE_MAX_ITEMS = 2048
PREVIEW_MAX_REDIRECTS = 3
PREVIEW_RATE_WINDOW_SECONDS = env_int("MIRROR_LINK_PREVIEW_RATE_WINDOW", 10)
PREVIEW_RATE_MAX_CALLS = env_int("MIRROR_LINK_PREVIEW_RATE_MAX", 20)
PREVIEW_MAX_CONCURRENCY = max(env_int("MIRROR_LINK_PREVIEW_MAX_CONCURRENCY", 4), 1)
PREVIEW_DEADLINE_SECONDS = max(env_int("MIRROR_LINK_PREVIEW_DEADLINE", 8), 1)

RATE_LIMITED = object()
_CACHE_FAILURE = object()

_preview_cache = {}
_preview_cache_lock = threading.Lock()
_url_locks = tuple(threading.Lock() for _ in range(64))

_probe_rate = {"window_start": 0.0, "used": 0}
_probe_rate_lock = threading.Lock()
_preview_concurrency = threading.BoundedSemaphore(PREVIEW_MAX_CONCURRENCY)
_preview_dns_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="link-preview-dns")
_preview_dns_slot = threading.BoundedSemaphore(1)


class _PreviewDeadlineExceeded(Exception):
    pass


def _force_close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _DeadlineSocketGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._socket = None
        self._expired = False

    def track(self, sock):
        with self._lock:
            if self._expired:
                _force_close_socket(sock)
                raise _PreviewDeadlineExceeded
            self._socket = sock

    def untrack(self, sock):
        with self._lock:
            if self._socket is sock:
                self._socket = None

    def expire(self):
        with self._lock:
            self._expired = True
            sock = self._socket
            self._socket = None
        if sock is not None:
            _force_close_socket(sock)

    def close_current(self):
        with self._lock:
            sock = self._socket
            self._socket = None
        if sock is not None:
            _force_close_socket(sock)


def _acquire_probe_slot():
    """outbound HTTP 요청 1회의 전역 예산을 확보한다."""
    now = time.monotonic()
    with _probe_rate_lock:
        if now - _probe_rate["window_start"] >= PREVIEW_RATE_WINDOW_SECONDS:
            _probe_rate["window_start"] = now
            _probe_rate["used"] = 0
        if _probe_rate["used"] >= PREVIEW_RATE_MAX_CALLS:
            return False
        _probe_rate["used"] += 1
        return True


def normalize_preview_url(value):
    """허용된 https 절대 URL이면 정규화 문자열을, 아니면 None을 반환한다."""
    url = str(value or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return parsed._replace(scheme="https").geturl()


def is_valid_preview_url(value):
    return normalize_preview_url(value) is not None


def _cache_key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cached_result(key):
    cached = cache_get(_preview_cache, _preview_cache_lock, key)
    if cached is _CACHE_FAILURE:
        return True, None
    if cached is not None:
        return True, cached
    return False, None


def _clean_text(value, limit):
    text = html.unescape(" ".join(str(value or "").split())).strip()
    return text[:limit].rstrip() or None


def _meta_content(soup, key):
    wanted = key.lower()
    for tag in soup.find_all("meta"):
        marker = tag.get("property") or tag.get("name")
        if str(marker or "").strip().lower() == wanted:
            return tag.get("content")
    return None


def _parse_preview(body, host):
    soup = BeautifulSoup(body, "lxml")
    title = _clean_text(_meta_content(soup, "og:title"), 150)
    if not title and soup.title:
        title = _clean_text(soup.title.get_text(" ", strip=True), 150)
    if not title:
        return None
    return {
        "title": title,
        "description": _clean_text(_meta_content(soup, "og:description"), 200),
        "site_name": _clean_text(_meta_content(soup, "og:site_name"), 150),
        "host": host,
    }


def _deadline_remaining(started_at):
    return PREVIEW_DEADLINE_SECONDS - (time.monotonic() - started_at)


def _check_deadline(started_at):
    remaining = _deadline_remaining(started_at)
    if remaining <= 0:
        raise _PreviewDeadlineExceeded
    return remaining


def _resolve_target(url):
    try:
        return resolve_media_target(url, require_allowed_media_host=False)
    finally:
        _preview_dns_slot.release()


def _resolve_target_with_deadline(url, started_at):
    if not _preview_dns_slot.acquire(timeout=_check_deadline(started_at)):
        raise _PreviewDeadlineExceeded
    try:
        future = _preview_dns_executor.submit(_resolve_target, url)
    except BaseException:
        _preview_dns_slot.release()
        raise
    try:
        target = future.result(timeout=_check_deadline(started_at))
    except FutureTimeoutError as exc:
        if future.cancel():
            _preview_dns_slot.release()
        raise _PreviewDeadlineExceeded from exc
    _check_deadline(started_at)
    return target


def _read_limited_html(response, started_at):
    _check_deadline(started_at)
    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "text/html":
        return None
    chunks = []
    remaining = PREVIEW_MAX_BYTES
    while remaining > 0:
        _check_deadline(started_at)
        chunk = response.read(min(8192, remaining))
        _check_deadline(started_at)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _connect_pinned_target(target, started_at, socket_guard):
    ssl_context = ssl.create_default_context()
    last_error = None
    for address in target.addresses:
        raw_socket = None
        try:
            raw_socket = socket.create_connection(
                (address, target.port),
                timeout=min(PREVIEW_TIMEOUT[0], _check_deadline(started_at)),
            )
            socket_guard.track(raw_socket)
            raw_socket.settimeout(min(PREVIEW_TIMEOUT[1], _check_deadline(started_at)))
            tls_socket = ssl_context.wrap_socket(
                raw_socket,
                server_hostname=target.hostname,
                do_handshake_on_connect=False,
            )
            raw_socket = None
            socket_guard.track(tls_socket)
            tls_socket.settimeout(min(PREVIEW_TIMEOUT[1], _check_deadline(started_at)))
            tls_socket.do_handshake()
            _check_deadline(started_at)
            return tls_socket
        except (OSError, ssl.SSLError, ValueError, _PreviewDeadlineExceeded) as exc:
            last_error = exc
            socket_guard.close_current()
            if raw_socket is not None:
                _force_close_socket(raw_socket)
            _check_deadline(started_at)
    if last_error is not None:
        raise last_error
    raise OSError("no validated preview address")


def _request_preview_target(url, target, started_at, socket_guard):
    parsed = urlparse(url)
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"

    tls_socket = _connect_pinned_target(target, started_at, socket_guard)
    connection = http.client.HTTPConnection(
        target.hostname,
        target.port,
        timeout=min(PREVIEW_TIMEOUT[1], _check_deadline(started_at)),
    )
    connection.sock = tls_socket
    response = None
    try:
        tls_socket.settimeout(min(PREVIEW_TIMEOUT[1], _check_deadline(started_at)))
        connection.request(
            "GET",
            request_target,
            headers={
                "Host": target.host_header,
                "User-Agent": PC_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        _check_deadline(started_at)
        body = None
        if 200 <= response.status < 300:
            body = _read_limited_html(response, started_at)
        return response.status, response.headers, body
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()
            socket_guard.untrack(tls_socket)


def _fetch_uncached(url):
    if not _preview_concurrency.acquire(blocking=False):
        return RATE_LIMITED

    started_at = time.monotonic()
    socket_guard = _DeadlineSocketGuard()
    deadline_timer = threading.Timer(
        max(_deadline_remaining(started_at), 0),
        socket_guard.expire,
    )
    deadline_timer.daemon = True
    deadline_timer.start()
    try:
        current_url = url
        redirects = 0
        while True:
            current_url = normalize_preview_url(current_url)
            if not current_url:
                return None
            _check_deadline(started_at)
            if not _acquire_probe_slot():
                return RATE_LIMITED
            _check_deadline(started_at)

            target = _resolve_target_with_deadline(current_url, started_at)
            if target is None:
                return None
            _check_deadline(started_at)

            status_code, headers, body = _request_preview_target(
                current_url,
                target,
                started_at,
                socket_guard,
            )
            location = headers.get("Location")
            if status_code in {301, 302, 303, 307, 308} and location:
                if redirects >= PREVIEW_MAX_REDIRECTS:
                    return None
                redirects += 1
                current_url = urljoin(current_url, location)
                continue
            if not 200 <= status_code < 300 or body is None:
                return None
            return _parse_preview(body, target.hostname)
    except (
        OSError,
        ssl.SSLError,
        http.client.HTTPException,
        UnicodeError,
        ValueError,
        _PreviewDeadlineExceeded,
    ):
        return None
    finally:
        deadline_timer.cancel()
        socket_guard.close_current()
        _preview_concurrency.release()


def fetch_preview(url):
    normalized_url = normalize_preview_url(url)
    if not normalized_url:
        return None
    key = _cache_key(normalized_url)
    found, cached = _cached_result(key)
    if found:
        return cached

    lock = _url_locks[int(key[:8], 16) % len(_url_locks)]
    with lock:
        found, cached = _cached_result(key)
        if found:
            return cached
        result = _fetch_uncached(normalized_url)
        if result is RATE_LIMITED:
            return RATE_LIMITED
        cache_set_after_insert(
            _preview_cache,
            _preview_cache_lock,
            key,
            result if result is not None else _CACHE_FAILURE,
            PREVIEW_CACHE_TTL if result is not None else PREVIEW_FAILURE_CACHE_TTL,
            PREVIEW_CACHE_MAX_ITEMS,
        )
        return result
