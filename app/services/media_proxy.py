from dataclasses import dataclass
import ipaddress
import os
import re
import socket
import tempfile
import threading
import time
from html import escape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from flask import Response, stream_with_context, url_for


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


HTTP_TIMEOUT = _env_int("MIRROR_HTTP_TIMEOUT", 20)
MEDIA_CACHE_MAX_AGE = _env_int("MIRROR_MEDIA_CACHE_MAX_AGE", 86400)
MEDIA_MAX_BYTES = _env_int("MIRROR_MEDIA_MAX_BYTES", 50 * 1024 * 1024)
MEDIA_CHUNK_BYTES = max(_env_int("MIRROR_MEDIA_CHUNK_BYTES", 256 * 1024), 16 * 1024)
MEDIA_STREAMING_MIN_BYTES = max(_env_int("MIRROR_MEDIA_STREAMING_MIN_BYTES", 1024 * 1024), 0)
MEDIA_REDIRECT_LIMIT = _env_int("MIRROR_MEDIA_REDIRECT_LIMIT", 3)
MEDIA_DNS_CACHE_TTL = max(_env_int("MIRROR_MEDIA_DNS_CACHE_TTL", 30), 0)
MEDIA_DNS_CACHE_MAX_ITEMS = max(_env_int("MIRROR_MEDIA_DNS_CACHE_MAX_ITEMS", 512), 0)
MEDIA_HTTP_POOL_MAXSIZE = max(_env_int("MIRROR_MEDIA_HTTP_POOL_MAXSIZE", 16), 1)
MEDIA_ALLOWED_HOST_SUFFIXES = tuple(
    suffix.strip().lower().lstrip(".")
    for suffix in os.getenv("MIRROR_MEDIA_ALLOWED_HOST_SUFFIXES", "dcinside.com,dcinside.co.kr").split(",")
    if suffix.strip()
)
PC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
_ORIGINAL_REQUESTS_GET = requests.get
_MEDIA_SESSION_LOCAL = threading.local()
_PUBLIC_HOST_CACHE = {}
_PUBLIC_HOST_CACHE_LOCK = threading.Lock()
_MEDIA_SPOOL_MEMORY_BYTES = 1024 * 1024
_PINNED_ADDRESS_ATTEMPT_LIMIT = 2


class UnsafeMediaAddress(requests.RequestException):
    pass


@dataclass(frozen=True)
class ResolvedMediaTarget:
    scheme: str
    hostname: str
    port: int
    host_header: str
    addresses: tuple


class PinnedMediaAdapter(HTTPAdapter):
    def __init__(self, target):
        super().__init__(
            pool_connections=MEDIA_HTTP_POOL_MAXSIZE,
            pool_maxsize=MEDIA_HTTP_POOL_MAXSIZE,
        )
        self.target = target
        self._active_address = None

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        host_params["host"] = self._active_address
        if host_params["scheme"] == "https":
            pool_kwargs["assert_hostname"] = self.target.hostname
            pool_kwargs["server_hostname"] = self.target.hostname
        return self.poolmanager.connection_from_host(
            **host_params,
            pool_kwargs=pool_kwargs,
        )

    def send(self, request, **kwargs):
        kwargs["proxies"] = {}
        last_error = None
        for address in self.target.addresses:
            prepared = request.copy()
            prepared.headers["Host"] = self.target.host_header
            self._active_address = address
            try:
                return super().send(prepared, **kwargs)
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
                requests.exceptions.Timeout,
            ) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise UnsafeMediaAddress("no validated media address")


def _media_http_session():
    session = getattr(_MEDIA_SESSION_LOCAL, "session", None)
    if session is not None:
        return session

    session = requests.Session()
    session.trust_env = False
    _MEDIA_SESSION_LOCAL.session = session
    return session


def _pinned_media_adapter(target):
    adapter = getattr(_MEDIA_SESSION_LOCAL, "adapter", None)
    if adapter is None:
        adapter = PinnedMediaAdapter(target)
        _MEDIA_SESSION_LOCAL.adapter = adapter
    else:
        adapter.target = target
    return adapter


def _http_get(url, **kwargs):
    if requests.get is not _ORIGINAL_REQUESTS_GET:
        return requests.get(url, **kwargs)
    target = resolve_media_target(url)
    if target is None:
        raise UnsafeMediaAddress("media host did not resolve exclusively to public addresses")
    session = _media_http_session()
    session.mount(f"{target.scheme}://", _pinned_media_adapter(target))
    try:
        session.cookies.clear()
        return session.get(url, **kwargs)
    finally:
        session.cookies.clear()


