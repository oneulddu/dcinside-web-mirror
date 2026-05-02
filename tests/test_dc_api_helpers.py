import lxml.html
import pytest

from app.services.dc_api import API, to_int


def test_to_int_extracts_numbers_and_falls_back_safely():
    assert to_int("조회 1,234") == 1234
    assert to_int("추천 -5") == -5
    assert to_int(None, default=7) == 7
    assert to_int("숫자 없음", default=3) == 3


def test_document_content_helpers_keep_lazy_images_and_drop_ads():
    api = API.__new__(API)
    api.session = object()
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <div class="adv-groupin">광고</div>
          <p>본문</p>
          <img src="https://nstatic.dcinside.com/ad.jpg">
          <img data-gif="https://images.dcinside.com/post.gif" src="https://images.dcinside.com/post-thumb.jpg">
          <img src="https://img.iacstatic.co.kr/ad.jpg">
        </div>
        """
    )

    cleaned = api._API__prepare_document_content(doc_content)
    images = api._API__document_images(cleaned, "test", "123")

    assert "광고" not in cleaned.text_content()
    assert api._API__document_contents_text(cleaned) == "본문"
    assert [image.src for image in images] == ["https://images.dcinside.com/post.gif"]
    assert images[0].board_id == "test"
    assert images[0].document_id == "123"


@pytest.mark.asyncio
async def test_replace_poll_iframes_handles_relative_poll_src():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div>
          <iframe src="/poll?vote_id=123"></iframe>
          <iframe src="//evil.com/poll"></iframe>
        </div>
        """
    )
    requests = []

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        requests.append((method, url))
        return (
            200,
            {},
            """
            <html><body>
              <div class="vote-tit-inner">오늘의 투표</div>
              <ul class="vote-date-lst"><li>2026.05.03</li></ul>
              <div class="vote-join">10명 참여</div>
              <ul class="vote-ask-lst"><li><span class="vote-txt">찬성</span></li></ul>
            </body></html>
            """,
        )

    api._API__request_text = fake_request_text

    await api._API__replace_poll_iframes(doc_content)

    assert requests == [("GET", "https://m.dcinside.com/poll?vote_id=123&preview=1")]
    assert doc_content.xpath("string(.//*[contains(@class, 'dc-poll-title')])") == "오늘의 투표"
    assert doc_content.xpath(".//a[contains(@class, 'dc-poll-link')]/@href") == [
        "https://m.dcinside.com/poll?vote_id=123"
    ]
    assert doc_content.xpath(".//iframe/@src") == ["//evil.com/poll"]
