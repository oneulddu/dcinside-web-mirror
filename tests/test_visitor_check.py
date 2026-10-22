import re

import pytest
from itsdangerous import TimestampSigner

from app import create_app
from app.services import visitor_check

PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Dest": "document"}
CHALLENGE_MARKER = "visitor-check-message"


@pytest.fixture
def app():
    app = create_app()
    app.config.update(VISITOR_CHECK=True, TESTING=True, SECRET_KEY="visitor-test-secret")
    return app


def _token_from(response):
    match = re.search(r'data-token="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def test_page_without_cookie_gets_challenge_whose_token_unlocks_the_site(app):
    client = app.test_client()

    challenge = client.get("/recent", headers=PAGE_HEADERS)

    assert challenge.status_code == 200
    assert CHALLENGE_MARKER in challenge.get_data(as_text=True)
    assert challenge.headers["Cache-Control"] == "no-store"
    assert "noindex" in challenge.headers["X-Robots-Tag"]

    client.set_cookie(visitor_check.COOKIE, _token_from(challenge))
    page = client.get("/recent", headers=PAGE_HEADERS)

    assert page.status_code == 200
    assert CHALLENGE_MARKER not in page.get_data(as_text=True)


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("get", "/read/related", {"Accept": "application/json", "Sec-Fetch-Dest": "empty"}),
        ("get", "/media", {"Accept": "image/*,*/*", "Sec-Fetch-Dest": "image"}),
        ("post", "/recent/clear", PAGE_HEADERS),
    ],
)
def test_non_page_request_without_cookie_is_refused(app, method, path, headers):
    response = getattr(app.test_client(), method)(path, headers=headers)

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"


def test_tampered_cookie_gets_challenge_again(app):
    client = app.test_client()
    token = _token_from(client.get("/recent", headers=PAGE_HEADERS))

    client.set_cookie(visitor_check.COOKIE, token[:-2] + "xx")
    assert CHALLENGE_MARKER in client.get("/recent", headers=PAGE_HEADERS).get_data(as_text=True)

def test_exemptions(app):
    client = app.test_client()

    assert client.get("/healthz").status_code == 200
    assert client.get("/favicon.ico").status_code == 200
    script = client.get("/static/javascript/visitor_check.js")
    assert script.status_code == 200
    assert "location.reload()" in script.text


@pytest.mark.parametrize("value", [False, "off", "0"])
def test_disabled_check_lets_requests_through(app, value):
    app.config["VISITOR_CHECK"] = value

    response = app.test_client().get("/recent", headers=PAGE_HEADERS)

    assert response.status_code == 200
    assert CHALLENGE_MARKER not in response.get_data(as_text=True)


@pytest.mark.parametrize(("accept", "status", "challenge"), [
    ("*/*", 200, True),
    ("*/*;q=0.1", 200, True),
    ("text/html", 200, True),
    ("application/xhtml+xml", 200, True),
    ("application/json", 403, False),
    ("text/html;q=0", 403, False),
    ("application/xhtml+xml;q=0", 403, False),
    ("*/*;q=0", 403, False),
])
def test_accept_without_fetch_metadata(app, accept, status, challenge):
    response = app.test_client().get("/recent", headers={"Accept": accept})
    assert response.status_code == status
    assert (CHALLENGE_MARKER in response.text) is challenge


def assert_json_rejection(response):
    assert response.status_code == 403
    assert response.mimetype == "application/json"
    assert response.json == {
        "ok": False, "code": "visitor_check_required", "sent": False,
        "message": visitor_check.MESSAGE, "error": visitor_check.MESSAGE,
    }
    assert "새로고침" in response.json["message"]
    assert "전송하지 않았" in response.json["message"]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("headers", [
    {"Accept": "text/html", visitor_check.FETCH_HEADER: "1"},
    {"Accept": "text/html", "Sec-Fetch-Dest": "empty"},
    {"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"},
    {"Accept": "application/json"},
])
def test_js_page_fetch_is_json_not_challenge(app, headers):
    assert_json_rejection(app.test_client().get("/recent", headers=headers))


JSON_PATHS = [
    "/board/updates", "/board/times", "/read/related", "/read/vote/options",
    "/embed/youtube-size", "/embed/link-preview",
    "/poker/free/list", "/poker/free/123/comments",
]
MEDIA_PATHS = ["/media", "/movie", "/embed/link-preview-image", "/write/captcha", "/poker/media"]
POST_PATHS = ["/write", "/read/vote", "/read/comment", "/read/delete",
              "/read/comment/delete", "/write/modify/load"]


@pytest.mark.parametrize("path", JSON_PATHS)
def test_known_json_endpoint_never_gets_challenge(app, path):
    assert_json_rejection(app.test_client().get(path, headers=PAGE_HEADERS))


@pytest.mark.parametrize("path", POST_PATHS)
def test_json_post_endpoint_rejects_even_form_without_accept(app, path):
    assert_json_rejection(app.test_client().post(path, data={"password": "keep"}))


@pytest.mark.parametrize("path", MEDIA_PATHS)
def test_media_is_non_html_even_with_html_accept(app, path):
    response = app.test_client().get(path, headers=PAGE_HEADERS)
    assert response.status_code == 403
    assert response.mimetype == "text/plain"
    assert "<script" not in response.text