def build_pc_view_referer(board, pid, kind=None):
    board_id = (board or "").strip()
    doc_id = _safe_int(pid, 0)
    board_kind = (kind or "").strip().lower()
    if not board_id or doc_id <= 0:
        return "https://gall.dcinside.com/"
    if board_kind == "minor":
        return f"https://gall.dcinside.com/mgallery/board/view/?id={board_id}&no={doc_id}"
    if board_kind == "mini":
        return f"https://gall.dcinside.com/mini/board/view/?id={board_id}&no={doc_id}"
    if board_kind == "person":
        return f"https://gall.dcinside.com/person/board/view/?id={board_id}&no={doc_id}"
    return f"https://gall.dcinside.com/board/view/?id={board_id}&no={doc_id}"


def build_mobile_view_referer(board, pid):
    board_id = (board or "").strip()
    doc_id = _safe_int(pid, 0)
    if not board_id or doc_id <= 0:
        return "https://m.dcinside.com/"
    return f"https://m.dcinside.com/board/{board_id}/{doc_id}"


def is_allowed_media_host(hostname):
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in MEDIA_ALLOWED_HOST_SUFFIXES)


def _resolve_public_hostname(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _prune_public_host_cache_locked(now):
    expired_keys = [
        key
        for key, entry in _PUBLIC_HOST_CACHE.items()
        if float(entry.get("expires_at", 0.0) or 0.0) <= now
    ]
    for key in expired_keys:
        _PUBLIC_HOST_CACHE.pop(key, None)

    overflow = len(_PUBLIC_HOST_CACHE) - MEDIA_DNS_CACHE_MAX_ITEMS
    if overflow <= 0:
        return

    oldest_keys = sorted(
        _PUBLIC_HOST_CACHE,
        key=lambda key: float(_PUBLIC_HOST_CACHE[key].get("expires_at", 0.0) or 0.0),
    )[:overflow]
    for key in oldest_keys:
        _PUBLIC_HOST_CACHE.pop(key, None)


def is_public_hostname(hostname):
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if MEDIA_DNS_CACHE_TTL <= 0 or MEDIA_DNS_CACHE_MAX_ITEMS <= 0:
        return _resolve_public_hostname(host)

    now = time.time()
    with _PUBLIC_HOST_CACHE_LOCK:
        _prune_public_host_cache_locked(now)
        cached = _PUBLIC_HOST_CACHE.get(host)
        if cached and cached["expires_at"] > now:
            return bool(cached["value"])

    result = _resolve_public_hostname(host)
    # Public results are intentionally not cached: media URL validation is part
    # of the SSRF/DNS-rebinding boundary, so each allowed request must see the
    # current DNS answer. Cache only non-public failures briefly to reduce
    # repeated bad lookups without weakening the allow path.
    if not result:
        with _PUBLIC_HOST_CACHE_LOCK:
            _PUBLIC_HOST_CACHE[host] = {"value": False, "expires_at": now + MEDIA_DNS_CACHE_TTL}
            _prune_public_host_cache_locked(now)
    return result


def _parse_media_url(raw_url, base_url=None):
    url = (raw_url or "").strip()
    if not url:
        return None, None
    if url.startswith("//"):
        url = "https:" + url
    if base_url:
        url = urljoin(base_url, url)

    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return None, None

    if parsed.scheme not in {"http", "https"}:
        return None, None
    if parsed.username or parsed.password:
        return None, None
    if not is_allowed_media_host(parsed.hostname):
        return None, None
    return url, parsed


def resolve_media_target(raw_url, base_url=None):
    url, parsed = _parse_media_url(raw_url, base_url=base_url)
    if not url:
        return None

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, ValueError):
        return None

    addresses = []
    for info in infos:
        if not info or not info[4]:
            return None
        address = info[4][0]
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError:
            return None
        if not parsed_address.is_global:
            return None
        normalized_address = str(parsed_address)
        if normalized_address not in addresses:
            addresses.append(normalized_address)
    if not addresses:
        return None

    default_port = 443 if parsed.scheme == "https" else 80
    host_header = hostname if port == default_port else f"{hostname}:{port}"
    return ResolvedMediaTarget(
        scheme=parsed.scheme,
        hostname=hostname,
        port=port,
        host_header=host_header,
        addresses=tuple(addresses[:_PINNED_ADDRESS_ATTEMPT_LIMIT]),
    )


def normalize_media_url_shape(raw_url, base_url=None):
    url, _ = _parse_media_url(raw_url, base_url=base_url)
    return url


def validate_media_url(raw_url, base_url=None):
    url, parsed = _parse_media_url(raw_url, base_url=base_url)
    if not url:
        return None

    if not is_public_hostname(parsed.hostname):
        return None
    return url


