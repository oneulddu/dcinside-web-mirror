import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import requests
from flask import Response


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
MEDIA_MAX_BYTES = _env_int("MIRROR_MEDIA_MAX_BYTES", 25 * 1024 * 1024)
MEDIA_REDIRECT_LIMIT = _env_int("MIRROR_MEDIA_REDIRECT_LIMIT", 3)
MEDIA_ALLOWED_HOST_SUFFIXES = tuple(
    suffix.strip().lower().lstrip(".")
    for suffix in os.getenv("MIRROR_MEDIA_ALLOWED_HOST_SUFFIXES", "dcinside.com,dcinside.co.kr").split(",")
    if suffix.strip()
)


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


def is_allowed_media_host(hostname):
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in MEDIA_ALLOWED_HOST_SUFFIXES)


def is_public_hostname(hostname):
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


def validate_media_url(raw_url, base_url=None):
    url = (raw_url or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if base_url:
        url = urljoin(base_url, url)

    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        return None
    if not is_allowed_media_host(parsed.hostname):
        return None
    if not is_public_hostname(parsed.hostname):
        return None
    return url


def fetch_media_response(src, headers, cookies):
    url = validate_media_url(src)
    if not url:
        return None, 400

    for _ in range(MEDIA_REDIRECT_LIMIT + 1):
        try:
            upstream = requests.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=HTTP_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None, 502

        if not upstream.is_redirect:
            return upstream, None

        location = upstream.headers.get("Location")
        upstream.close()
        url = validate_media_url(location, base_url=url)
        if not url:
            return None, 400

    return None, 508


def is_allowed_media_content_type(content_type):
    value = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if value == "application/octet-stream":
        return True
    return value.startswith(("image/", "video/", "audio/"))


def read_limited_media_body(upstream):
    total = 0
    chunks = []
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MEDIA_MAX_BYTES:
                return None, 413
            chunks.append(chunk)
        return b"".join(chunks), None
    finally:
        upstream.close()


def build_media_response(src, board, pid, kind=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": build_pc_view_referer(board, pid, kind=kind),
    }
    cookies = {"__gat_mobile_search": "1", "list_count": "200"}
    upstream, error_status = fetch_media_response(src, headers, cookies)
    if error_status:
        return "", error_status

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    if not is_allowed_media_content_type(content_type):
        upstream.close()
        return "", 415

    raw_content_length = upstream.headers.get("Content-Length")
    content_length = _safe_int(raw_content_length, 0)
    if content_length and content_length > MEDIA_MAX_BYTES:
        upstream.close()
        return "", 413

    body, error_status = read_limited_media_body(upstream)
    if error_status:
        return "", error_status

    response = Response(body or b"", status=upstream.status_code)
    response.headers["Content-Type"] = content_type
    response.content_length = len(body or b"")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
