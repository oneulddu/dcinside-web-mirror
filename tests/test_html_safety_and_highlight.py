from bs4 import BeautifulSoup, Comment, Doctype
import pytest

from app import create_app
from app.services import highlight
from app.services import html_sanitizer
from app.services.dc_links import dcinside_internal_href


@pytest.mark.parametrize("drop_tag", ["form", "object", "button"])
def test_sanitizer_skips_descendants_detached_by_dropped_parent(drop_tag):
    cleaned = html_sanitizer.sanitize_html_fragment(
        f"<{drop_tag}><div><span>removed</span></div></{drop_tag}><p>kept</p>"
    )

    soup = BeautifulSoup(cleaned, "html.parser")

    assert soup.find(drop_tag) is None
    assert "removed" not in soup.get_text()
    assert soup.p.get_text() == "kept"


def test_prepare_read_html_discards_malformed_href_without_raising():
    app = create_app()

    with app.test_request_context("/read?board=test&pid=123"):
        cleaned = html_sanitizer.prepare_read_html(
            '<p><a href="http://[">broken link</a></p>',
            [],
            "test",
            123,
            None,
        )
        assert dcinside_internal_href("http://[") is None

    soup = BeautifulSoup(cleaned, "html.parser")
    assert soup.a.get_text() == "broken link"
    assert "href" not in soup.a.attrs


def test_prepare_read_html_discards_malformed_iframe_without_raising():
    app = create_app()

    with app.test_request_context("/read?board=test&pid=123"):
        cleaned = html_sanitizer.prepare_read_html(
            '<p>before</p><iframe src="https://["></iframe><p>after</p>',
            [],
            "test",
            123,
            None,
        )

    soup = BeautifulSoup(cleaned, "html.parser")
    assert soup.find("iframe") is None
    assert soup.get_text(" ", strip=True) == "before after"


def test_body_highlight_limits_total_matches_per_document():
    original_text = "a" * 2500
    soup = BeautifulSoup(f"<p>{original_text}</p><div>a</div>", "html.parser")

    highlight.highlight_soup_text(soup, "a")

    assert highlight.HTML_HIGHLIGHT_MAX_MATCHES == 2000
    assert len(soup.find_all("mark", class_="search-highlight")) == 2000
    assert soup.get_text() == original_text + "a"


def test_body_highlight_leaves_comments_and_doctype_untouched():
    soup = BeautifulSoup(
        "<!DOCTYPE SECRET><!-- SECRET hidden --><p>visible SECRET</p>",
        "html.parser",
    )

    highlight.highlight_soup_text(soup, "SECRET")

    comments = soup.find_all(string=lambda value: isinstance(value, Comment))
    doctypes = soup.find_all(string=lambda value: isinstance(value, Doctype))
    assert [str(value) for value in comments] == [" SECRET hidden "]
    assert [str(value) for value in doctypes] == ["SECRET"]
    assert [mark.get_text() for mark in soup.find_all("mark")] == ["SECRET"]
