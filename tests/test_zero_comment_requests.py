"""원본의 명시적인 댓글 0개만 신뢰해 추가 요청을 생략한다."""
import lxml.html
import pytest

from app.services import core
from app.services.dc.api import API

HEADER = """<div class="gall-tit-box"><span class="tit">title</span>
<ul class="ginfo2"><li>익명(1.2)</li><li>2026.09.26 12:00</li></ul></div>
<div class="thum-txtin"><p>body</p></div>"""
COMMENT = """<li class="comment" no="10" m_no="1">
<div class="ginfo-area"><button class="nick">작성자</button></div>
<p class="txt">댓글</p><span class="date">09.26 12:00</span></li>"""


def title_count(value):
    return f'<div class="all-comment-tit"><span class="ct">{value}</span></div>'


def hidden_count(value):
    return f'<input id="reple_totalCnt" value="{value}">'


@pytest.mark.parametrize("markup, expected", [
    (title_count("[0]"), 0),
    (hidden_count("0"), 0),
    (hidden_count("0") + title_count("[0]"), 0),
    (hidden_count("1,234") + title_count("[1,234]"), 1234),
    (title_count("[12]"), 12),
    ("", None),
    (hidden_count(""), None),
    (title_count("[]"), None),
    (title_count("[-1]"), None),
    (title_count("[0/10]"), None),
    (title_count("[1,23]"), None),
    (title_count("불러오는 중"), None),
    (hidden_count("bad") + title_count("[0]"), None),
    (hidden_count("0") + title_count("[1]"), None),
    (hidden_count("0") + hidden_count("1"), None),
])
def test_embedded_count_distinguishes_known_zero_from_unknown(markup, expected):
    api = API.__new__(API)
    parsed = lxml.html.fromstring(f"<html><body>{markup}</body></html>")
    rows, total = api._API__parse_embedded_mobile_comments(parsed)
    assert rows == []
    assert total == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("markup, embedded, fetch", [
    (title_count("[0]"), "", False),
    (hidden_count("0"), "", False),
    (title_count("[0]") + hidden_count("0"), "", False),
    ("", "", True),
    (title_count("[]"), "", True),
    (hidden_count("bad") + title_count("[0]"), "", True),
    (hidden_count("0") + title_count("[1]"), "", True),
    (title_count("[1]"), "", True),
    (title_count("[0]"), COMMENT, True),
    (title_count("[1]"), COMMENT, False),
])
async def test_real_document_pipeline_only_skips_confirmed_complete_comments(markup, embedded, fetch):
    api = API.__new__(API)
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "GET" and url == "https://m.dcinside.com/board/test/123":
            return 200, {}, f"<html><body>{HEADER}{markup}<ul class='all-comment-lst'>{embedded}</ul></body></html>"
        if method == "POST" and url == "https://m.dcinside.com/ajax/response-comment":
            return 200, {}, f"<div><ul class='all-comment-lst'>{COMMENT}</ul></div>"
        raise AssertionError(f"Unexpected upstream request: {method} {url}")

    api._API__request_text = request
    data, comments, images = await core._read_document_with_api(api, "123", "test")
    assert len(calls) == (2 if fetch else 1)
    assert data["_comments_complete"] is True
    assert data["title"] == "title"
    assert "body" in data["html"]
    assert images == []
    assert len(comments) == (1 if fetch or embedded else 0)
