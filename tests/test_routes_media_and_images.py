import base64
import json
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from bs4 import BeautifulSoup
from flask import Response, jsonify

from app import create_app
from app import routes
from app.services import heung
from app.services import html_sanitizer
from app.services import media_proxy
from app.services import recent


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DummyUpstream:
    def __init__(self, chunks, headers=None, status_code=200):
        self.chunks = chunks
        self.headers = headers or {}
        self.status_code = status_code
        self.closed = False
        self.iterated = 0

    def iter_content(self, chunk_size=1):
        for chunk in self.chunks:
            self.iterated += 1
            yield chunk

    def close(self):
        self.closed = True


class ExplodingUpstream(DummyUpstream):
    def iter_content(self, chunk_size=1):
        self.iterated += 1
        raise OSError("upstream disconnected")


class DummyMovieResponse:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in {301, 302, 303, 307, 308} and bool(self.headers.get("Location"))

    def close(self):
        self.closed = True


def test_media_proxy_mobile_user_agent_uses_ios():
    assert "iPhone" in media_proxy.MOBILE_USER_AGENT


def test_html_and_json_responses_are_compressed():
    app = create_app()

    @app.get("/compression-html-test")
    def compression_html_test():
        return "<p>" + ("압축 테스트" * 200) + "</p>"

    @app.get("/compression-json-test")
    def compression_json_test():
        return jsonify({"text": "압축 테스트" * 200})

    client = app.test_client()

    html_response = client.get("/compression-html-test", headers={"Accept-Encoding": "gzip"})
    json_response = client.get("/compression-json-test", headers={"Accept-Encoding": "gzip"})

    assert html_response.headers["Content-Encoding"] == "gzip"
    assert json_response.headers["Content-Encoding"] == "gzip"


