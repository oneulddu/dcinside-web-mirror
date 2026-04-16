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


def test_board_source_page_hint_is_only_added_for_normal_lists(monkeypatch):
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
    assert "source_page" not in recommend_query


def test_related_loader_keeps_empty_results_retryable():
    script = Path(routes.BASE_DIR, "app/static/javascript/read_related_loader.js").read_text()

    assert "cached.items.length === 0" in script
    assert "if (items.length > 0)" in script
    assert 'setButtonState(button, "idle");' in script
