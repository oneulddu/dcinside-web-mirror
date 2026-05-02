import base64
import json
import threading
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from bs4 import BeautifulSoup

from app import create_app
from app import routes
from app.services import heung
from app.services import html_sanitizer
from app.services import media_proxy
from app.services import recent


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
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


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


def test_media_route_rejects_non_dcinside_source_before_fetch(monkeypatch):
    def fail_fetch(*args, **kwargs):
        raise AssertionError("invalid media source must be rejected before fetch")

    monkeypatch.setattr(media_proxy, "fetch_media_response", fail_fetch)
    app = create_app()

    response = app.test_client().get("/media?src=https://example.com/test.jpg")

    assert response.status_code == 400


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


def test_sanitize_html_fragment_removes_unsafe_tags_and_attributes():
    cleaned = html_sanitizer.sanitize_html_fragment(
        """
        <div onclick="alert(1)">
          <script>alert(1)</script>
          <a href="javascript:alert(1)" target="_blank">bad</a>
          <a href="https://example.com/path">good</a>
          <img src="https://images.dcinside.com/raw.jpg">
          <img src="/media?src=https%3A%2F%2Fimages.dcinside.com%2Fsafe.jpg" onerror="alert(1)">
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
    assert len(images) == 1
    assert images[0]["src"].startswith("/media?")
    assert "onerror" not in images[0].attrs


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


def test_movie_route_renders_same_origin_video_player(monkeypatch):
    movie_html = """
    <html><body>
      <video poster="https://dcm6.dcinside.co.kr/viewmovie.php?type=jpg&amp;code=poster">
        <source src="https://dcm6.dcinside.co.kr/viewmovie.php?type=mp4&amp;code=movie" type="video/mp4">
      </video>
    </body></html>
    """
    requests_seen = []

    def fake_get(url, headers=None, timeout=None):
        requests_seen.append((url, headers))
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


def test_board_read_links_preserve_source_page_and_recommend_mode(monkeypatch):
    async def fake_async_index(page, board, recommend, kind=None):
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
        ]

    monkeypatch.setattr(routes, "async_index", fake_async_index)
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


def test_board_renders_image_icon_before_image_post_title(monkeypatch):
    async def fake_async_index(page, board, recommend, kind=None):
        return [
            {
                "id": "123",
                "title": "사진 있는 글",
                "has_image": True,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
            {
                "id": "124",
                "title": "텍스트 글",
                "has_image": False,
                "comment_count": 0,
                "subject": None,
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
            },
        ]

    monkeypatch.setattr(routes, "async_index", fake_async_index)
    app = create_app()

    response = app.test_client().get("/board?board=test")
    soup = BeautifulSoup(response.data, "html.parser")
    items = soup.select("a.feed-item")

    assert items[0].select_one(".feed-image-icon") is not None
    assert items[0].select_one(".feed-image-icon")["aria-label"] == "사진 첨부"
    assert items[0].select_one(".feed-image-icon + .feed-title") is not None
    assert items[1].select_one(".feed-image-icon") is None


def test_board_normalizes_page_and_recommend_inputs(monkeypatch):
    async def fake_async_index(page, board, recommend, kind=None):
        assert page == 1
        assert board == "test"
        assert recommend == 0
        return []

    monkeypatch.setattr(routes, "async_index", fake_async_index)
    app = create_app()

    response = app.test_client().get("/board?board=test&recommend=2&page=0")

    assert response.status_code == 200


def test_board_rejects_invalid_board_and_kind(monkeypatch):
    async def fail_async_index(*args, **kwargs):
        raise AssertionError("invalid board input must be rejected before upstream fetch")

    monkeypatch.setattr(routes, "async_index", fail_async_index)
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
    script = Path(routes.BASE_DIR, "app/static/javascript/read_related_loader.js").read_text()

    assert "function appendItems(" in script
    assert "[data-related-loader-status='1'], .empty-row" in script
    assert "getRenderedPostIds(list)" in script
    assert "renderedIds[postId]" in script
    assert "list.appendChild(createItemNode" in script
    assert "clearChildren" not in script
    assert "function getLastRenderedPostId(" in script
    assert 'params.set("after_pid", afterPid)' in script
    assert "function responseHasMore(" in script
    assert "has_more" in script
    assert '"has_next"' in script
    assert "clearLegacySessionCache()" in script
    assert "window.sessionStorage.removeItem(key)" in script
    assert "cachedResult.items.length > 0" not in script
    assert "payload.ok === false" in script
    assert 'setButtonState(button, "idle");' in script
    assert 'setButtonState(button, "refresh");' in script
    assert 'setButtonState(button, "retry");' in script
    assert 'setButtonState(button, "no-more");' in script
    assert "cached.items" not in script
    assert "cached.items.length === 0" not in script
    assert 'params.set("recommend", "1")' in script


def test_theme_toggle_persists_and_updates_accessibility_state():
    template = Path(routes.BASE_DIR, "app/templates/base.html").read_text()
    script = Path(routes.BASE_DIR, "app/static/javascript/read_state.js").read_text()
    style = Path(routes.BASE_DIR, "app/static/css/main.css").read_text()

    assert 'class="theme-toggle"' in template
    assert 'aria-pressed="false"' in template
    assert 'window.localStorage.getItem("mirror_theme_v1") === "light"' in template
    assert 'root.dataset.theme = theme' in template
    assert 'THEME_STORAGE_KEY = "mirror_theme_v1"' in script
    assert "window.localStorage.setItem(THEME_STORAGE_KEY" in script
    assert "document.documentElement.dataset.theme" in script
    assert "body.dataset.theme" in script
    assert "aria-pressed" in script
    assert "aria-label" in script
    assert "mirror-light-theme-overrides" not in script
    assert "html[data-theme='light'] .related-more-wrap" in style
    assert "html[data-theme='light'] .board-head" in style


def test_read_renders_embedded_related_posts_without_extra_related_request(monkeypatch):
    async def fake_async_read(pid, board, kind=None, recommend=0):
        assert recommend == 1
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
    script = Path(routes.BASE_DIR, "app/static/javascript/comment_spam_filter.js").read_text()

    assert "hidden.length >= items.length" not in script
    assert "SHORT_REACTION_REPEAT_THRESHOLD" in script
    assert "getRepeatThreshold" in script
    assert "aria-expanded" in script
    assert 'li.classList.add("comment-spam-hidden")' in script


def _encode_recent_cookie(rows):
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def test_recent_gallery_preserves_recommend_context_from_board(monkeypatch):
    async def fake_async_index(page, board, recommend, kind=None):
        return []

    monkeypatch.setattr(routes, "async_index", fake_async_index)
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
    async def fake_async_index(page, board, recommend, kind=None):
        return []

    monkeypatch.setattr(routes, "async_index", fake_async_index)
    app = create_app()
    client = app.test_client()

    client.get("/board?board=test&recommend=1&page=1&kind=minor")
    client.get("/board?board=test&recommend=0&page=1&kind=minor")
    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    recommends = [parse_qs(urlparse(link["href"]).query)["recommend"][0] for link in soup.select("a.feed-item")]

    assert recommends[:2] == ["0", "1"]


def test_recent_gallery_old_cookie_without_recommend_defaults_to_normal():
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie([{"board": "legacy", "kind": "minor", "visited_at": 1}]),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    query = parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)

    assert query["board"] == ["legacy"]
    assert query["recommend"] == ["0"]
    assert query["kind"] == ["minor"]


def test_recent_gallery_ignores_bad_visited_at_in_cookie():
    app = create_app()
    client = app.test_client()
    client.set_cookie(
        recent.RECENT_COOKIE_NAME,
        _encode_recent_cookie([{"board": "legacy", "kind": "minor", "visited_at": "bad"}]),
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


def test_recent_server_cache_accepts_legacy_list_shape():
    app = create_app()
    key = "visitor-test-key"
    rows = [{"board": "legacy", "kind": None, "recommend": 0, "visited_at": 1}]
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
        recent.RECENT_SERVER_CACHE[key] = rows

    with app.test_request_context(
        "/recent",
        headers={"Cookie": f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={key}"},
    ):
        assert recent.load_recent_entries() == rows

    with recent.RECENT_SERVER_CACHE_LOCK:
        assert isinstance(recent.RECENT_SERVER_CACHE[key], dict)


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
    async def fake_async_index(page, board, recommend, kind=None):
        return []

    monkeypatch.setattr(routes, "async_index", fake_async_index)
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


def test_search_galleries_parses_gallery_search_results(monkeypatch):
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