@pytest.mark.parametrize("path", ["/recent/clear", "/recent/remove", "/write/unlock", "/write/lock"])
def test_form_post_is_terminal_with_safe_get_link(app, path):
    response = app.test_client().post(path, headers=PAGE_HEADERS)
    assert response.status_code == 403
    assert response.mimetype == "text/html"
    assert '<a href="/recent">' in response.text
    assert "전송하지 않았" in response.text
    assert "<script" not in response.text
    assert "data-token" not in response.text
    assert "Location" not in response.headers
    assert not response.headers.getlist("Set-Cookie")


@pytest.mark.parametrize(("path", "headers"), [
    ("/recent", {"Accept": "*/*"}),
    ("/read/related", PAGE_HEADERS),
    ("/media", PAGE_HEADERS),
    ("/recent", {"Accept": "text/html;q=0"}),
    ("/recent", {**PAGE_HEADERS, visitor_check.FETCH_HEADER: "1"}),
])
def test_head_parity_is_bodyless_and_never_sets_cookie(app, monkeypatch, path, headers):
    def forbidden(**kwargs):
        pytest.fail("protected view called")

    with app.test_request_context(path):
        from flask import request
        endpoint = request.endpoint
    monkeypatch.setitem(app.view_functions, endpoint, forbidden)
    client = app.test_client()
    get = client.get(path, headers=headers)
    head = client.head(path, headers=headers)
    assert head.status_code == get.status_code
    assert head.data == b""
    for header in ("Content-Type", "Cache-Control", "X-Robots-Tag"):
        assert head.headers[header] == get.headers[header]
    assert not head.headers.getlist("Set-Cookie")
    assert not get.headers.getlist("Set-Cookie")


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/board"), ("GET", "/read"), ("GET", "/poker/free/123"),
    *[("GET", path) for path in JSON_PATHS + MEDIA_PATHS],
    *[("POST", path) for path in POST_PATHS],
    ("POST", "/recent/clear"),
])
def test_rejected_requests_never_dispatch_view_or_upstream(app, monkeypatch, method, path):
    from app import routes, poker_routes

    def forbidden(*args, **kwargs):
        pytest.fail("protected view/upstream called")

    for owner, names in [
        (routes, ["run_async", "build_media_response", "build_movie_response"]),
        (routes.dc_write, ["perform"]),
        (poker_routes.reader, ["board", "post", "comment_page"]),
        (poker_routes, ["build_image_response"]),
    ]:
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    with app.test_request_context(path, method=method):
        from flask import request
        endpoint = request.endpoint
    assert endpoint
    monkeypatch.setitem(app.view_functions, endpoint, forbidden)
    response = app.test_client().open(path, method=method, headers=PAGE_HEADERS)
    assert response.status_code in (200, 403)


@pytest.mark.parametrize(("method", "path"), [
    *[("GET", path) for path in JSON_PATHS + MEDIA_PATHS],
    *[("POST", path) for path in POST_PATHS],
    ("POST", "/recent/clear"), ("HEAD", "/media"),
])
def test_valid_cookie_allows_non_pages_to_dispatch(app, monkeypatch, method, path):
    from app import routes
    monkeypatch.setattr(routes.dc_write, "configured", lambda: True)
    monkeypatch.setattr(routes.dc_write, "enabled", lambda: True)
    calls = []

    def protected(**kwargs):
        calls.append(kwargs)
        return "protected", 202

    with app.test_request_context(path, method=method):
        from flask import request
        endpoint = request.endpoint
        token = visitor_check._signer().dumps("v1")
    monkeypatch.setitem(app.view_functions, endpoint, protected)
    client = app.test_client()
    client.set_cookie(visitor_check.COOKIE, token)
    response = client.open(path, method=method, headers={"Origin": "http://localhost"})
    assert response.status_code == 202
    assert len(calls) == 1
    assert response.data == (b"" if method == "HEAD" else b"protected")


@pytest.mark.parametrize("age", [visitor_check.COOKIE_AGE + 1, -60])
def test_real_timestamp_expiry_and_future_issue_are_rejected(app, monkeypatch, age):
    now = 2_000_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now - age)
    with app.app_context():
        token = visitor_check._signer().dumps("v1")
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now)
    client = app.test_client()
    client.set_cookie(visitor_check.COOKIE, token)
    assert_json_rejection(client.get("/read/related"))


def test_secret_rotation_rejects_old_token(app):
    with app.app_context():
        token = visitor_check._signer().dumps("v1")
    app.secret_key = "rotated-secret"
    client = app.test_client()
    client.set_cookie(visitor_check.COOKIE, token)
    assert_json_rejection(client.get("/read/related"))


def test_https_data_attributes_and_external_script(app):
    response = app.test_client().get("/recent", base_url="https://mirror.test", headers=PAGE_HEADERS)
    assert 'data-cookie="mirror_visitor"' in response.text
    assert f'data-max-age="{visitor_check.COOKIE_AGE}"' in response.text
    assert 'data-secure="1"' in response.text
    assert "/static/javascript/visitor_check.js" in response.text
    assert "<noscript>" in response.text
    assert "<script>" not in response.text
