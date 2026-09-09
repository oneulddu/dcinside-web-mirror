import pytest

from app import create_app
from app.services import media_proxy


IMAGE_URL = "https://dcimg4.dcinside.co.kr/viewimage.php?id=private-token&no=image-token"


class Upstream:
    is_redirect = False

    def __init__(self, status=200, content_type="text/html", body=b"origin error", **headers):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **headers}
        self.body = body
        self.closed = False
        self.read = False

    def iter_content(self, chunk_size):
        assert not self.closed
        self.read = True
        yield self.body

    def close(self):
        self.closed = True


def install_responses(monkeypatch, *responses, method="GET"):
    calls = []

    def request(url, **kwargs):
        index = len(calls)
        assert index < len(responses), "unexpected extra origin request"
        if index:
            assert responses[index - 1].closed
        calls.append((url, kwargs))
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response

    def wrong_method(*args, **kwargs):
        raise AssertionError("changed HTTP method during recovery")

    monkeypatch.setattr(media_proxy, "_http_head" if method == "HEAD" else "_http_get", request)
    monkeypatch.setattr(media_proxy, "_http_get" if method == "HEAD" else "_http_head", wrong_method)
    return calls


def request_image(method="GET", src=IMAGE_URL, headers=None):
    return create_app().test_client().open(
        "/media", method=method,
        query_string={"src": src, "board": "coffee", "pid": "681267", "kind": "minor"},
        headers=headers,
    )


@pytest.mark.parametrize("status", [200, 403, 500, 502, 503, 504])
def test_transient_non_media_response_recovers_once(monkeypatch, caplog, status):
    failed = Upstream(status)
    image = Upstream(content_type="application/octet-stream", body=b"image bytes")
    calls = install_responses(monkeypatch, failed, image)

    response = request_image()

    assert response.status_code == 200
    assert response.data == b"image bytes"
    assert len(calls) == 2
    assert [call[0] for call in calls] == [IMAGE_URL, IMAGE_URL]
    assert calls[0][1]["headers"] == calls[1][1]["headers"]
    assert calls[0][1]["cookies"] == calls[1][1]["cookies"]
    assert failed.closed and image.closed and not failed.read
    assert "dcimg4.dcinside.co.kr" in caplog.text
    assert str(status) in caplog.text and "text/html" in caplog.text
    assert "private-token" not in caplog.text and "image-token" not in caplog.text


def test_repeated_html_is_rejected_without_caching(monkeypatch):
    failed = [Upstream(), Upstream()]
    calls = install_responses(monkeypatch, *failed)

    response = request_image()

    assert response.status_code == 415
    assert response.data == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 2
    assert all(item.closed and not item.read for item in failed)


@pytest.mark.parametrize("status", [404, 429])
@pytest.mark.parametrize("content_type", ["text/html", "application/octet-stream"])
def test_permanent_or_rate_limited_failure_is_not_retried_or_cached(monkeypatch, status, content_type):
    failed = Upstream(status, content_type, **{"Retry-After": "60"})
    calls = install_responses(monkeypatch, failed)

    response = request_image()

    assert response.status_code == status
    assert response.data == b""
    assert response.headers["Cache-Control"] == "no-store"
    if status == 429:
        assert response.headers["Retry-After"] == "60"
    assert len(calls) == 1 and failed.closed and not failed.read


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_recovery_preserves_method_range_and_partial_metadata(monkeypatch, method):
    failed = Upstream()
    image = Upstream(206, "image/jpeg", b"abc", **{
        "Content-Length": "3", "Content-Range": "bytes 1-3/10",
    })
    calls = install_responses(monkeypatch, failed, image, method=method)

    response = request_image(method=method, headers={"Range": "bytes=1-3"})

    assert response.status_code == 206
    assert response.data == (b"" if method == "HEAD" else b"abc")
    assert response.headers["Content-Length"] == "3"
    assert response.headers["Content-Range"] == "bytes 1-3/10"
    assert all(call[1]["headers"]["Range"] == "bytes=1-3" for call in calls)
    assert all(call[1]["allow_redirects"] is False for call in calls)
    assert failed.closed and image.closed
    if method == "HEAD":
        assert not image.read


@pytest.mark.parametrize("error,status", [
    (media_proxy.UnsafeMediaAddress("private address"), 400),
    (media_proxy.requests.Timeout("origin timed out"), 502),
])
def test_recovery_cannot_bypass_fetch_validation_or_swallow_network_failure(monkeypatch, error, status):
    failed = Upstream()
    calls = install_responses(monkeypatch, failed, error)

    response = request_image()

    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 2 and failed.closed


@pytest.mark.parametrize("src", [
    "https://images.dcinside.com/test.jpg",
    "https://dcimg4.dcinside.co.kr/not-an-image.php",
])
def test_non_image_endpoint_does_not_receive_recovery_requests(monkeypatch, src):
    failed = Upstream()
    calls = install_responses(monkeypatch, failed)

    response = request_image(src=src)

    assert response.status_code == 415
    assert len(calls) == 1 and failed.closed and not failed.read


def test_successful_image_is_not_retried(monkeypatch):
    image = Upstream(content_type="image/png", body=b"image bytes")
    calls = install_responses(monkeypatch, image)

    response = request_image()

    assert response.status_code == 200 and response.data == b"image bytes"
    assert response.headers["Cache-Control"].startswith("public, max-age=")
    assert len(calls) == 1 and image.closed


def test_recovery_rejects_redirect_to_private_host(monkeypatch):
    failed = Upstream()
    redirect = Upstream(302, Location="http://127.0.0.1/private")
    redirect.is_redirect = True
    calls = install_responses(monkeypatch, failed, redirect)

    response = request_image()

    assert response.status_code == 400
    assert len(calls) == 2 and failed.closed and redirect.closed
    assert not redirect.read


def test_recovered_image_still_obeys_size_limit(monkeypatch):
    monkeypatch.setattr(media_proxy, "MEDIA_MAX_BYTES", 3)
    failed = Upstream()
    oversized = Upstream(content_type="image/png", body=b"oversized")
    calls = install_responses(monkeypatch, failed, oversized)

    response = request_image()

    assert response.status_code == 413 and response.data == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 2 and failed.closed and oversized.closed


def test_origin_retry_after_prevents_immediate_retry(monkeypatch):
    failed = Upstream(503, **{"Retry-After": "60"})
    calls = install_responses(monkeypatch, failed)

    response = request_image()

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 1 and failed.closed and not failed.read


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_unsatisfiable_range_preserves_resource_length_without_caching(monkeypatch, method):
    failed = Upstream(416, "video/mp4", **{
        "Content-Range": "bytes */100", "Accept-Ranges": "bytes",
    })
    calls = install_responses(monkeypatch, failed, method=method)

    response = request_image(method=method, headers={"Range": "bytes=200-"})

    assert response.status_code == 416 and response.data == b""
    assert response.headers["Content-Range"] == "bytes */100"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 1 and failed.closed and not failed.read
