from urllib.parse import parse_qs, urlparse
from pathlib import Path

from bs4 import BeautifulSoup

from app import create_app
from app import routes


class DummyUpstream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=1):
        yield from self.chunks

    def close(self):
        self.closed = True


def test_buffer_limited_media_rejects_unknown_length_over_limit(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 5)
    upstream = DummyUpstream([b"123", b"456"])

    body, total = routes._buffer_limited_media(upstream)

    assert body is None
    assert total == 6
    assert upstream.closed is True


def test_buffer_limited_media_keeps_complete_body_when_within_limit(monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_MAX_BYTES", 10)
    upstream = DummyUpstream([b"123", b"456"])

    body, total = routes._buffer_limited_media(upstream)

    assert total == 6
    assert body.read() == b"123456"
    assert upstream.closed is True
    body.close()


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


def test_related_loader_keeps_empty_results_retryable():
    script = Path(routes.BASE_DIR, "app/static/javascript/read_related_loader.js").read_text()

    assert "cached.items.length === 0" in script
    assert "if (items.length > 0)" in script
    assert 'setButtonState(button, "idle");' in script
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
