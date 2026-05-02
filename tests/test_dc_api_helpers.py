import lxml.html
import pytest

from app.services.dc_api import API, to_int


def test_to_int_extracts_numbers_and_falls_back_safely():
    assert to_int("조회 1,234") == 1234
    assert to_int("추천 -5") == -5
    assert to_int(None, default=7) == 7
    assert to_int("숫자 없음", default=3) == 3


def test_parse_mobile_list_item_extracts_gallog_author_id():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <a class="lt" href="https://m.dcinside.com/board/test/321">
            <span class="subject-add">
              <span class="sp-lst sp-lst-txt">텍스트</span>
              <span class="subjectin">mobile title</span>
            </span>
            <ul class="ginfo">
              <li><a href="https://gallog.dcinside.com/writer123">작성자</a></li>
              <li>04.16 12:00</li>
              <li>조회 7</li>
              <li>추천 2</li>
            </ul>
          </a>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test")

    assert item.author == "작성자"
    assert item.author_id == "writer123"
    assert item.comment_count == 0


def test_parse_legacy_mobile_board_row_keeps_best_style_metadata():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div>
            <a class="lt" href="https://m.dcinside.com/board/dcbest/654?recommend=1">
              <span class="subject-add">
                <span class="sp-lst sp-lst-recoimg">이미지</span>
                <span class="subjectin"><b>베스트</b>legacy title</span>
              </span>
              <ul class="ginfo">
                <li>작성자</li>
                <li>04.16 12:00</li>
                <li>조회 17</li>
                <li>추천 9</li>
              </ul>
            </a>
            <a class="rt" href="#comment_box"><span class="ct">6</span></a>
          </div>
        </li>
        """
    )

    item = api._API__parse_legacy_mobile_board_row(row, "dcbest", recommend=True)

    assert item.id == "654"
    assert item.title == "베스트legacy title"
    assert item.subject == "베스트"
    assert item.author == "작성자"
    assert item.view_count == 17
    assert item.voteup_count == 9
    assert item.comment_count == 6
    assert item.isimage is True
    assert item.isrecommend is True
    assert item.isdcbest is True


def test_parse_pc_board_row_extracts_author_counts_and_issue_flag():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="777" data-type="icon_issue">
          <td class="gall_tit">
            <em class="icon_img icon_txt"></em>
            <a href="/mgallery/board/view/?id=test&no=777">pc title</a>
            <a class="reply_numbox"><span class="reply_num">[4]</span></a>
          </td>
          <td class="gall_writer" data-nick="" data-uid="writer777">pc author</td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">12</td>
          <td class="gall_recommend">5</td>
        </tr>
        """
    )

    item = api._API__parse_pc_board_row(row, "test", kind="minor")

    assert item.author == "pc author"
    assert item.author_id == "writer777"
    assert item.view_count == 12
    assert item.voteup_count == 5
    assert item.comment_count == 4
    assert item.ishit is True


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