def test_media_and_movie_routes_are_not_compressed(monkeypatch):
    upstream = DummyUpstream(
        [b"1234567890"],
        headers={"Content-Type": "image/jpeg"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    monkeypatch.setattr(
        media_proxy.requests,
        "get",
        lambda *args, **kwargs: DummyMovieResponse("<html>" + ("movie" * 200) + "</html>"),
    )
    app = create_app()
    client = app.test_client()

    media_response = client.get(
        "/media?src=https://images.dcinside.com/test.jpg",
        headers={"Accept-Encoding": "gzip"},
    )
    movie_response = client.get(
        "/movie?no=6499430&board=idolism&pid=1193413",
        headers={"Accept-Encoding": "gzip"},
    )

    assert "Content-Encoding" not in media_response.headers
    assert "Content-Encoding" not in movie_response.headers


def test_favicon_route_serves_svg_icon():
    app = create_app()

    response = app.test_client().get("/favicon.ico")

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert b"<svg" in response.data


def test_read_limited_media_body_closes_after_success(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream([b"123", b"456"])

    body, error_status = media_proxy.read_limited_media_body(upstream)

    assert body == b"123456"
    assert error_status is None
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_read_limited_media_body_returns_413_and_closes_when_limit_exceeded(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream([b"123", b"456"])

    body, error_status = media_proxy.read_limited_media_body(upstream)

    assert body is None
    assert error_status == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_read_limited_media_body_returns_502_and_closes_on_stream_error():
    upstream = ExplodingUpstream([], headers={"Content-Type": "image/jpeg"})

    body, error_status = media_proxy.read_limited_media_body(upstream)

    assert body is None
    assert error_status == 502
    assert upstream.iterated == 1
    assert upstream.closed is True


def test_should_stream_known_length_media_respects_threshold(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_STREAMING_MIN_BYTES", 1024)

    assert media_proxy.should_stream_known_length_media("image/jpeg", 1023) is False
    assert media_proxy.should_stream_known_length_media("image/jpeg", 1024) is True
    assert media_proxy.should_stream_known_length_media("application/octet-stream", 1024) is True
    assert media_proxy.should_stream_known_length_media("text/html", 1024) is False


def test_identity_content_encoding_accepts_only_empty_or_identity():
    assert media_proxy.is_identity_content_encoding(None) is True
    assert media_proxy.is_identity_content_encoding("") is True
    assert media_proxy.is_identity_content_encoding("identity") is True
    assert media_proxy.is_identity_content_encoding("gzip") is False


def test_media_route_rejects_unknown_length_streams_when_limit_exceeded(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_buffers_unknown_length_streams_within_limit(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 200
    assert response.data == b"123456"
    assert response.content_length == 6
    assert upstream.closed is True


def test_media_route_buffers_upstream_and_sets_verified_length(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    with app.test_request_context("/media?src=https://images.dcinside.com/test.jpg"):
        response = routes.media()
        assert response.content_length == 6
        assert response.get_data() == b"123456"

    assert upstream.closed is True


def test_media_route_streams_large_known_length_images_without_buffering(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 20)
    monkeypatch.setattr(media_proxy, "MEDIA_STREAMING_MIN_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "application/octet-stream", "Content-Length": "6"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))

    def fail_buffering(*args, **kwargs):
        raise AssertionError("known-length large media should stream without full buffering")

    monkeypatch.setattr(media_proxy, "read_limited_media_body", fail_buffering)
    app = create_app()

    response = app.test_client().get("/media?src=https://dcimg7.dcinside.co.kr/viewimage.php?id=test")

    assert response.status_code == 200
    assert response.data == b"123456"
    assert response.headers["Content-Length"] == "6"
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert upstream.closed is True


def test_media_route_buffers_encoded_known_length_images(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 20)
    monkeypatch.setattr(media_proxy, "MEDIA_STREAMING_MIN_BYTES", 1)
    upstream = DummyUpstream(
        [b"decoded", b"-body"],
        headers={
            "Content-Type": "image/jpeg",
            "Content-Length": "6",
            "Content-Encoding": "gzip",
        },
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))

    def fail_streaming(*args, **kwargs):
        raise AssertionError("encoded media should not reuse upstream Content-Length while streaming")

    monkeypatch.setattr(media_proxy, "build_streaming_media_response", fail_streaming)
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 200
    assert response.data == b"decoded-body"
    assert response.content_length == len(b"decoded-body")
    assert "Content-Encoding" not in response.headers
    assert upstream.closed is True


def test_media_route_buffers_encoded_video_instead_of_streaming(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 20)
    upstream = DummyUpstream(
        [b"decoded", b"-video"],
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": "5",
            "Content-Encoding": "gzip",
        },
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))

    def fail_streaming(*args, **kwargs):
        raise AssertionError("encoded video should not reuse upstream Content-Length while streaming")

    monkeypatch.setattr(media_proxy, "build_streaming_media_response", fail_streaming)
    app = create_app()

    response = app.test_client().get("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")

    assert response.status_code == 200
    assert response.data == b"decoded-video"
    assert response.content_length == len(b"decoded-video")
    assert "Content-Encoding" not in response.headers
    assert upstream.closed is True


def test_media_route_rejects_encoded_partial_content(monkeypatch):
    upstream = DummyUpstream(
        [b"decoded", b"-partial"],
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": "7",
            "Content-Encoding": "gzip",
            "Content-Range": "bytes 0-6/100",
        },
        status_code=206,
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get(
        "/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4",
        headers={"Range": "bytes=0-6"},
    )

    assert response.status_code == 502
    assert upstream.iterated == 0
    assert upstream.closed is True


def test_media_route_streams_video_range_requests(monkeypatch):
    upstream = DummyUpstream(
        [b"abc", b"def"],
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": "6",
            "Content-Range": "bytes 0-5/100",
            "Accept-Ranges": "bytes",
        },
        status_code=206,
    )
    captured = {}

    def fake_fetch(src, headers, cookies):
        captured["headers"] = headers
        return upstream, None

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    app = create_app()

    response = app.test_client().get(
        "/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4",
        headers={"Range": "bytes=0-5"},
    )

    assert response.status_code == 206
    assert response.data == b"abcdef"
    assert response.headers["Content-Type"] == "video/mp4"
    assert response.headers["Content-Range"] == "bytes 0-5/100"
    assert captured["headers"]["Accept-Encoding"] == "identity"
    assert captured["headers"]["Range"] == "bytes=0-5"
    assert upstream.closed is True


def test_media_route_rejects_mismatched_known_length_when_stream_exceeds_limit(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg", "Content-Length": "5"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_rejects_known_length_video_over_limit_before_streaming(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "video/mp4", "Content-Length": "6"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")
    body = response.get_data()

    assert response.status_code == 413
    assert body == b""
    assert upstream.iterated == 0
    assert upstream.closed is True


def test_media_route_rejects_unknown_length_video_over_limit(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "video/mp4"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")
    body = response.get_data()

    assert response.status_code == 413
    assert body == b""
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_caps_stream_to_verified_content_length(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "video/mp4", "Content-Length": "5"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")

    assert response.status_code == 200
    assert response.data == b"12345"
    assert response.content_length == 5
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_rejects_conflicting_content_lengths(monkeypatch):
    upstream = DummyUpstream(
        [b"12345"],
        headers={"Content-Type": "video/mp4", "Content-Length": "5, 6"},
    )
    monkeypatch.setattr(media_proxy, "fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")

    assert response.status_code == 502
    assert response.get_data() == b""
    assert upstream.iterated == 0
    assert upstream.closed is True


def test_media_head_uses_upstream_head_without_reading_unknown_length_video(monkeypatch):
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "video/mp4"},
    )
    captured = {}

    def fake_fetch(src, headers, cookies, method="GET"):
        captured["method"] = method
        return upstream, None

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    app = create_app()

    response = app.test_client().head("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")

    assert response.status_code == 200
    assert response.data == b""
    assert "Content-Length" not in response.headers
    assert captured["method"] == "HEAD"
    assert upstream.iterated == 0
    assert upstream.closed is True


def test_media_head_preserves_known_length_without_starting_stream(monkeypatch):
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "video/mp4", "Content-Length": "6"},
    )

    def fake_fetch(src, headers, cookies, method="GET"):
        assert method == "HEAD"
        return upstream, None

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    app = create_app()

    response = app.test_client().head("/media?src=https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4")

    assert response.status_code == 200
    assert response.data == b""
    assert response.headers["Content-Length"] == "6"
    assert upstream.iterated == 0
    assert upstream.closed is True


def test_media_fetch_uses_pinned_head_request(monkeypatch):
    upstream = DummyUpstream([], headers={"Content-Type": "image/jpeg"})
    upstream.is_redirect = False
    captured = {}

    def fake_head(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return upstream

    def fail_get(*args, **kwargs):
        raise AssertionError("HEAD media fetch must not issue an upstream GET")

    monkeypatch.setattr(media_proxy, "_http_head", fake_head)
    monkeypatch.setattr(media_proxy, "_http_get", fail_get)

    response, error_status = media_proxy.fetch_media_response(
        "https://images.dcinside.com/test.jpg",
        headers={},
        cookies={},
        method="HEAD",
    )

    assert response is upstream
    assert error_status is None
    assert captured["url"] == "https://images.dcinside.com/test.jpg"
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["allow_redirects"] is False
    upstream.close()


def test_media_fetch_rejects_dns_rebinding_before_local_connection(monkeypatch):
    hit = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hit.set()
            body = b"private"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_getaddrinfo = socket.getaddrinfo

    def rebinding_getaddrinfo(host, port, *args, **kwargs):
        if str(host).lower().rstrip(".") == "images.dcinside.com":
            if port is None:
                return [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("93.184.216.34", 0),
                    )
                ]
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", port),
                )
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    if hasattr(media_proxy._MEDIA_SESSION_LOCAL, "session"):
        media_proxy._MEDIA_SESSION_LOCAL.session.close()
        delattr(media_proxy._MEDIA_SESSION_LOCAL, "session")
    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    monkeypatch.setenv("NO_PROXY", "*")

    try:
        upstream, error_status = media_proxy.fetch_media_response(
            f"http://images.dcinside.com:{server.server_port}/secret",
            headers={},
            cookies={},
        )
        if upstream is not None:
            upstream.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if hasattr(media_proxy._MEDIA_SESSION_LOCAL, "session"):
            media_proxy._MEDIA_SESSION_LOCAL.session.close()
            delattr(media_proxy._MEDIA_SESSION_LOCAL, "session")

    assert error_status == 400
    assert upstream is None
    assert hit.is_set() is False


def test_pinned_media_adapter_keeps_original_https_identity():
    target = media_proxy.ResolvedMediaTarget(
        scheme="https",
        hostname="images.dcinside.com",
        port=443,
        host_header="images.dcinside.com",
        addresses=("93.184.216.34",),
    )
    adapter = media_proxy.PinnedMediaAdapter(target)
    captured = {}

    class PoolManagerSpy:
        def connection_from_host(self, **kwargs):
            captured.update(kwargs)
            return object()

    adapter.poolmanager = PoolManagerSpy()
    adapter._active_address = target.addresses[0]
    prepared = media_proxy.requests.Request("GET", "https://images.dcinside.com/image.jpg").prepare()

    adapter.get_connection_with_tls_context(prepared, verify=True)

    assert captured["host"] == "93.184.216.34"
    assert captured["pool_kwargs"]["server_hostname"] == "images.dcinside.com"
    assert captured["pool_kwargs"]["assert_hostname"] == "images.dcinside.com"


def test_pinned_media_adapter_tries_only_validated_ips_and_forces_host(monkeypatch):
    target = media_proxy.ResolvedMediaTarget(
        scheme="http",
        hostname="images.dcinside.com",
        port=8080,
        host_header="images.dcinside.com:8080",
        addresses=("93.184.216.34", "93.184.216.35"),
    )
    adapter = media_proxy.PinnedMediaAdapter(target)
    calls = []

    def fake_send(self, request, **kwargs):
        calls.append((self._active_address, request.headers.get("Host"), kwargs.get("proxies")))
        if self._active_address == target.addresses[0]:
            raise media_proxy.requests.exceptions.ConnectionError("first address failed")
        return "ok"

    monkeypatch.setattr(media_proxy.HTTPAdapter, "send", fake_send)
    prepared = media_proxy.requests.Request("GET", "http://images.dcinside.com:8080/image.jpg").prepare()

    result = adapter.send(prepared, proxies={"http": "http://127.0.0.1:9999"})

    assert result == "ok"
    assert calls == [
        ("93.184.216.34", "images.dcinside.com:8080", {}),
        ("93.184.216.35", "images.dcinside.com:8080", {}),
    ]


def test_resolve_media_target_rejects_mixed_public_and_private_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )

    assert media_proxy.resolve_media_target("https://images.dcinside.com/image.jpg") is None


def test_resolve_media_target_keeps_ipv4_fallback_when_ipv6_is_listed_first(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1001", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        ],
    )

    target = media_proxy.resolve_media_target("https://images.dcinside.com/image.jpg")

    assert target is not None
    assert target.addresses == ("2606:4700:4700::1111", "93.184.216.34")


def test_media_route_rejects_non_dcinside_source_before_fetch(monkeypatch):
    def fail_fetch(*args, **kwargs):
        raise AssertionError("invalid media source must be rejected before fetch")

    monkeypatch.setattr(media_proxy, "fetch_media_response", fail_fetch)
    app = create_app()

    response = app.test_client().get("/media?src=https://example.com/test.jpg")

    assert response.status_code == 400


def test_public_hostname_cache_does_not_cache_public_results(monkeypatch):
    media_proxy._PUBLIC_HOST_CACHE.clear()
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_TTL", 30)
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_MAX_ITEMS", 10)
    calls = []

    def fake_resolve(hostname):
        calls.append(hostname)
        return True

    monkeypatch.setattr(media_proxy, "_resolve_public_hostname", fake_resolve)

    assert media_proxy.is_public_hostname("IMAGES.DCINSIDE.COM.") is True
    assert media_proxy.is_public_hostname("images.dcinside.com") is True
    assert calls == ["images.dcinside.com", "images.dcinside.com"]
    assert media_proxy._PUBLIC_HOST_CACHE == {}


def test_public_hostname_cache_reuses_short_lived_non_public_result(monkeypatch):
    media_proxy._PUBLIC_HOST_CACHE.clear()
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_TTL", 30)
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_MAX_ITEMS", 10)
    calls = []

    def fake_resolve(hostname):
        calls.append(hostname)
        return False

    monkeypatch.setattr(media_proxy, "_resolve_public_hostname", fake_resolve)

    assert media_proxy.is_public_hostname("IMAGES.DCINSIDE.COM.") is False
    assert media_proxy.is_public_hostname("images.dcinside.com") is False
    assert calls == ["images.dcinside.com"]
    media_proxy._PUBLIC_HOST_CACHE.clear()


def test_public_hostname_cache_expires_non_public_result(monkeypatch):
    media_proxy._PUBLIC_HOST_CACHE.clear()
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_TTL", 5)
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_MAX_ITEMS", 10)
    calls = []
    now = [1000.0]

    def fake_resolve(hostname):
        calls.append(hostname)
        return False

    monkeypatch.setattr(media_proxy, "_resolve_public_hostname", fake_resolve)
    monkeypatch.setattr(media_proxy.time, "time", lambda: now[0])

    assert media_proxy.is_public_hostname("images.dcinside.com") is False
    now[0] += 6
    assert media_proxy.is_public_hostname("images.dcinside.com") is False

    assert calls == ["images.dcinside.com", "images.dcinside.com"]
    media_proxy._PUBLIC_HOST_CACHE.clear()


def test_public_hostname_cache_prunes_to_max_items(monkeypatch):
    media_proxy._PUBLIC_HOST_CACHE.clear()
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_TTL", 30)
    monkeypatch.setattr(media_proxy, "MEDIA_DNS_CACHE_MAX_ITEMS", 2)

    def fake_resolve(hostname):
        return False

    monkeypatch.setattr(media_proxy, "_resolve_public_hostname", fake_resolve)

    assert media_proxy.is_public_hostname("a.dcinside.com") is False
    assert media_proxy.is_public_hostname("b.dcinside.com") is False
    assert media_proxy.is_public_hostname("c.dcinside.com") is False

    assert len(media_proxy._PUBLIC_HOST_CACHE) == 2
    assert "c.dcinside.com" in media_proxy._PUBLIC_HOST_CACHE
    media_proxy._PUBLIC_HOST_CACHE.clear()


def test_media_http_session_clears_upstream_cookie_state(monkeypatch):
    if hasattr(media_proxy._MEDIA_SESSION_LOCAL, "session"):
        delattr(media_proxy._MEDIA_SESSION_LOCAL, "session")

    class FakeCookies:
        def __init__(self):
            self.values = {"stale": "1"}
            self.clear_count = 0

        def clear(self):
            self.clear_count += 1
            self.values.clear()

    class FakeSession:
        def __init__(self):
            self.cookies = FakeCookies()
            self.sent_cookie_state = None

        def mount(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            self.sent_cookie_state = dict(self.cookies.values)
            self.cookies.values["upstream"] = "set-cookie"
            return "ok"

    fake_session = FakeSession()
    monkeypatch.setattr(media_proxy.requests, "Session", lambda: fake_session)

    assert media_proxy._http_get("https://images.dcinside.com/test.jpg") == "ok"
    assert fake_session.sent_cookie_state == {}
    assert fake_session.cookies.values == {}
    assert fake_session.cookies.clear_count == 2

    if hasattr(media_proxy._MEDIA_SESSION_LOCAL, "session"):
        delattr(media_proxy._MEDIA_SESSION_LOCAL, "session")


def test_rewrite_content_images_removes_unmapped_images_without_shifting_urls():
    app = create_app()
    soup = BeautifulSoup(
        """
        <article>
          <img src="https://img.iacstatic.co.kr/ad.jpg">
          <img data-original="https://images.dcinside.com/post-a.jpg" src="/placeholder-a.jpg">
          <img src="https://images.dcinside.com/post-b.jpg">
        </article>
        """,
        "html.parser",
    )

    with app.test_request_context("/read?board=test&pid=123&kind=minor"):
        html_sanitizer.rewrite_content_images(
            soup,
            [
                "https://images.dcinside.com/post-a.jpg",
                "https://images.dcinside.com/post-b.jpg",
            ],
            "test",
            123,
            "minor",
        )

    images = soup.find_all("img")
    assert len(images) == 2
    first_query = parse_qs(urlparse(images[0]["src"]).query)
    second_query = parse_qs(urlparse(images[1]["src"]).query)
    assert first_query["src"] == ["https://images.dcinside.com/post-a.jpg"]
    assert second_query["src"] == ["https://images.dcinside.com/post-b.jpg"]
    assert images[0]["loading"] == "eager"
    assert images[0]["fetchpriority"] == "high"
    assert images[1]["loading"] == "lazy"
    assert "fetchpriority" not in images[1].attrs
    assert "data-original" not in images[0].attrs


def test_rewrite_content_images_rewrites_dc_movie_iframes_to_local_player():
    app = create_app()
    soup = BeautifulSoup(
        """
        <article>
          <iframe src="https://m.dcinside.com/movie/player?no=6499430&amp;mobile=M"></iframe>
        </article>
        """,
        "html.parser",
    )

    with app.test_request_context("/read?board=idolism&pid=1193413&kind=minor"):
        html_sanitizer.rewrite_content_images(soup, [], "idolism", 1193413, "minor")

    iframe = soup.find("iframe")
    parsed = urlparse(iframe["src"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/movie"
    assert query["no"] == ["6499430"]
    assert query["board"] == ["idolism"]
    assert query["pid"] == ["1193413"]
    assert query["kind"] == ["minor"]


def test_rewrite_content_images_rewrites_video_sources_to_media_proxy():
    app = create_app()
    soup = BeautifulSoup(
        """
        <article>
          <video controls autoplay loop muted playsinline data-src="https://dcimg7.dcinside.co.kr/ignored.mp4">
            <source src="https://dcimg7.dcinside.co.kr/viewimage.php?id=test&amp;no=mp4" type="video/mp4">
          </video>
        </article>
        """,
        "html.parser",
    )

    with app.test_request_context("/read?board=idolism&pid=1201641&kind=minor"):
        html_sanitizer.rewrite_content_images(
            soup,
            ["https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=mp4"],
            "idolism",
            1201641,
            "minor",
        )

    source = soup.find("source")
    query = parse_qs(urlparse(source["src"]).query)
    assert query["src"] == ["https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=mp4"]
    assert query["board"] == ["idolism"]
    assert query["pid"] == ["1201641"]
    assert query["kind"] == ["minor"]
    assert "data-src" not in soup.find("video").attrs


def test_rewrite_content_images_rewrites_nested_source_data_src():
    app = create_app()
    soup = BeautifulSoup(
        """
        <article>
          <video controls>
            <source data-src="https://dcimg7.dcinside.co.kr/lazy-source.mp4" type="video/mp4">
          </video>
        </article>
        """,
        "html.parser",
    )

    with app.test_request_context("/read?board=idolism&pid=1201641&kind=minor"):
        html_sanitizer.rewrite_content_images(
            soup,
            ["https://dcimg7.dcinside.co.kr/lazy-source.mp4"],
            "idolism",
            1201641,
            "minor",
        )

    source = soup.find("source")
    query = parse_qs(urlparse(source["src"]).query)
    assert query["src"] == ["https://dcimg7.dcinside.co.kr/lazy-source.mp4"]


def test_rewrite_content_images_rewrites_lazy_video_src_and_poster():
    app = create_app()
    soup = BeautifulSoup(
        """
        <article>
          <video controls data-src="https://dcimg7.dcinside.co.kr/lazy.mp4"
                 poster="https://dcimg7.dcinside.co.kr/poster.jpg"></video>
        </article>
        """,
        "html.parser",
    )

    with app.test_request_context("/read?board=idolism&pid=1201641&kind=minor"):
        html_sanitizer.rewrite_content_images(
            soup,
            [
                "https://dcimg7.dcinside.co.kr/lazy.mp4",
                "https://dcimg7.dcinside.co.kr/poster.jpg",
            ],
            "idolism",
            1201641,
            "minor",
        )

    video = soup.find("video")
    src_query = parse_qs(urlparse(video["src"]).query)
    poster_query = parse_qs(urlparse(video["poster"]).query)
    assert src_query["src"] == ["https://dcimg7.dcinside.co.kr/lazy.mp4"]
    assert poster_query["src"] == ["https://dcimg7.dcinside.co.kr/poster.jpg"]
    assert "data-src" not in video.attrs


def test_sanitize_html_fragment_removes_unsafe_tags_and_attributes():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <div onclick="alert(1)">
          <script>alert(1)</script>
          <a href="javascript:alert(1)" target="_blank">bad</a>
          <a href="https://example.com/path">good</a>
          <img src="https://images.dcinside.com/raw.jpg">
          <img src="/media?src=https%3A%2F%2Fimages.dcinside.com%2Fsafe.jpg" fetchpriority="high" onerror="alert(1)">
          <img src="/media?src=https%3A%2F%2Fimages.dcinside.com%2Fbad-priority.jpg" fetchpriority="fast">
        </div>
        """
    )
    soup = BeautifulSoup(cleaned, "html.parser")

    assert soup.find("script") is None
    assert soup.div is not None
    assert "onclick" not in soup.div.attrs
    anchors = soup.find_all("a")
    assert "href" not in anchors[0].attrs
    assert anchors[1]["href"] == "https://example.com/path"
    assert anchors[1]["rel"] == ["noopener", "noreferrer"]
    images = soup.find_all("img")
    assert len(images) == 2
    assert images[0]["src"].startswith("/media?")
    assert images[0]["fetchpriority"] == "high"
    assert "onerror" not in images[0].attrs
    assert "fetchpriority" not in images[1].attrs


def test_sanitize_html_fragment_keeps_rewritten_video_source():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <video controls autoplay loop muted playsinline data-src="https://dcimg7.dcinside.co.kr/raw.mp4">
          <source src="/media?src=https%3A%2F%2Fdcimg7.dcinside.co.kr%2Fsafe.mp4" type="video/mp4">
          <source src="https://dcimg7.dcinside.co.kr/raw.mp4" type="video/mp4">
        </video>
        """
    )
    soup = BeautifulSoup(cleaned, "html.parser")

    assert soup.video is not None
    assert soup.video.has_attr("controls")
    assert "data-src" not in soup.video.attrs
    sources = soup.find_all("source")
    assert len(sources) == 1
    assert sources[0]["src"].startswith("/media?")


def test_sanitize_html_fragment_keeps_rewritten_video_src_and_poster():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <video controls src="/media?src=https%3A%2F%2Fdcimg7.dcinside.co.kr%2Fsafe.mp4"
               poster="/media?src=https%3A%2F%2Fdcimg7.dcinside.co.kr%2Fposter.jpg"></video>
        """
    )
    soup = BeautifulSoup(cleaned, "html.parser")

    assert soup.video is not None
    assert soup.video["src"].startswith("/media?")
    assert soup.video["poster"].startswith("/media?")


def test_sanitize_html_fragment_rejects_scheme_relative_iframe_src():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <div>
          <iframe src="//evil.com/poll"></iframe>
          <iframe src="/poll"></iframe>
          <iframe src="https://m.dcinside.com/poll"></iframe>
        </div>
        """
    )
    soup = BeautifulSoup(cleaned, "html.parser")
    iframe_sources = [iframe.get("src") for iframe in soup.find_all("iframe")]

    assert iframe_sources == ["/poll", "https://m.dcinside.com/poll"]


def test_sanitize_html_fragment_keeps_local_movie_iframe():
    cleaned = html_sanitizer.sanitize_html_fragment(
        '<iframe src="/movie?no=6499430&amp;board=idolism&amp;pid=1193413"></iframe>'
    )
    soup = BeautifulSoup(cleaned, "html.parser")

    assert soup.iframe["src"] == "/movie?no=6499430&board=idolism&pid=1193413"


def test_sanitize_html_fragment_keeps_dc_movie_and_youtube_iframes():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <div>
          <iframe src="https://gall.dcinside.com/board/movie/movie_view?no=6499427" allowfullscreen></iframe>
          <iframe src="https://m.dcinside.com/movie/player?no=6499430&amp;mobile=M"></iframe>
          <iframe src="//www.youtube.com/embed/abc123" allow="autoplay; encrypted-media"></iframe>
          <iframe src="https://www.youtube.com/embed/../watch?v=abc123"></iframe>
          <iframe src="https://www.youtube.com/watch?v=abc123"></iframe>
          <iframe src="https://gall.dcinside.com/board/movie/movie_view?no=bad"></iframe>
        </div>
        """
    )
    soup = BeautifulSoup(cleaned, "html.parser")
    iframe_sources = [iframe.get("src") for iframe in soup.find_all("iframe")]

    assert iframe_sources == [
        "https://gall.dcinside.com/board/movie/movie_view?no=6499427",
        "https://gall.dcinside.com/board/movie/movie_view?no=6499430",
        "https://www.youtube.com/embed/abc123",
    ]
    assert soup.find("iframe", src="https://www.youtube.com/embed/abc123").get("allow") == "autoplay; encrypted-media"


def test_prepare_read_html_rewrites_sanitizes_and_highlights_with_one_parse(monkeypatch):
    parser_calls = []
    real_beautiful_soup = html_sanitizer.BeautifulSoup

    def counting_beautiful_soup(*args, **kwargs):
        parser_calls.append(args[1] if len(args) > 1 else kwargs.get("features"))
        return real_beautiful_soup(*args, **kwargs)

    monkeypatch.setattr(html_sanitizer, "BeautifulSoup", counting_beautiful_soup)
    app = create_app()

    with app.test_request_context("/read?board=test&pid=123&kind=minor"):
        rendered = html_sanitizer.prepare_read_html(
            """
            <div onclick="alert(1)">
              <script>alert(1)</script>
              <p>hello BODY</p>
              <code>BODY</code>
              <img data-original="https://images.dcinside.com/body.jpg" src="/placeholder.jpg" onerror="alert(1)">
              <img src="https://images.dcinside.com/unmapped.jpg">
            </div>
            """,
            ["https://images.dcinside.com/body.jpg"],
            "test",
            123,
            "minor",
            search_keyword="body",
        )

    soup = BeautifulSoup(rendered, "html.parser")
    media_query = parse_qs(urlparse(soup.find("img")["src"]).query)

    assert parser_calls == [html_sanitizer.HTML_PARSER]
    assert soup.find("script") is None
    assert "onclick" not in soup.div.attrs
    assert len(soup.find_all("img")) == 1
    assert "onerror" not in soup.find("img").attrs
    assert media_query["src"] == ["https://images.dcinside.com/body.jpg"]
    assert soup.p.find("mark", class_="search-highlight").text == "BODY"
    assert soup.code.find("mark") is None


def test_prepare_read_html_rewrites_dcinside_links_to_internal_routes():
    app = create_app()

    with app.test_request_context("/read?board=test&pid=123&kind=minor"):
        rendered = html_sanitizer.prepare_read_html(
            """
            <div>
              <a href="https://m.dcinside.com/board/test/456?recommend=1&amp;headid=10#comment_box" target="_blank">mobile read</a>
              <a href="https://m.dcinside.com/mini/minitest?page=3&amp;s_type=subject_m&amp;s_keyword=hello">mobile list</a>
              <a href="https://gall.dcinside.com/mgallery/board/view/?id=minor_test&amp;no=789&amp;page=4&amp;search_head=20">pc read</a>
              <a href="/board/lists/?id=normal_test&amp;page=2&amp;exception_mode=recommend">pc list</a>
              <a href="https://gallog.dcinside.com/writer">gallog</a>
            </div>
            """,
            [],
            "test",
            123,
            "minor",
        )

    soup = BeautifulSoup(rendered, "html.parser")
    links = {link.get_text(strip=True): link for link in soup.find_all("a")}
    mobile_read = links["mobile read"]
    mobile_list = links["mobile list"]
    pc_read = links["pc read"]
    pc_list = links["pc list"]

    assert mobile_read["href"] == "/read?board=test&pid=456&recommend=1&headid=10#comment_box"
    assert "target" not in mobile_read.attrs

    mobile_list_query = parse_qs(urlparse(mobile_list["href"]).query)
    assert urlparse(mobile_list["href"]).path == "/board"
    assert mobile_list_query["board"] == ["minitest"]
    assert mobile_list_query["kind"] == ["mini"]
    assert mobile_list_query["page"] == ["3"]
    assert mobile_list_query["s_type"] == ["subject_m"]
    assert mobile_list_query["serval"] == ["hello"]

    pc_read_query = parse_qs(urlparse(pc_read["href"]).query)
    assert urlparse(pc_read["href"]).path == "/read"
    assert pc_read_query["board"] == ["minor_test"]
    assert pc_read_query["pid"] == ["789"]
    assert pc_read_query["kind"] == ["minor"]
    assert pc_read_query["source_page"] == ["4"]
    assert pc_read_query["headid"] == ["20"]

    pc_list_query = parse_qs(urlparse(pc_list["href"]).query)
    assert urlparse(pc_list["href"]).path == "/board"
    assert pc_list_query["board"] == ["normal_test"]
    assert pc_list_query["recommend"] == ["1"]
    assert pc_list_query["page"] == ["2"]

    assert links["gallog"]["href"] == "https://gallog.dcinside.com/writer"


def test_movie_route_renders_same_origin_video_player(monkeypatch):
    movie_html = """
    <html><body>
      <video poster="https://dcm6.dcinside.co.kr/viewmovie.php?type=jpg&amp;code=poster">
        <source src="https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4&amp;code=movie" type="video/mp4">
      </video>
    </body></html>
    """
    requests_seen = []

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        requests_seen.append((url, headers, allow_redirects))
        return DummyMovieResponse(movie_html)

    monkeypatch.setattr(media_proxy.requests, "get", fake_get)
    app = create_app()

    response = app.test_client().get("/movie?no=6499430&board=idolism&pid=1193413&kind=minor")

    assert response.status_code == 200
    soup = BeautifulSoup(response.data.decode("utf-8"), "html.parser")
    source_query = parse_qs(urlparse(soup.find("source")["src"]).query)
    poster_query = parse_qs(urlparse(soup.find("video")["poster"]).query)
    assert source_query["src"] == ["https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4&code=movie"]
    assert poster_query["src"] == ["https://dcm6.dcinside.co.kr/viewmovie.php?type=jpg&code=poster"]
    assert requests_seen[0][0] == "https://gall.dcinside.com/board/movie/movie_view?no=6499430"
    assert requests_seen[0][1]["Referer"] == "https://gall.dcinside.com/mgallery/board/view/?id=idolism&no=1193413"
    assert requests_seen[0][2] is False


def test_movie_player_rejects_unsafe_redirect_without_following(monkeypatch):
    redirect = DummyMovieResponse(
        "",
        status_code=302,
        headers={"Location": "http://127.0.0.1:9999/private"},
    )
    calls = []

    monkeypatch.setattr(
        media_proxy,
        "movie_player_candidates",
        lambda *args, **kwargs: [("https://gall.dcinside.com/player", {})],
    )

    def fake_http_get(url, **kwargs):
        calls.append((url, kwargs))
        return redirect

    monkeypatch.setattr(media_proxy, "_http_get", fake_http_get)

    media = media_proxy.fetch_movie_media("123", "test", 456)

    assert media is None
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert redirect.closed is True


def test_movie_player_revalidates_each_safe_redirect_hop(monkeypatch):
    redirect = DummyMovieResponse(
        "",
        status_code=302,
        headers={"Location": "/board/movie/final"},
    )
    final = DummyMovieResponse(
        '<video><source src="https://dcm6.dcinside.co.kr/movie.mp4"></video>'
    )
    responses = [redirect, final]
    calls = []

    monkeypatch.setattr(
        media_proxy,
        "movie_player_candidates",
        lambda *args, **kwargs: [("https://gall.dcinside.com/player", {})],
    )

    def fake_http_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(media_proxy, "_http_get", fake_http_get)

    media = media_proxy.fetch_movie_media("123", "test", 456)

    assert media == {"source": "https://dcm6.dcinside.co.kr/movie.mp4", "poster": None}
    assert [call[0] for call in calls] == [
        "https://gall.dcinside.com/player",
        "https://gall.dcinside.com/board/movie/final",
    ]
    assert all(call[1]["allow_redirects"] is False for call in calls)
    assert redirect.closed is True
    assert final.closed is True


def test_board_read_links_preserve_source_page_and_recommend_mode(monkeypatch):
    seen_scan_limits = []

    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        seen_scan_limits.append(kwargs.get("max_scan_pages"))
        return [
            {
                "id": "123",
                "title": "title",
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            }
        ], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()

    normal_response = app.test_client().get("/board?board=test&recommend=0&page=3")
    normal_soup = BeautifulSoup(normal_response.data, "html.parser")
    normal_href = normal_soup.select_one("a.feed-item")["href"]
    normal_query = parse_qs(urlparse(normal_href).query)

    recommend_response = app.test_client().get("/board?board=test&recommend=1&page=3")
    recommend_soup = BeautifulSoup(recommend_response.data, "html.parser")
    recommend_href = recommend_soup.select_one("a.feed-item")["href"]
    recommend_query = parse_qs(urlparse(recommend_href).query)

    assert normal_query["source_page"] == ["3"]
    assert recommend_query["source_page"] == ["3"]
    assert recommend_query["recommend"] == ["1"]
    assert seen_scan_limits == [1, 1]


def test_board_renders_current_assets_and_links(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        assert page == 3
        assert board == "test"
        assert recommend == 1
        assert kind == "minor"
        assert kwargs["head_id"] == "10"
        return [
            {
                "id": "123",
                "title": "v2 title",
                "comment_count": 2,
                "subject": "[잡갤]",
                "author": "익명",
                "author_code": "1.2",
                "author_role": "manager",
                "time": "-",
                "time_display": "-",
                "needs_time_hydrate": False,
                "voteup_count": 4,
                "has_image": True,
                "isimage": True,
                "has_video": False,
                "isvideo": False,
                "isrecommend": True,
            }
        ], [{"head_id": "10", "label": "말머리", "active": True}]

    monkeypatch.setattr(routes, "_load_board_payload", fake_board_payload)
    app = create_app()

    response = app.test_client().get(
        "/board?board=test&recommend=1&page=3&kind=minor&headid=10&gallery_name=%ED%85%8C%EC%8A%A4%ED%8A%B8%20%EA%B0%A4%EB%9F%AC%EB%A6%AC"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    read_link = soup.select_one("a.feed-item")
    read_query = parse_qs(urlparse(read_link["href"]).query)

    assert response.status_code == 200
    assert soup.select_one("link[href*='/static/css/main.css']") is not None
    assert soup.select_one("script[src*='/static/javascript/read_state.js']") is not None
    assert soup.select_one(".board-head h1").get_text(strip=True) == "테스트 갤러리 게시판"
    assert urlparse(read_link["href"]).path == "/read"
    assert read_query["source_page"] == ["3"]
    assert read_query["gallery_name"] == ["테스트 갤러리"]
    assert soup.select_one(".board-category-tab.active").get_text(strip=True) == "말머리"
    assert soup.select_one(".post-subject").get_text(strip=True) == "[잡갤]"
    assert "[[잡갤]]" not in response.get_data(as_text=True)
    assert soup.select_one(".feed-recommend-icon.is-hot") is not None
    assert soup.select_one(".author-text.author-role-manager") is not None


def test_manager_author_color_uses_role_token():
    style = (PROJECT_ROOT / "app/static/css/main.css").read_text()

    assert "--role-manager: #FF6B00;" in style
    assert "--role-submanager: #3182F6;" in style
    assert ".author-text.author-role-manager {\n    color: var(--role-manager);" in style
    assert ".author-text.author-role-submanager {\n    color: var(--role-submanager);" in style
    assert ".author-text.author-role-manager,\n.author-text.author-role-submanager {\n    color: var(--accent);" not in style


def test_index_board_links_include_gallery_name(monkeypatch):
    def fake_heung_galleries():
        return [
            {
                "rank": 1,
                "name": "특이점이 온다",
                "board_id": "thesingularity",
                "board_kind": "minor",
            }
        ], 1

    monkeypatch.setattr(routes, "get_heung_galleries", fake_heung_galleries)
    app = create_app()

    response = app.test_client().get("/")
    soup = BeautifulSoup(response.data, "html.parser")
    query = parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)

    assert response.status_code == 200
    assert query["board"] == ["thesingularity"]
    assert query["kind"] == ["minor"]
    assert query["gallery_name"] == ["특이점이 온다"]


def test_board_visit_stores_gallery_name_for_recent(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", fake_board_payload)
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 1))
    monkeypatch.setattr(routes, "search_galleries", lambda query: [])
    app = create_app()
    client = app.test_client()

    board_response = client.get(
        "/board?board=thesingularity&kind=minor&gallery_name=%ED%8A%B9%EC%9D%B4%EC%A0%90%EC%9D%B4%20%EC%98%A8%EB%8B%A4"
    )
    recent_response = client.get("/recent")
    soup = BeautifulSoup(recent_response.data, "html.parser")
    row = soup.select_one("a.feed-item")
    query = parse_qs(urlparse(row["href"]).query)

    assert board_response.status_code == 200
    assert recent_response.status_code == 200
    assert row.select_one(".feed-title").get_text(strip=True) == "특이점이 온다"
    assert "thesingularity" in row.select_one(".feed-meta-left").get_text(" ", strip=True)
    assert "바로가기" not in row.get_text(" ", strip=True)
    assert soup.select_one("form.recent-remove-form button[aria-label='특이점이 온다 삭제']") is not None
    assert query["gallery_name"] == ["특이점이 온다"]


def test_board_renders_date_only_time_for_async_hydration(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [
            {
                "id": "123",
                "title": "전날 글",
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "2026-04-16 23:59:59",
                "time_display": "04.16",
                "needs_time_hydrate": True,
                "voteup_count": 0,
            }
        ], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()

    response = app.test_client().get("/board?board=test&page=2")
    soup = BeautifulSoup(response.data, "html.parser")
    time_node = soup.select_one("[data-board-time][data-post-id='123']")

    assert time_node.get_text(strip=True) == "04.16"
    assert time_node["data-needs-time-hydrate"] == "1"
    assert "2026-04-16 23:59:59" not in response.get_data(as_text=True)
    assert soup.select_one("script[src*='board_time_hydrator.js']") is not None


def test_read_social_meta_uses_canonical_url(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, head_id=None, **kwargs):
        assert pid == 123
        assert board == "test"
        assert kind == "minor"
        assert recommend == 1
        assert head_id == "10"
        return (
            {
                "title": "v2 공유 글",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "contents": "v2 미리보기 설명",
                "html": "<p>본문</p>",
                "related_posts": [],
            },
            [],
            ["https://images.dcinside.com/post-a.jpg"],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()
    app.config["PUBLIC_BASE_URL"] = "https://mirror.example"

    response = app.test_client().get(
        "/read?board=test&pid=123&kind=minor&recommend=1&source_page=2&headid=10&gallery_name=%ED%85%8C%EC%8A%A4%ED%8A%B8%20%EA%B0%A4%EB%9F%AC%EB%A6%AC",
        base_url="http://internal.local",
    )
    soup = BeautifulSoup(response.data, "html.parser")
    og_url = soup.select_one('meta[property="og:url"]')["content"]
    related_section = soup.select_one("#related-section")

    assert response.status_code == 200
    assert og_url == "https://mirror.example/read?board=test&pid=123&recommend=1&source_page=2&kind=minor&headid=10"
    assert soup.select_one(".crumb-link").get_text(strip=True) == "← 테스트 갤러리 게시판"
    assert soup.select_one("script[src*='/static/javascript/read_related_loader.js']") is not None
    assert related_section["data-head-id"] == "10"
    assert related_section["data-recommend"] == "1"
    assert related_section["data-gallery-name"] == "테스트 갤러리"


def test_board_times_endpoint_returns_precise_times(monkeypatch):
    seen = {}

    async def fake_precise_times(page, board, recommend, kind=None, search_type=None, search_keyword=None, head_id=None, target_ids=None):
        seen.update(
            {
                "page": page,
                "board": board,
                "recommend": recommend,
                "kind": kind,
                "search_type": search_type,
                "search_keyword": search_keyword,
                "head_id": head_id,
                "target_ids": target_ids,
            }
        )
        return {"123": "2026-04-16 12:00:00"}

    monkeypatch.setattr(routes, "async_board_precise_times", fake_precise_times)
    app = create_app()

    response = app.test_client().get(
        "/board/times?board=test&page=2&recommend=1&kind=minor&headid=10&s_type=subject&serval=hello&ids=123,124"
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "times": {"123": "2026-04-16 12:00"}}
    assert seen == {
        "page": 2,
        "board": "test",
        "recommend": 1,
        "kind": "minor",
        "search_type": "subject",
        "search_keyword": "hello",
        "head_id": "10",
        "target_ids": ["123", "124"],
    }


def test_board_head_category_tabs_filter_and_preserve_links(monkeypatch):
    seen = {}

    async def fake_board_payload(page, board, recommend, kind=None, head_id=None, **kwargs):
        seen["head_id"] = head_id
        return [
            {
                "id": "123",
                "title": "정보 글",
                "comment_count": 0,
                "subject": "📪정보",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            }
        ], [
            {"head_id": None, "label": "전체", "active": head_id is None},
            {"head_id": "0", "label": "일반", "active": head_id == "0"},
            {"head_id": "10", "label": "📪정보", "active": head_id == "10"},
        ]

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()

    response = app.test_client().get("/board?board=test&headid=10")
    soup = BeautifulSoup(response.data, "html.parser")
    active_tab = soup.select_one(".board-category-tab.active")
    read_query = parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)
    main_tab_queries = [
        parse_qs(urlparse(link["href"]).query)
        for link in soup.select(".top-tabs a.tab-item")[:2]
    ]

    assert seen["head_id"] == "10"
    assert active_tab.get_text(strip=True) == "📪정보"
    assert read_query["headid"] == ["10"]
    assert main_tab_queries[0]["headid"] == ["10"]
    assert main_tab_queries[1]["headid"] == ["10"]


def test_board_renders_image_icon_before_image_post_title(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [
            {
                "id": "123",
                "title": "사진 있는 글",
                "has_image": True,
                "isrecommend": False,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "author_role": "manager",
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "124",
                "title": "텍스트 글",
                "has_image": False,
                "isrecommend": False,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "author_role": "submanager",
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "125",
                "title": "동영상 글",
                "has_image": False,
                "has_video": True,
                "isrecommend": False,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "126",
                "title": "사진 없는 개념글",
                "has_image": False,
                "isrecommend": True,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "127",
                "title": "사진 있는 개념글",
                "has_image": True,
                "isrecommend": True,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "128",
                "title": "동영상 있는 개념글",
                "has_image": False,
                "has_video": True,
                "isrecommend": True,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
        ], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()

    response = app.test_client().get("/board?board=test")
    soup = BeautifulSoup(response.data, "html.parser")
    items = soup.select("a.feed-item")

    assert items[0].select_one(".feed-image-icon") is not None
    assert items[0].select_one(".feed-image-icon")["aria-label"] == "사진 첨부"
    assert items[0].select_one(".feed-image-icon + .feed-title") is not None
    assert items[0].select_one(".author-text.author-role-manager") is not None
    assert items[1].select_one(".feed-image-icon") is None
    assert items[1].select_one(".feed-recommend-icon") is None
    assert items[1].select_one(".author-text.author-role-submanager") is not None
    assert items[2].select_one(".feed-play-icon") is not None
    assert items[2].select_one(".feed-image-icon") is None
    assert items[3].select_one(".feed-recommend-icon.is-plain") is not None
    assert items[3].select_one(".feed-recommend-icon .flame-outer") is not None
    assert items[3].select_one(".feed-image-icon") is None
    assert items[4].select_one(".feed-recommend-icon.is-hot") is not None
    assert items[4].select_one(".feed-recommend-icon .flame-inner") is not None
    assert items[4].select_one(".feed-image-icon") is None
    assert items[5].select_one(".feed-recommend-icon.is-video") is not None
    assert items[5].select_one(".feed-play-icon") is None


def test_read_renders_embedded_related_post_icons_and_subject(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "본문",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "html": "<p>본문</p>",
                "related_posts": [
                    {
                        "id": "201",
                        "title": "사진 있는 글",
                        "subject": "말머리",
                        "has_image": True,
                        "isimage": False,
                        "has_video": False,
                        "isvideo": False,
                        "isrecommend": False,
                        "author": "익명",
                        "author_code": None,
                        "time": "-",
                        "comment_count": 3,
                        "voteup_count": 1,
                    },
                    {
                        "id": "202",
                        "title": "동영상 글",
                        "has_image": False,
                        "has_video": True,
                        "isrecommend": False,
                        "author": "익명",
                        "author_code": None,
                        "time": "-",
                        "comment_count": 0,
                        "voteup_count": 2,
                    },
                    {
                        "id": "203",
                        "title": "사진 없는 개념글",
                        "has_image": False,
                        "has_video": False,
                        "isrecommend": True,
                        "author": "익명",
                        "author_code": None,
                        "time": "-",
                        "comment_count": 0,
                        "voteup_count": 3,
                    },
                    {
                        "id": "204",
                        "title": "사진 있는 개념글",
                        "has_image": True,
                        "has_video": False,
                        "isrecommend": True,
                        "author": "익명",
                        "author_code": None,
                        "time": "-",
                        "comment_count": 0,
                        "voteup_count": 4,
                    },
                    {
                        "id": "205",
                        "title": "텍스트 글",
                        "has_image": "sp-lst-txt",
                        "isimage": "0",
                        "has_video": False,
                        "isvideo": False,
                        "isrecommend": False,
                        "author": "익명",
                        "author_code": None,
                        "time": "-",
                        "comment_count": 0,
                        "voteup_count": 5,
                    },
                ],
            },
            [],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()

    response = app.test_client().get("/read?board=test&pid=100")
    soup = BeautifulSoup(response.data, "html.parser")
    items = soup.select("#related-list a.feed-item")

    assert response.status_code == 200
    assert len(items) == 5
    assert items[0].select_one(".feed-image-icon") is not None
    assert items[0].select_one(".feed-image-icon + .feed-title") is not None
    assert items[0].select_one(".post-subject").text == "[말머리]"
    assert items[0].select_one(".reply-count").text == "[3]"
    assert items[1].select_one(".feed-play-icon") is not None
    assert items[1].select_one(".feed-image-icon") is None
    assert items[2].select_one(".feed-recommend-icon.is-plain") is not None
    assert items[2].select_one(".feed-image-icon") is None
    assert items[3].select_one(".feed-recommend-icon.is-hot") is not None
    assert items[3].select_one(".feed-image-icon") is None
    assert items[4].select_one(".feed-image-icon") is None
    assert items[4].select_one(".feed-play-icon") is None
    assert items[4].select_one(".feed-recommend-icon") is None


def test_read_renders_social_preview_meta_with_public_image_url(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "공유 미리보기 글",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "contents": "첫 문장입니다. URL 미리보기 설명으로 들어갑니다.",
                "html": "<p>본문</p>",
                "related_posts": [],
            },
            [],
            ["https://images.dcinside.com/post-a.jpg"],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()
    app.config["PUBLIC_BASE_URL"] = "https://mirror.example"

    response = app.test_client().get(
        "/read?board=test&pid=123&kind=minor&recommend=1&source_page=2&headid=10",
        base_url="http://internal.local",
    )
    soup = BeautifulSoup(response.data, "html.parser")

    og_url = soup.select_one('meta[property="og:url"]')["content"]
    og_image = soup.select_one('meta[property="og:image"]')["content"]
    image_query = parse_qs(urlparse(og_image).query)

    assert response.status_code == 200
    assert soup.select_one('meta[property="og:title"]')["content"] == "공유 미리보기 글"
    assert soup.select_one('meta[property="og:description"]')["content"] == "첫 문장입니다. URL 미리보기 설명으로 들어갑니다."
    assert soup.select_one('meta[name="twitter:card"]')["content"] == "summary_large_image"
    assert soup.select_one('meta[name="twitter:image"]')["content"] == og_image
    assert soup.select_one('meta[property="og:image:secure_url"]')["content"] == og_image
    assert og_url == "https://mirror.example/read?board=test&pid=123&recommend=1&source_page=2&kind=minor&headid=10"
    assert urlparse(og_image).scheme == "https"
    assert urlparse(og_image).netloc == "mirror.example"
    assert urlparse(og_image).path == "/media"
    assert image_query["src"] == ["https://images.dcinside.com/post-a.jpg"]
    assert image_query["board"] == ["test"]
    assert image_query["pid"] == ["123"]
    assert image_query["kind"] == ["minor"]


def test_read_social_preview_skips_video_sources_for_image_meta(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "동영상 글",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "contents": "동영상 본문",
                "html": "<p>본문</p>",
                "related_posts": [],
            },
            [],
            [
                "https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4&no=123",
                "https://dcimg7.dcinside.co.kr/viewimage.php?id=poster",
            ],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()
    app.config["PUBLIC_BASE_URL"] = "https://mirror.example"

    response = app.test_client().get("/read?board=test&pid=123")
    soup = BeautifulSoup(response.data, "html.parser")
    og_image = soup.select_one('meta[property="og:image"]')["content"]
    image_query = parse_qs(urlparse(og_image).query)

    assert response.status_code == 200
    assert soup.select_one('meta[name="twitter:card"]')["content"] == "summary_large_image"
    assert image_query["src"] == ["https://dcimg7.dcinside.co.kr/viewimage.php?id=poster"]


def test_read_related_json_serializes_post_flags_and_subject(monkeypatch):
    seen = {}

    async def fake_async_related_after_position(
        pid,
        after_pid,
        board,
        kind=None,
        limit=12,
        source_page=0,
        recommend=0,
        head_id=None,
        **kwargs,
    ):
        seen["head_id"] = head_id
        return (
            [
                {
                    "id": "301",
                    "title": "텍스트 글",
                    "subject": "일반",
                    "has_image": "sp-lst-txt",
                    "isimage": "0",
                    "has_video": False,
                    "isvideo": False,
                    "isrecommend": False,
                    "author": "익명",
                    "author_code": None,
                    "author_role": "manager",
                    "time": "-",
                    "comment_count": 0,
                    "voteup_count": 0,
                },
                {
                    "id": "302",
                    "title": "동영상 개념글",
                    "subject": "영상",
                    "has_image": False,
                    "isimage": False,
                    "has_video": "true",
                    "isvideo": "1",
                    "isrecommend": "true",
                    "author": "익명",
                    "author_code": None,
                    "author_role": "submanager",
                    "time": "-",
                    "comment_count": 2,
                    "voteup_count": 9,
                },
            ],
            True,
        )

    monkeypatch.setattr(routes, "async_related_after_position", fake_async_related_after_position)
    app = create_app()

    response = app.test_client().get("/read/related?board=test&pid=100&headid=10")
    payload = response.get_json()

    assert response.status_code == 200
    assert seen["head_id"] == "10"
    assert payload["has_more"] is True
    assert payload["items"][0]["subject"] == "일반"
    assert payload["items"][0]["has_image"] is False
    assert payload["items"][0]["isimage"] is False
    assert payload["items"][0]["has_video"] is False
    assert payload["items"][0]["isrecommend"] is False
    assert payload["items"][0]["author_role"] == "manager"
    assert payload["items"][1]["subject"] == "영상"
    assert payload["items"][1]["has_video"] is True
    assert payload["items"][1]["isvideo"] is True
    assert payload["items"][1]["isrecommend"] is True
    assert payload["items"][1]["author_role"] == "submanager"


def test_board_normalizes_page_and_recommend_inputs(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        assert page == 1
        assert board == "test"
        assert recommend == 0
        return [], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()

    response = app.test_client().get("/board?board=test&recommend=2&page=0")

    assert response.status_code == 200


def test_board_rejects_invalid_board_and_kind(monkeypatch):
    async def fail_board_payload(*args, **kwargs):
        raise AssertionError("invalid board input must be rejected before upstream fetch")

    monkeypatch.setattr(routes, "async_index_with_head_categories", fail_board_payload)
    app = create_app()
    client = app.test_client()

    assert client.get("/board?board=../secret").status_code == 400
    assert client.get("/board?board=test&kind=weird").status_code == 400


def test_read_rejects_non_positive_pid(monkeypatch):
    async def fail_async_read(*args, **kwargs):
        raise AssertionError("invalid pid must be rejected before upstream fetch")

    monkeypatch.setattr(routes, "async_read", fail_async_read)
    app = create_app()

    assert app.test_client().get("/read?board=test&pid=0").status_code == 404


def test_related_loader_appends_related_results_without_replacing_existing_rows():
    script = (PROJECT_ROOT / "app/static/javascript/read_related_loader.js").read_text()

    assert "function appendItems(" in script
    assert "[data-related-loader-status='1'], .empty-row" in script
    assert "getRenderedPostState(list)" in script
    assert "renderedIds[postId]" in script
    assert "context.list.appendChild(createItemNode" in script
    assert "clearChildren" not in script
    assert "lastPostId" in script
    assert 'params.set("after_pid", afterPid)' in script
    assert "section.dataset.headId" in script
    assert 'params.set("headid", headId)' in script
    assert 'href += "&headid="' in script
    assert "function responseHasMore(" in script
    assert "has_more" in script
    assert "function createFeedStatusIcon(" in script
    assert "function postHasImage(" in script
    assert "function postHasVideo(" in script
    assert "feed-recommend-icon" in script
    assert "post-subject" in script
    assert '"has_next"' in script
    assert "mirror:related:" not in script
    assert "cachedResult.items.length > 0" not in script
    assert "payload.ok === false" in script
    assert 'setButtonState(button, "idle");' in script
    assert 'setButtonState(button, "refresh");' in script
    assert 'setButtonState(button, "retry");' in script
    assert 'setButtonState(button, "no-more");' in script
    assert "cached.items" not in script
    assert "cached.items.length === 0" not in script
    assert 'params.set("recommend", "1")' in script


def test_board_time_hydrator_sends_rendered_post_ids():
    script = (PROJECT_ROOT / "app/static/javascript/board_time_hydrator.js").read_text()

    assert 'params.set("ids", postIds.join(","))' in script
    assert "var postIds = Object.keys(targets)" in script
    assert "buildParams(section, postIds)" in script


def test_theme_toggle_persists_and_updates_accessibility_state():
    template = (PROJECT_ROOT / "app/templates/base.html").read_text()
    script = (PROJECT_ROOT / "app/static/javascript/read_state.js").read_text()
    style = (PROJECT_ROOT / "app/static/css/main.css").read_text()

    assert 'class="theme-toggle"' in template
    assert 'window.localStorage.getItem("mirror_theme_v1") === "light"' in template
    assert 'root.dataset.theme = theme' in template
    assert 'THEME_STORAGE_KEY = "mirror_theme_v1"' in script
    assert "window.localStorage.setItem(THEME_STORAGE_KEY" in script
    assert "document.documentElement.dataset.theme" in script
    assert "body.dataset.theme" in script
    assert 'button.setAttribute("aria-label", actionLabel)' in script
    assert "mirror-light-theme-overrides" not in script
    assert "--bg: #FFFFFF;" in style
    assert "--accent: #3182F6;" in style


def test_dccon_block_toggle_folds_comments_and_prevents_initial_image_src(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "title",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "html": "<p>body</p>",
                "related_posts": [],
            },
            [
                {
                    "author": "댓글러",
                    "author_code": None,
                    "time": "-",
                    "contents": "",
                    "parent_id": None,
                    "is_reply": False,
                    "dccon": "https://dccon.dcinside.com/original.png",
                }
            ],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()

    response = app.test_client().get("/read?board=test&pid=123")
    soup = BeautifulSoup(response.data, "html.parser")
    dccon = soup.select_one("img.dccon")
    template = (PROJECT_ROOT / "app/templates/base.html").read_text()
    script = (PROJECT_ROOT / "app/static/javascript/read_state.js").read_text()
    style = (PROJECT_ROOT / "app/static/css/main.css").read_text()

    assert response.status_code == 200
    assert soup.select_one(".dccon-toggle") is not None
    assert dccon is not None
    assert not dccon.has_attr("src")
    assert dccon["data-dccon-src"].startswith("/media?src=https")
    assert dccon.has_attr("hidden")
    assert soup.select_one(".dccon-blocked-note") is None
    assert "이모티콘 자동 차단 전환" in template
    assert 'window.localStorage.getItem("mirror_dccon_block_v1") === "1"' in template
    assert 'DCCON_BLOCK_STORAGE_KEY = "mirror_dccon_block_v1"' in script
    assert 'image.removeAttribute("src")' in script
    assert "차단된 이모티콘 보기" in script
    assert "comment-dccon-block-hidden" in script
    assert ".comment-dccon-block-hidden" in style


def test_read_passes_head_id_to_initial_document_fetch(monkeypatch):
    seen = {}

    async def fake_async_read(pid, board, kind=None, recommend=0, head_id=None, **kwargs):
        seen["pid"] = pid
        seen["board"] = board
        seen["kind"] = kind
        seen["head_id"] = head_id
        return (
            {
                "title": "title",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "html": "<p>body</p>",
                "related_posts": [],
            },
            [],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()

    response = app.test_client().get("/read?board=test&pid=123&kind=minor&headid=10")
    soup = BeautifulSoup(response.data, "html.parser")

    assert response.status_code == 200
    assert seen == {"pid": 123, "board": "test", "kind": "minor", "head_id": "10"}
    assert soup.select_one("#related-section")["data-head-id"] == "10"


def test_read_omits_seconds_from_post_comment_and_related_times(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "title",
                "author": "익명",
                "author_code": None,
                "time": datetime(2026, 4, 16, 12, 34, 56, 123456),
                "voteup_count": 0,
                "html": "<p>body</p>",
                "related_posts": [
                    {
                        "id": "122",
                        "title": "related title",
                        "author": "작성자",
                        "author_code": None,
                        "time": "2026-04-16 12:36:58",
                        "comment_count": 0,
                        "voteup_count": 2,
                        "source_page": 0,
                    }
                ],
            },
            [
                {
                    "author": "댓글러",
                    "author_code": None,
                    "time": "04.16 12:35:57",
                    "contents": "댓글",
                    "parent_id": None,
                    "is_reply": False,
                    "dccon": None,
                }
            ],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()

    response = app.test_client().get("/read?board=test&pid=123")
    text = response.get_data(as_text=True)
    soup = BeautifulSoup(response.data, "html.parser")

    assert response.status_code == 200
    assert "2026-04-16 12:34" in soup.select_one(".article-meta").get_text(" ", strip=True)
    assert "04.16 12:35" in soup.select_one(".comment-meta").get_text(" ", strip=True)
    assert "2026-04-16 12:36" in soup.select_one("#related-list").get_text(" ", strip=True)
    assert "12:34:56" not in text
    assert "12:35:57" not in text
    assert "12:36:58" not in text
    assert ".123456" not in text


def test_read_renders_embedded_related_posts_without_extra_related_request(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        assert recommend == 1
        assert kwargs.get("head_id") is None
        return (
            {
                "title": "title",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "html": "<p>body</p>",
                "related_posts": [
                    {
                        "id": "122",
                        "title": "embedded title",
                        "author": "작성자",
                        "author_code": "3.4",
                        "time": "-",
                        "comment_count": 5,
                        "voteup_count": 2,
                        "source_page": 0,
                    }
                ],
            },
            [],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    app = create_app()

    response = app.test_client().get("/read?board=test&pid=123&recommend=1&source_page=2")
    soup = BeautifulSoup(response.data, "html.parser")
    related_link = soup.select_one("#related-list a.feed-item")

    assert related_link is not None
    assert "embedded title" in related_link.get_text(" ", strip=True)
    assert soup.select_one("#related-section")["data-recommend"] == "1"
    assert "recommend=1" in related_link["href"]
    assert "source_page=2" in related_link["href"]
    assert soup.select_one("#related-load-button") is not None


def test_comment_spam_filter_keeps_summary_even_when_every_comment_is_filtered():
    script = (PROJECT_ROOT / "app/static/javascript/comment_spam_filter.js").read_text()

    assert "hidden.length >= items.length" not in script
    assert "SHORT_REACTION_REPEAT_THRESHOLD" in script
    assert "getRepeatThreshold" in script
    assert "aria-expanded" in script
    assert 'li.classList.add("comment-spam-hidden")' in script


def test_related_loader_does_not_double_wrap_bracketed_subject():
    script = (PROJECT_ROOT / "app/static/javascript/read_related_loader.js").read_text()

    assert "function formatSubject" in script
    assert 'subject.charAt(0) === "["' in script
    assert 'subject.textContent = formatSubject(item.subject);' in script


def _encode_recent_cookie(rows):
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def test_recent_gallery_preserves_recommend_context_from_board(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()
    client = app.test_client()

    client.get("/board?board=test&recommend=1&page=1&kind=minor")
    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    link = soup.select_one("a.feed-item")
    query = parse_qs(urlparse(link["href"]).query)

    assert query["board"] == ["test"]
    assert query["recommend"] == ["1"]
    assert query["kind"] == ["minor"]
    assert "개념글" in link.get_text(" ", strip=True)


def test_recent_gallery_dedupes_by_recommend_context(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()
    client = app.test_client()

    client.get("/board?board=test&recommend=1&page=1&kind=minor")
    client.get("/board?board=test&recommend=0&page=1&kind=minor")
    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    recommends = [parse_qs(urlparse(link["href"]).query)["recommend"][0] for link in soup.select("a.feed-item")]

    assert recommends[:2] == ["0", "1"]


def test_recent_gallery_dedupes_missing_kind_against_specific_kind(monkeypatch):
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 1))
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {"board": "thesingularity", "kind": None, "recommend": 0, "visited_at": 3},
                {"board": "thesingularity", "kind": "minor", "recommend": 0, "visited_at": 2},
            ]
        ),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    rows = soup.select("a.feed-item")
    query = parse_qs(urlparse(rows[0]["href"]).query)

    assert len(rows) == 1
    assert query["board"] == ["thesingularity"]
    assert query["kind"] == ["minor"]
    assert "마이너" in rows[0].get_text(" ", strip=True)


def test_recent_gallery_keeps_specific_kind_when_followup_visit_omits_kind(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", fake_board_payload)
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {
                    "board": "thesingularity",
                    "name": "특이점이 온다",
                    "kind": "minor",
                    "recommend": 0,
                    "visited_at": 1,
                }
            ]
        ),
    )

    client.get("/board?board=thesingularity")
    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    rows = soup.select("a.feed-item")
    query = parse_qs(urlparse(rows[0]["href"]).query)

    assert len(rows) == 1
    assert rows[0].select_one(".feed-title").get_text(strip=True) == "특이점이 온다"
    assert query["kind"] == ["minor"]


def test_recent_gallery_old_cookie_without_recommend_defaults_to_normal():
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie([{"board": "old_cookie", "kind": "minor", "visited_at": 1}]),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    query = parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)

    assert query["board"] == ["old_cookie"]
    assert query["recommend"] == ["0"]
    assert query["kind"] == ["minor"]


def test_recent_gallery_renders_kind_labels_in_korean(monkeypatch):
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 1))
    monkeypatch.setattr(routes, "search_galleries", lambda query: [])
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {"board": "minor_board", "kind": "minor", "recommend": 0, "visited_at": 1},
                {"board": "mini_board", "kind": "mini", "recommend": 0, "visited_at": 2},
                {"board": "normal_board", "kind": None, "recommend": 0, "visited_at": 3},
            ]
        ),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    badges = [node.get_text(strip=True) for node in soup.select(".gallery-badge")]

    assert response.status_code == 200
    assert badges == ["마이너", "미니", "일반"]


def test_recent_gallery_prefers_korean_name_and_keeps_board_id(monkeypatch):
    def fake_heung_galleries():
        raise AssertionError("/recent should not load heung galleries while rendering")

    def fake_search_galleries(query):
        raise AssertionError("/recent should not search galleries while rendering")

    monkeypatch.setattr(routes, "get_heung_galleries", fake_heung_galleries)
    monkeypatch.setattr(routes, "search_galleries", fake_search_galleries)
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {
                    "board": "thesingularity",
                    "name": "특이점이 온다",
                    "kind": "minor",
                    "recommend": 0,
                    "visited_at": 1,
                },
                {"board": "dcbest", "name": "실시간 베스트", "kind": None, "recommend": 0, "visited_at": 2},
            ]
        ),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    rows = soup.select("a.feed-item")

    assert response.status_code == 200
    assert rows[0].select_one(".feed-title").get_text(strip=True) == "특이점이 온다"
    assert "thesingularity" in rows[0].select_one(".feed-meta-left").get_text(" ", strip=True)
    assert rows[1].select_one(".feed-title").get_text(strip=True) == "실시간 베스트"
    assert "dcbest" in rows[1].select_one(".feed-meta-left").get_text(" ", strip=True)


def test_recent_gallery_applies_korean_name_to_recommend_row(monkeypatch):
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 1))
    monkeypatch.setattr(routes, "search_galleries", lambda query: [])
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {
                    "board": "thesingularity",
                    "name": "특이점이 온다",
                    "kind": "minor",
                    "recommend": 0,
                    "visited_at": 2,
                },
                {
                    "board": "thesingularity",
                    "name": "thesingularity",
                    "kind": "minor",
                    "recommend": 1,
                    "visited_at": 1,
                },
            ]
        ),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    rows = soup.select("a.feed-item")
    recommend_query = parse_qs(urlparse(rows[1]["href"]).query)

    assert response.status_code == 200
    assert rows[1].select_one(".feed-title").get_text(strip=True) == "특이점이 온다"
    assert "개념글" in rows[1].get_text(" ", strip=True)
    assert recommend_query["recommend"] == ["1"]
    assert recommend_query["gallery_name"] == ["특이점이 온다"]


def test_recent_route_tolerates_non_finite_cookie_timestamp():
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {
                    "board": "safe_recent",
                    "name": "안전한 최근 방문",
                    "kind": None,
                    "recommend": 0,
                    "visited_at": "1e309",
                }
            ]
        ),
    )

    response = client.get("/recent")

    assert response.status_code == 200
    assert "안전한 최근 방문" in response.get_data(as_text=True)


def test_recent_gallery_uses_heung_cache_for_missing_names(monkeypatch):
    def fail_search(query):
        raise AssertionError("/recent should not call search_galleries")

    monkeypatch.setattr(
        routes,
        "get_heung_galleries",
        lambda: (
            [
                {
                    "board_id": "unknown_a",
                    "board_kind": "minor",
                    "name": "알려진 갤러리",
                }
            ],
            1,
        ),
    )
    monkeypatch.setattr(routes, "search_galleries", fail_search)
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {"board": "unknown_a", "kind": "minor", "recommend": 0, "visited_at": 1},
                {"board": "unknown_b", "kind": "minor", "recommend": 1, "visited_at": 2},
            ]
        ),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    rows = soup.select("a.feed-item")

    assert response.status_code == 200
    titles = {row.select_one(".feed-meta-left span").get_text(strip=True): row.select_one(".feed-title").get_text(strip=True) for row in rows}
    assert titles["unknown_a"] == "알려진 갤러리"
    assert titles["unknown_b"] == "unknown_b"


def test_recent_cookie_strips_names_when_payload_is_too_large(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_COOKIE_MAX_BYTES", 120)
    app = create_app()
    rows = [
        {
            "board": "longname",
            "name": "가" * 80,
            "kind": "minor",
            "recommend": 0,
            "visited_at": 1,
        }
    ]

    with app.test_request_context("/"):
        response = Response("")
        recent.save_recent_cookie(response, rows)

    cookie_header = next(
        header
        for header in response.headers.getlist("Set-Cookie")
        if header.startswith(f"{recent.RECENT_COOKIE_NAME}=")
    )
    encoded = cookie_header.split(";", 1)[0].split("=", 1)[1]
    decoded = base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode("ascii")).decode("utf-8")

    assert "name" not in json.loads(decoded)[0]


def test_recent_cookie_payload_never_exceeds_configured_limit(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_COOKIE_MAX_BYTES", 3600)
    app = create_app()
    rows = [
        {
            "board": f"{index:02d}" + ("a" * 78),
            "name": None,
            "kind": "minor",
            "recommend": 1,
            "visited_at": 9_999_999_999.123,
        }
        for index in range(30)
    ]

    with app.test_request_context("/"):
        response = Response("")
        recent.save_recent_cookie(response, rows)

    cookie_header = next(
        header
        for header in response.headers.getlist("Set-Cookie")
        if header.startswith(f"{recent.RECENT_COOKIE_NAME}=")
    )
    encoded = cookie_header.split(";", 1)[0].split("=", 1)[1]
    decoded_rows = json.loads(
        base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode("ascii")).decode("utf-8")
    )

    assert len(encoded.encode("ascii")) <= recent.RECENT_COOKIE_MAX_BYTES
    assert decoded_rows
    assert decoded_rows[0]["board"] == rows[0]["board"]


def test_recent_cookie_trimmed_rows_are_restored_from_server_cache(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_COOKIE_MAX_BYTES", 3600)
    recent.RECENT_SERVER_CACHE.clear()
    app = create_app()
    cache_key = "trimmed-cookie-cache-key"
    rows = [
        {
            "board": f"{index:02d}" + ("a" * 78),
            "name": None,
            "kind": "minor",
            "recommend": 1,
            "visited_at": 9_999_999_999.123 - index,
        }
        for index in range(30)
    ]

    with app.test_request_context("/"):
        encoded = recent._fit_recent_cookie_value(rows)
        recent.set_recent_server_cache(cache_key, rows)

    decoded_rows = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
    assert len(decoded_rows) < len(rows)

    with app.test_request_context(
        "/",
        headers={
            "Cookie": (
                f"{recent.RECENT_COOKIE_NAME}={encoded}; "
                f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
            )
        },
    ):
        restored = recent.load_recent_entries()

    assert [row["board"] for row in restored] == [row["board"] for row in rows]
    recent.RECENT_SERVER_CACHE.clear()


def test_recent_cookie_and_newer_server_generation_merge_by_visit_time():
    app = create_app()
    cache_key = "concurrent-visit-key"
    cookie_rows = [
        {"board": "a", "kind": "minor", "recommend": 0, "visited_at": 2},
        {"board": "old", "kind": "minor", "recommend": 0, "visited_at": 1},
    ]
    server_rows = [
        {"board": "b", "kind": "minor", "recommend": 0, "visited_at": 3},
        *cookie_rows,
    ]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    recent.set_recent_server_cache(cache_key, server_rows)

    with app.test_request_context(
        "/recent",
        headers={
            "Cookie": (
                f"{recent.RECENT_COOKIE_NAME}={_encode_recent_cookie(cookie_rows)}; "
                f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
            )
        },
    ):
        rows = recent.load_recent_entries()

    assert [row["board"] for row in rows] == ["b", "a", "old"]
    recent.RECENT_SERVER_CACHE.clear()


def test_recent_server_cache_atomically_keeps_concurrent_visits():
    app = create_app()
    cache_key = "concurrent-write-key"
    old_row = {"board": "old", "kind": "minor", "recommend": 0, "visited_at": 1}
    stale_a_generation = [
        {"board": "a", "kind": "minor", "recommend": 0, "visited_at": 2},
        old_row,
    ]
    newer_b_generation = [
        {"board": "b", "kind": "minor", "recommend": 0, "visited_at": 3},
        old_row,
    ]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()

    recent.set_recent_server_cache(cache_key, newer_b_generation)
    merged = recent.set_recent_server_cache(cache_key, stale_a_generation)

    assert [row["board"] for row in merged] == ["b", "a", "old"]
    with app.test_request_context(
        "/recent",
        headers={
            "Cookie": (
                f"{recent.RECENT_COOKIE_NAME}={_encode_recent_cookie(stale_a_generation)}; "
                f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
            )
        },
    ):
        rows = recent.load_recent_entries()

    assert [row["board"] for row in rows] == ["b", "a", "old"]
    recent.RECENT_SERVER_CACHE.clear()


def test_recent_cookie_compact_rows_merge_names_from_server_cache():
    app = create_app()
    cache_key = "visitor-name-key"
    compact_rows = [{"board": "thesingularity", "kind": "minor", "recommend": 0, "visited_at": 1}]
    named_rows = [
        {
            "board": "thesingularity",
            "name": "특이점이 온다",
            "kind": "minor",
            "recommend": 0,
            "visited_at": 1,
        }
    ]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    recent.set_recent_server_cache(cache_key, named_rows)

    with app.test_request_context(
        "/recent",
        headers={
            "Cookie": (
                f"{recent.RECENT_COOKIE_NAME}={_encode_recent_cookie(compact_rows)}; "
                f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
            )
        },
    ):
        rows = recent.load_recent_entries()

    assert rows[0]["name"] == "특이점이 온다"
    assert rows[0]["kind"] == "minor"


def test_recent_cookie_compact_normal_row_merges_specific_name_from_server_cache():
    app = create_app()
    cache_key = "visitor-name-key"
    compact_rows = [{"board": "thesingularity", "kind": None, "recommend": 0, "visited_at": 2}]
    named_rows = [
        {
            "board": "thesingularity",
            "name": "특이점이 온다",
            "kind": "minor",
            "recommend": 0,
            "visited_at": 1,
        }
    ]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    recent.set_recent_server_cache(cache_key, named_rows)

    with app.test_request_context(
        "/recent",
        headers={
            "Cookie": (
                f"{recent.RECENT_COOKIE_NAME}={_encode_recent_cookie(compact_rows)}; "
                f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
            )
        },
    ):
        rows = recent.load_recent_entries()

    assert len(rows) == 1
    assert rows[0]["name"] == "특이점이 온다"
    assert rows[0]["kind"] == "minor"


def test_touch_recent_gallery_keeps_existing_name_when_new_visit_has_no_name(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", fake_board_payload)
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie(
            [
                {
                    "board": "thesingularity",
                    "name": "특이점이 온다",
                    "kind": "minor",
                    "recommend": 1,
                    "visited_at": 1,
                }
            ]
        ),
    )

    client.get("/board?board=thesingularity&recommend=1&kind=minor")
    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    row = soup.select_one("a.feed-item")

    assert row.select_one(".feed-title").get_text(strip=True) == "특이점이 온다"


def test_recent_gallery_ignores_bad_visited_at_in_cookie():
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie([{"board": "bad_time", "kind": "minor", "visited_at": "bad"}]),
    )

    response = client.get("/recent")

    assert response.status_code == 200


def test_recent_server_cache_prunes_expired_fallback():
    app = create_app()
    key = "visitor-test-key"
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
        recent.RECENT_SERVER_CACHE[key] = {
            "entries": [{"board": "expired", "kind": None, "recommend": 0, "visited_at": 1}],
            "expires_at": 0,
            "last_seen": 0,
        }

    with app.test_request_context(
        "/recent",
        headers={"Cookie": f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={key}"},
    ):
        assert recent.load_recent_entries() == []

    assert key not in recent.RECENT_SERVER_CACHE


def test_recent_server_cache_discards_obsolete_list_shape():
    app = create_app()
    key = "visitor-test-key"
    rows = [{"board": "obsolete", "kind": None, "recommend": 0, "visited_at": 1}]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
        recent.RECENT_SERVER_CACHE[key] = rows

    with app.test_request_context(
        "/recent",
        headers={"Cookie": f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={key}"},
    ):
        assert recent.load_recent_entries() == []

    with recent.RECENT_SERVER_CACHE_LOCK:
        assert key not in recent.RECENT_SERVER_CACHE


def test_recent_server_cache_does_not_share_by_ip_and_user_agent():
    app = create_app()
    old_shared_key = "127.0.0.1|pytest"
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
        recent.RECENT_SERVER_CACHE[old_shared_key] = {
            "entries": [{"board": "private", "kind": None, "recommend": 0, "visited_at": 1}],
            "expires_at": 9999999999,
            "last_seen": 0,
        }

    with app.test_request_context("/recent", environ_base={"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pytest"}):
        assert recent.load_recent_entries() == []


def test_touch_recent_gallery_sets_private_fallback_key_cookie(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "async_index_with_head_categories", fake_board_payload)
    app = create_app()
    response = app.test_client().get("/board?board=test")

    set_cookie_headers = response.headers.getlist("Set-Cookie")

    assert any(header.startswith(f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}=") for header in set_cookie_headers)
    assert any("HttpOnly" in header for header in set_cookie_headers if header.startswith(f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}="))


def test_recent_server_cache_limits_total_keys(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE_TTL", 60)
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE_MAX_KEYS", 2)
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()

    recent.set_recent_server_cache("one", [{"board": "one"}])
    recent.set_recent_server_cache("two", [{"board": "two"}])
    recent.set_recent_server_cache("three", [{"board": "three"}])

    assert len(recent.RECENT_SERVER_CACHE) <= 2


def test_recent_server_cache_set_evicts_expired_foreign_keys(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE_TTL", 60)
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE_MAX_KEYS", 10)
    now = [100.0]
    monkeypatch.setattr(recent.time, "time", lambda: now[0])
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
        recent.RECENT_SERVER_CACHE["expired"] = {
            "entries": [{"board": "expired"}],
            "expires_at": 99.0,
            "last_seen": 1.0,
        }

    recent.set_recent_server_cache("fresh", [{"board": "fresh"}])

    with recent.RECENT_SERVER_CACHE_LOCK:
        assert "expired" not in recent.RECENT_SERVER_CACHE
        assert "fresh" in recent.RECENT_SERVER_CACHE


def test_get_heung_galleries_does_not_hold_cache_lock_while_fetching_or_writing(monkeypatch):
    class TrackingLock:
        def __init__(self):
            self.depth = 0

        def __enter__(self):
            self.depth += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            self.depth -= 1

    lock = TrackingLock()
    calls = []

    def fake_fetch():
        assert lock.depth == 0
        calls.append("fetch")
        return [{"rank": 1, "name": "테스트", "board_id": "test"}]

    def fake_write(updated_at, items):
        assert lock.depth == 0
        calls.append("write")

    monkeypatch.setattr(heung, "HEUNG_CACHE", {"updated_at": 0.0, "items": []})
    monkeypatch.setattr(heung, "HEUNG_CACHE_LOCK", lock)
    monkeypatch.setattr(heung, "HEUNG_REFRESH_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "_read_heung_cache_file", lambda: None)
    monkeypatch.setattr(heung, "_fetch_heung_galleries", fake_fetch)
    monkeypatch.setattr(heung, "_write_heung_cache_file", fake_write)

    items, updated_at = heung.get_heung_galleries()

    assert items == [{"rank": 1, "name": "테스트", "board_id": "test"}]
    assert updated_at > 0
    assert calls == ["fetch", "write"]


def test_get_heung_galleries_returns_stale_while_refreshing(monkeypatch):
    stale_items = [{"rank": 1, "name": "오래된 목록", "board_id": "old"}]
    fresh_items = [{"rank": 1, "name": "새 목록", "board_id": "fresh"}]
    refresh_started = threading.Event()
    allow_refresh_finish = threading.Event()

    def fake_fetch():
        refresh_started.set()
        assert allow_refresh_finish.wait(timeout=2)
        return fresh_items

    monkeypatch.setattr(heung, "HEUNG_CACHE", {"updated_at": 1.0, "items": stale_items})
    monkeypatch.setattr(heung, "HEUNG_CACHE_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "HEUNG_REFRESH_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "_read_heung_cache_file", lambda: None)
    monkeypatch.setattr(heung, "_fetch_heung_galleries", fake_fetch)
    monkeypatch.setattr(heung, "_write_heung_cache_file", lambda updated_at, items: None)

    started_at = time.monotonic()
    items, updated_at = heung.get_heung_galleries()

    assert time.monotonic() - started_at < 0.2
    assert items == stale_items
    assert updated_at == 1.0
    assert refresh_started.wait(timeout=1)

    allow_refresh_finish.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        cached_items, _ = heung._heung_cache_snapshot()
        if cached_items == fresh_items:
            break
        time.sleep(0.01)

    cached_items, cached_updated_at = heung._heung_cache_snapshot()
    assert cached_items == fresh_items
    assert cached_updated_at > 1.0


def test_search_galleries_parses_gallery_search_results(monkeypatch):
    heung.SEARCH_CACHE.clear()

    class FakeResponse:
        text = """
        <div class="integrate_cont gallsch_result_all">
          <ul class="integrate_cont_list">
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/mgallery/board/lists/?id=test_minor">테스트 마이너 ⓜ</a>
              <span class="info ranking">흥한갤 1위</span>
              <span class="info txtnum">글 123개</span>
            </li>
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/board/lists/?id=test_normal">테스트 일반</a>
            </li>
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/board/lists/?id=test_normal">중복</a>
            </li>
          </ul>
        </div>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(heung.requests, "get", lambda *args, **kwargs: FakeResponse())

    items = heung.search_galleries("테스트")

    assert [item["board_id"] for item in items] == ["test_minor", "test_normal"]
    assert items[0]["name"] == "테스트 마이너"
    assert items[0]["board_kind"] == "minor"
    assert items[0]["kind"] == "마이너"
    assert items[0]["extra"] == "흥한갤 1위 | 글 123개"
    assert items[0]["internal_supported"] is True
    assert items[1]["board_kind"] == "normal"


def test_search_galleries_prefers_specific_kind_for_duplicate_board_id(monkeypatch):
    heung.SEARCH_CACHE.clear()

    class FakeResponse:
        text = """
        <div class="integrate_cont gallsch_result_all">
          <ul class="integrate_cont_list">
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/board/lists/?id=dup_board">중복 일반</a>
            </li>
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/mgallery/board/lists/?id=dup_board">중복 마이너 ⓜ</a>
            </li>
          </ul>
        </div>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(heung.requests, "get", lambda *args, **kwargs: FakeResponse())

    items = heung.search_galleries("중복")

    assert len(items) == 1
    assert items[0]["board_id"] == "dup_board"
    assert items[0]["name"] == "중복 마이너"
    assert items[0]["board_kind"] == "minor"
    assert items[0]["kind"] == "마이너"


def test_search_galleries_reuses_short_cache(monkeypatch):
    heung.SEARCH_CACHE.clear()

    class FakeResponse:
        text = """
        <div class="integrate_cont gallsch_result_all">
          <ul class="integrate_cont_list">
            <li>
              <a class="gallname_txt" href="https://gall.dcinside.com/board/lists/?id=test_normal">테스트 일반</a>
            </li>
          </ul>
        </div>
        """

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        return FakeResponse()

    monkeypatch.setattr(heung, "SEARCH_CACHE_TTL", 60)
    monkeypatch.setattr(heung.requests, "get", fake_get)

    first = heung.search_galleries(" 테스트 ")
    first[0]["name"] = "mutated"
    second = heung.search_galleries("테스트")

    assert len(calls) == 1
    assert second[0]["name"] == "테스트 일반"
