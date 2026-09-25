import lxml.html
import pytest

from app.services import core
from app.services.dc.api import API


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["mobile", "pc"])
@pytest.mark.parametrize(
    ("count_text", "expected"),
    [("1,234", 1234), (None, None), ("-", None), ("0", 0), ("알 수 없음", None)],
)
async def test_document_view_count_reaches_service_payload(monkeypatch, source, count_text, expected):
    count = "" if count_text is None else f"조회 {count_text}"
    if source == "mobile":
        header = f"""
            <div class="gallview-tit-box">
              <span class="tit">제목</span>
              <ul class="ginfo2"><li>작성자</li><li>{count}</li></ul>
            </div>
        """
        url = "https://m.dcinside.com/board/test/123"
    else:
        header = f"""
            <div class="gallview_head">
              <span class="title_subject">제목</span>
              <span class="nickname">작성자</span>
              <span class="gall_count">{count}</span>
            </div>
        """
        url = "https://gall.dcinside.com/board/view/?id=test&no=123"
    html = f"""<html><body>{header}
        <span id="recomm_btn">7</span>
        <div class="writing_view_box"><p>본문</p></div>
    </body></html>"""
    api = API.__new__(API)

    async def fetch(*args, **kwargs):
        return lxml.html.fromstring(html), html, url

    async def comments(*args, **kwargs):
        for comment in ():
            yield comment

    monkeypatch.setattr(api, "_API__fetch_parsed_from_urls", fetch)
    monkeypatch.setattr(api, "comments", comments)

    doc = await api.document("test", "123")
    assert doc.view_count == expected
    data, _, _ = await core._read_document_with_api(api, "123", "test")

    assert data["view_count"] == expected
    if expected is not None:
        assert type(data["view_count"]) is int
    assert data["voteup_count"] == 7
