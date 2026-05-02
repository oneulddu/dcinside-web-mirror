import ipaddress
import os
import re
import socket
from html import escape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
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
MEDIA_MAX_BYTES = _env_int("MIRROR_MEDIA_MAX_BYTES", 25 * 1024 * 1024)
MEDIA_REDIRECT_LIMIT = _env_int("MIRROR_MEDIA_REDIRECT_LIMIT", 3)
MEDIA_ALLOWED_HOST_SUFFIXES = tuple(
    suffix.strip().lower().lstrip(".")
    for suffix in os.getenv("MIRROR_MEDIA_ALLOWED_HOST_SUFFIXES", "dcinside.com,dcinside.co.kr").split(",")
    if suffix.strip()
)
PC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 7.0; SM-G892A Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/67.0.3396.87 Mobile Safari/537.36"


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
    except (requests.RequestException, OSError):
        return None, 502
    finally:
        upstream.close()


def stream_media_body(upstream):
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    finally:
        upstream.close()


def build_streaming_media_response(upstream, content_type):
    response = Response(
        stream_with_context(stream_media_body(upstream)),
        status=upstream.status_code,
        direct_passthrough=True,
    )
    response.headers["Content-Type"] = content_type
    for header in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
        value = upstream.headers.get(header)
        if value:
            response.headers[header] = value
    response.headers.setdefault("Accept-Ranges", "bytes")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def build_media_response(src, board, pid, kind=None, range_header=None):
    headers = {
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

    if is_streaming_media_response(content_type, upstream.status_code, normalized_range):
        return build_streaming_media_response(upstream, content_type)

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
    soup = BeautifulSoup(text or "", "html.parser")
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
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
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
    min-height: 100%;
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
    max-width: 100%;
    max-height: 100vh;
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
