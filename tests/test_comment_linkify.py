from bs4 import BeautifulSoup

from app.services.highlight import linkify_comment_text


def test_linkify_comment_text_turns_http_url_into_anchor():
    rendered = linkify_comment_text("봐봐 https://example.com/path?q=1")
    soup = BeautifulSoup(str(rendered), "html.parser")
    link = soup.find("a")

    assert link["href"] == "https://example.com/path?q=1"
    assert link["target"] == "_blank"
    assert "noopener" in link["rel"]
    assert link.get_text() == "https://example.com/path?q=1"


def test_linkify_comment_text_adds_scheme_for_www_url():
    rendered = linkify_comment_text("www.example.com/test")
    soup = BeautifulSoup(str(rendered), "html.parser")
    link = soup.find("a")

    assert link["href"] == "https://www.example.com/test"
    assert link.get_text() == "www.example.com/test"


def test_linkify_comment_text_keeps_trailing_punctuation_outside_anchor():
    rendered = linkify_comment_text("링크 https://example.com/test).")
    soup = BeautifulSoup(str(rendered), "html.parser")
    link = soup.find("a")

    assert link["href"] == "https://example.com/test"
    assert str(rendered).endswith("</a>).")


def test_linkify_comment_text_escapes_comment_html():
    rendered = str(linkify_comment_text("<script>alert(1)</script> https://example.com"))

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert '<a href="https://example.com"' in rendered
