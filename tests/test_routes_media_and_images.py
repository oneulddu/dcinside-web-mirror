import base64
import json
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


def test_stream_limited_media_yields_chunks_without_prefetching(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream([b"123", b"456"])

    stream = routes._stream_limited_media(upstream)

    assert upstream.iterated == 0
    assert next(stream) == b"123"
    assert upstream.iterated == 1
    assert upstream.closed is False
    assert list(stream) == [b"456"]
    assert upstream.closed is True


def test_stream_limited_media_stops_and_closes_when_limit_exceeded(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream([b"123", b"456"])

    assert list(routes._stream_limited_media(upstream)) == [b"123"]
    assert upstream.iterated == 2
    assert upstream.closed is True


def test_media_route_streams_upstream_without_buffering_and_sets_known_length(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream(
        [b"123", b"456"],
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
    )
    monkeypatch.setattr(routes, "_fetch_media_response", lambda src, headers, cookies: (upstream, None))
    app = create_app()

    with app.test_request_context("/media?src=https://images.dcinside.com/test.jpg"):
        response = routes.media()
        assert upstream.iterated == 0
        assert response.content_length == 6
        assert list(response.response) == [b"123", b"456"]

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
    assert "getRenderedPostIds(list)" in script
    assert "renderedIds[postId]" in script
    assert "list.appendChild(createItemNode" in script
    assert "clearChildren" not in script
    assert 'setButtonState(button, "no-more");' in script
    assert "return cached.items" in script
    assert "cached.items.length === 0" not in script
    assert 'params.set("recommend", "1")' in script


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


def test_comment_spam_filter_never_hides_every_comment():
    script = Path(routes.BASE_DIR, "app/static/javascript/comment_spam_filter.js").read_text()

    assert "hidden.length >= items.length" in script
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
