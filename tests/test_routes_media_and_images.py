import base64
import json
import threading
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from bs4 import BeautifulSoup

from app import create_app
from app import routes


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


def test_read_limited_media_body_closes_after_success(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream([b"123", b"456"])

    body, error_status = routes._read_limited_media_body(upstream)

    assert body == b"123456"
    assert error_status is None
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_read_limited_media_body_returns_413_and_closes_when_limit_exceeded(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream([b"123", b"456"])

    body, error_status = routes._read_limited_media_body(upstream)

    assert body is None
    assert error_status == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_rejects_unknown_length_streams_when_limit_exceeded(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg"},
    )
    monkeypatch.setattr(routes, "_fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_buffers_unknown_length_streams_within_limit(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg"},
    )
    monkeypatch.setattr(routes, "_fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 200
    assert response.data == b"123456"
    assert response.content_length == 6
    assert upstream.closed is True


def test_media_route_buffers_upstream_and_sets_verified_length(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
    )
    monkeypatch.setattr(routes, "_fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    with app.test_request_context("/media?src=https://images.dcinside.com/test.jpg"):
        response = routes.media()
        assert response.content_length == 6
        assert response.get_data() == b"123456"

    assert upstream.closed is True


def test_media_route_rejects_mismatched_known_length_when_stream_exceeds_limit(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg", "Content-Length": "5"},
    )
    monkeypatch.setattr(routes, "_fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    response = app.test_client().get("/media?src=https://images.dcinside.com/test.jpg")

    assert response.status_code == 413
    assert upstream.iterated == 2
    assert upstream.closed is True


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
        routes._rewrite_content_images(
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


def test_related_loader_appends_related_results_without_replacing_existing_rows():
    script = Path(routes.BASE_DIR, "app/static/javascript/read_related_loader.js").read_text()

    assert "function appendItems(" in script
    assert "[data-related-loader-status='1'], .empty-row" in script
    assert "getRenderedPostIds(list)" in script
    assert "renderedIds[postId]" in script
    assert "list.appendChild(createItemNode" in script
    assert "clearChildren" not in script
    assert "function isNoMoreResponse(" in script
    assert "items.length === 0" in script
    assert "responseHasNoNextCandidate" in script
    assert "window.sessionStorage.removeItem(key)" in script
    assert "cachedResult.items.length > 0" in script
    assert "payload.ok === false" in script
    assert 'setButtonState(button, "refresh");' in script
    assert 'setButtonState(button, "retry");' in script
    assert 'setButtonState(button, "no-more");' in script
    assert "cached.items" in script
    assert "cached.items.length === 0" not in script
    assert 'params.set("recommend", "1")' in script


def test_theme_toggle_persists_and_updates_accessibility_state():
    template = Path(routes.BASE_DIR, "app/templates/base.html").read_text()
    script = Path(routes.BASE_DIR, "app/static/javascript/read_state.js").read_text()

    assert 'class="theme-toggle"' in template
    assert 'aria-pressed="false"' in template
    assert 'THEME_STORAGE_KEY = "mirror_theme_v1"' in script
    assert "window.localStorage.setItem(THEME_STORAGE_KEY" in script
    assert "document.documentElement.dataset.theme" in script
    assert "body.dataset.theme" in script
    assert "aria-pressed" in script
    assert "aria-label" in script


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
        routes.RECENT_COOKIE_NAME,
        _encode_recent_cookie([{"board": "legacy", "kind": "minor", "visited_at": 1}]),
    )

    response = client.get("/recent")
    soup = BeautifulSoup(response.data, "html.parser")
    query = parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)

    assert query["board"] == ["legacy"]
    assert query["recommend"] == ["0"]
    assert query["kind"] == ["minor"]


def test_recent_server_cache_prunes_expired_fallback():
    app = create_app()
    key = "127.0.0.1|pytest"
    with routes.RECENT_SERVER_CACHE_LOCK:
        routes.RECENT_SERVER_CACHE.clear()
        routes.RECENT_SERVER_CACHE[key] = {
            "entries": [{"board": "expired", "kind": None, "recommend": 0, "visited_at": 1}],
            "expires_at": 0,
            "last_seen": 0,
        }

    with app.test_request_context("/recent", environ_base={"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pytest"}):
        assert routes._load_recent_entries() == []

    assert key not in routes.RECENT_SERVER_CACHE


def test_recent_server_cache_limits_total_keys(monkeypatch):
    monkeypatch.setattr(routes, "RECENT_SERVER_CACHE_TTL", 60)
    monkeypatch.setattr(routes, "RECENT_SERVER_CACHE_MAX_KEYS", 2)
    with routes.RECENT_SERVER_CACHE_LOCK:
        routes.RECENT_SERVER_CACHE.clear()

    routes._set_recent_server_cache("one", [{"board": "one"}])
    routes._set_recent_server_cache("two", [{"board": "two"}])
    routes._set_recent_server_cache("three", [{"board": "three"}])

    assert len(routes.RECENT_SERVER_CACHE) <= 2


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

    monkeypatch.setattr(routes, "HEUNG_CACHE", {"updated_at": 0.0, "items": []})
    monkeypatch.setattr(routes, "HEUNG_CACHE_LOCK", lock)
    monkeypatch.setattr(routes, "HEUNG_REFRESH_LOCK", threading.Lock())
    monkeypatch.setattr(routes, "_read_heung_cache_file", lambda: None)
    monkeypatch.setattr(routes, "_fetch_heung_galleries", fake_fetch)
    monkeypatch.setattr(routes, "_write_heung_cache_file", fake_write)

    items, updated_at = routes._get_heung_galleries()

    assert items == [{"rank": 1, "name": "테스트", "board_id": "test"}]
    assert updated_at > 0
    assert calls == ["fetch", "write"]