def fetch_media_response(src, headers, cookies):
    url = normalize_media_url_shape(src)
    if not url:
        return None, 400

    for _ in range(MEDIA_REDIRECT_LIMIT + 1):
        try:
            upstream = _http_get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=HTTP_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except UnsafeMediaAddress:
            return None, 400
        except requests.RequestException:
            return None, 502

        if not upstream.is_redirect:
            return upstream, None

        location = upstream.headers.get("Location")
        upstream.close()
        url = normalize_media_url_shape(location, base_url=url)
        if not url:
            return None, 400

    return None, 508


def normalize_range_header(value):
    range_header = (value or "").strip()
    if not range_header or len(range_header) > 100:
        return None
    if not re.fullmatch(r"bytes=\d*-\d*(?:,\d*-\d*)*", range_header):
        return None
    return range_header


def is_allowed_media_content_type(content_type):
    value = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if value == "application/octet-stream":
        return True
    return value.startswith(("image/", "video/", "audio/"))


def is_streaming_media_response(content_type, status_code, range_header=None):
    value = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    return bool(range_header) or status_code == 206 or value.startswith(("video/", "audio/"))


def should_stream_known_length_media(content_type, content_length):
    if content_length < MEDIA_STREAMING_MIN_BYTES:
        return False
    value = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    return value == "application/octet-stream" or value.startswith("image/")


def is_identity_content_encoding(content_encoding):
    value = (content_encoding or "").strip().lower()
    return not value or value == "identity"


def read_limited_media_body(upstream):
    total = 0
    chunks = []
    try:
        for chunk in upstream.iter_content(chunk_size=MEDIA_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > MEDIA_MAX_BYTES:
                return None, 413
            chunks.append(chunk)
        return b"".join(chunks), None
    except (requests.RequestException, OSError):
        return None, 502
    finally:
        upstream.close()


def stream_media_body(upstream, max_bytes=None):
    remaining = max(_safe_int(MEDIA_MAX_BYTES if max_bytes is None else max_bytes, 0), 0)
    try:
        for chunk in upstream.iter_content(chunk_size=MEDIA_CHUNK_BYTES):
            if not chunk:
                continue
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                yield chunk[:remaining]
                remaining = 0
                break
            yield chunk
            remaining -= len(chunk)
    finally:
        upstream.close()


def build_streaming_media_response(upstream, content_type, content_length=None):
    response = Response(
        stream_with_context(stream_media_body(upstream, max_bytes=content_length)),
        status=upstream.status_code,
        direct_passthrough=True,
    )
    response.headers["Content-Type"] = content_type
    if content_length is not None:
        response.headers["Content-Length"] = str(content_length)
    for header in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
        value = upstream.headers.get(header)
        if value:
            response.headers[header] = value
    response.headers.setdefault("Accept-Ranges", "bytes")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def read_limited_media_spool(upstream):
    total = 0
    spool = tempfile.SpooledTemporaryFile(max_size=_MEDIA_SPOOL_MEMORY_BYTES, mode="w+b")
    try:
        for chunk in upstream.iter_content(chunk_size=MEDIA_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > MEDIA_MAX_BYTES:
                spool.close()
                return None, 0, 413
            spool.write(chunk)
        spool.seek(0)
        return spool, total, None
    except (requests.RequestException, OSError):
        spool.close()
        return None, 0, 502
    finally:
        upstream.close()


def stream_spooled_media_body(spool):
    try:
        while True:
            chunk = spool.read(MEDIA_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        spool.close()


def build_spooled_media_response(spool, content_length, upstream, content_type):
    response = Response(
        stream_with_context(stream_spooled_media_body(spool)),
        status=upstream.status_code,
        direct_passthrough=True,
    )
    response.headers["Content-Type"] = content_type
    response.headers["Content-Length"] = str(content_length)
    for header in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
        value = upstream.headers.get(header)
        if value:
            response.headers[header] = value
    response.headers.setdefault("Accept-Ranges", "bytes")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def parse_media_content_length(headers):
    transfer_encoding = (headers.get("Transfer-Encoding") or "").strip().lower()
    if transfer_encoding and transfer_encoding != "identity":
        return None, None

    raw_value = headers.get("Content-Length")
    if raw_value in (None, ""):
        return None, None
    values = [part.strip() for part in str(raw_value).split(",")]
    if not values or any(not value.isdigit() for value in values):
        return None, 502
    if any(len(value) > 20 for value in values):
        return None, 413
    try:
        parsed_values = {int(value) for value in values}
    except (OverflowError, ValueError):
        return None, 502
    if len(parsed_values) != 1:
        return None, 502
    return parsed_values.pop(), None


def build_media_response(src, board, pid, kind=None, range_header=None):
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": PC_USER_AGENT,
        "Referer": build_pc_view_referer(board, pid, kind=kind),
    }
    normalized_range = normalize_range_header(range_header)
    if normalized_range:
        headers["Range"] = normalized_range
    cookies = {"__gat_mobile_search": "1", "list_count": "200"}
    upstream, error_status = fetch_media_response(src, headers, cookies)
    if error_status:
        return "", error_status

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    if not is_allowed_media_content_type(content_type):
        upstream.close()
        return "", 415

    content_encoding = upstream.headers.get("Content-Encoding")
    can_stream_decoded_body = is_identity_content_encoding(content_encoding)
    content_length, length_error = parse_media_content_length(upstream.headers)
    if length_error:
        upstream.close()
        return "", length_error
    if content_length is not None and content_length > MEDIA_MAX_BYTES:
        upstream.close()
        return "", 413
    if can_stream_decoded_body and is_streaming_media_response(content_type, upstream.status_code, normalized_range):
        if content_length is not None:
            return build_streaming_media_response(upstream, content_type, content_length=content_length)
        spool, verified_length, error_status = read_limited_media_spool(upstream)
        if error_status:
            return "", error_status
        return build_spooled_media_response(spool, verified_length, upstream, content_type)
    if not can_stream_decoded_body and upstream.status_code == 206:
        upstream.close()
        return "", 502

    if (
        content_length is not None
        and can_stream_decoded_body
        and should_stream_known_length_media(content_type, content_length)
    ):
        return build_streaming_media_response(upstream, content_type, content_length=content_length)

    body, error_status = read_limited_media_body(upstream)
    if error_status:
        return "", error_status

    response = Response(body or b"", status=upstream.status_code)
    response.headers["Content-Type"] = content_type
    response.content_length = len(body or b"")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def movie_player_candidates(movie_no, board, pid, kind=None):
    pc_referer = build_pc_view_referer(board, pid, kind=kind)
    mobile_referer = build_mobile_view_referer(board, pid)
    return [
        (
            f"https://gall.dcinside.com/board/movie/movie_view?no={movie_no}",
            {"User-Agent": PC_USER_AGENT, "Referer": pc_referer},
        ),
        (
            f"https://m.dcinside.com/movie/player?no={movie_no}&mobile=M",
            {"User-Agent": MOBILE_USER_AGENT, "Referer": mobile_referer},
        ),
    ]


def parse_movie_media(text):
    soup = BeautifulSoup(text or "", "lxml")
    video = soup.find("video")
    source = video.find("source") if video else soup.find("source")
    source_url = source.get("src") if source else None
    poster_url = video.get("poster") if video else None
    source_url = normalize_media_url_shape(source_url)
    poster_url = normalize_media_url_shape(poster_url)
    if not source_url:
        return None
    return {"source": source_url, "poster": poster_url}


def fetch_movie_media(movie_no, board, pid, kind=None):
    if not str(movie_no or "").isdigit():
        return None
    for url, headers in movie_player_candidates(movie_no, board, pid, kind=kind):
        try:
            response = _http_get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        if response.status_code >= 400 or not response.text:
            continue
        media = parse_movie_media(response.text)
        if media:
            return media
    return None


def movie_html(media, board, pid, kind=None):
    source_url = url_for("main.media", src=media["source"], board=board, pid=pid, kind=kind)
    poster_url = (
        url_for("main.media", src=media["poster"], board=board, pid=pid, kind=kind)
        if media.get("poster")
        else ""
    )
    poster_attr = f' poster="{escape(poster_url, quote=True)}"' if poster_url else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html, body {{
    margin: 0;
    width: 100%;
    height: 100%;
    background: #05070b;
}}
body {{
    display: flex;
    align-items: center;
    justify-content: center;
}}
video {{
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #05070b;
}}
</style>
</head>
<body>
<video controls playsinline controlslist="nodownload"{poster_attr}>
  <source src="{escape(source_url, quote=True)}" type="video/mp4">
  이 브라우저에서는 동영상을 재생할 수 없습니다.
</video>
</body>
</html>"""


def movie_error_html():
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html, body { margin: 0; min-height: 100%; background: #05070b; color: #111827; }
body { display: flex; align-items: center; justify-content: center; font: 14px/1.5 sans-serif; }
.message { box-sizing: border-box; width: 100%; padding: 18px; background: #fff; }
</style>
</head>
<body><div class="message">동영상을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.</div></body>
</html>"""


def build_movie_response(movie_no, board, pid, kind=None):
    media = fetch_movie_media(movie_no, board, pid, kind=kind)
    if not media:
        return Response(movie_error_html(), status=502, mimetype="text/html")
    response = Response(movie_html(media, board, pid, kind=kind), mimetype="text/html")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
