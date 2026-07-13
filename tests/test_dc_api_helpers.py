from datetime import datetime
from urllib.parse import parse_qs, urlparse

import lxml.html
import pytest

from app.services.dc import parsers
from app.services.dc.api import API, GET_HEADERS, MOBILE_USER_AGENT, XML_HTTP_REQ_HEADERS, to_int
from app.services.dc.models import Document


def test_to_int_extracts_numbers_and_falls_back_safely():
    assert to_int("조회 1,234") == 1234
    assert to_int("추천 -5") == -5
    assert to_int(None, default=7) == 7
    assert to_int("숫자 없음", default=3) == 3


def test_mobile_request_headers_use_ios_user_agent():
    assert "iPhone" in MOBILE_USER_AGENT
    assert GET_HEADERS["User-Agent"] == MOBILE_USER_AGENT
    assert XML_HTTP_REQ_HEADERS["User-Agent"] == MOBILE_USER_AGENT


def test_search_list_urls_include_platform_specific_search_position():
    api = API.__new__(API)
    positioned_urls = api._API__build_list_urls(
        "test", 2, search_type="subject", search_keyword="검색어", search_pos=-20816199
    )
    mobile_query = parse_qs(urlparse(positioned_urls[0]).query)
    pc_url = next(url for url in positioned_urls if url.startswith("https://gall.dcinside.com/"))
    pc_query = parse_qs(urlparse(pc_url).query)

    assert mobile_query["s_pos"] == ["-20816199"]
    assert "search_pos" not in mobile_query
    assert pc_query["search_pos"] == ["-20816199"]
    assert "s_pos" not in pc_query

    unpositioned_urls = api._API__build_list_urls(
        "test", 2, search_type="subject", search_keyword="검색어"
    )
    assert all("s_pos" not in parse_qs(urlparse(url).query) for url in unpositioned_urls)
    assert all("search_pos" not in parse_qs(urlparse(url).query) for url in unpositioned_urls)


def test_search_view_urls_include_platform_specific_search_position():
    api = API.__new__(API)
    positioned_urls = api._API__build_view_urls(
        "test", "123", search_type="subject", search_keyword="검색어", search_pos=-20816199
    )
    mobile_query = parse_qs(urlparse(positioned_urls[0]).query)
    pc_url = next(url for url in positioned_urls if url.startswith("https://gall.dcinside.com/"))
    pc_query = parse_qs(urlparse(pc_url).query)

    assert mobile_query["s_pos"] == ["-20816199"]
    assert "search_pos" not in mobile_query
    assert pc_query["search_pos"] == ["-20816199"]
    assert "s_pos" not in pc_query

    unpositioned_urls = api._API__build_view_urls(
        "test", "123", search_type="subject", search_keyword="검색어"
    )
    assert all("s_pos" not in parse_qs(urlparse(url).query) for url in unpositioned_urls)
    assert all("search_pos" not in parse_qs(urlparse(url).query) for url in unpositioned_urls)


@pytest.mark.asyncio
async def test_board_collects_mobile_search_block_navigation(monkeypatch):
    api = API.__new__(API)
    api.last_board_headtexts = []
    parsed = lxml.html.fromstring(
        """
        <html><body>
          <ul class="gall-detail-lst">
            <li><a class="lt" href="https://m.dcinside.com/board/test/123">
              <span class="subjectin">검색 결과</span>
              <ul class="ginfo"><li>일반</li><li>ㅇㅇ</li><li>00:02</li><li>조회 1</li><li>추천 0</li></ul>
            </a></li>
          </ul>
          <div class="paging" id="pagination_div">
            <a href="/board/test?s_type=subject_m&amp;serval=검색어&amp;page=2">2</a>
            <a href="/board/test?s_type=subject_m&amp;serval=검색어&amp;page=10">10</a>
            <a class="next" href="/board/test?s_type=subject_m&amp;serval=검색어&amp;s_pos=-20816199&amp;page=1">다음</a>
          </div>
        </body></html>
        """
    )

    async def fake_fetch(urls, validator=None):
        return parsed, "ok", "https://m.dcinside.com/board/test?page=1"

    monkeypatch.setattr(api, "_API__fetch_parsed_from_urls", fake_fetch)
    search_nav = {}
    rows = [
        row
        async for row in api.board(
            "test",
            num=1,
            max_scan_pages=1,
            search_type="subject_m",
            search_keyword="검색어",
            search_nav_collector=search_nav,
        )
    ]

    assert [row.id for row in rows] == ["123"]
    assert search_nav == {
        "prev_pos": None,
        "next_page": 2,
        "next_pos": -20816199,
        "block_max_page": 10,
    }


def test_search_navigation_parses_prev_without_using_it_as_next_fallback():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="paging">
          <a class="prev" href="/board/test?serval=kw&amp;s_pos=-20826199&amp;page=1">이전</a>
          <a href="/board/test?serval=kw&amp;s_pos=-20816199&amp;page=1">1</a>
          <a class="next" href="/board/test?serval=kw&amp;s_pos=-20806199&amp;page=1">다음</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed, "https://m.dcinside.com/board/test", -20816199
    )

    assert nav == {
        "prev_pos": -20826199,
        "next_page": None,
        "next_pos": -20806199,
        "block_max_page": 1,
    }


def test_search_navigation_ignores_first_block_prev_self_link():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="paging">
          <a class="prev" href="/board/test?serval=kw&amp;page=1">이전</a>
          <a href="/board/test?serval=kw&amp;page=1">1</a>
          <a class="next" href="/board/test?serval=kw&amp;s_pos=-20816199&amp;page=1">다음</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed, "https://m.dcinside.com/board/test", None
    )

    assert nav["prev_pos"] is None
    assert nav["next_pos"] == -20816199


def test_search_navigation_never_uses_prev_link_as_next_fallback():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="paging">
          <a class="prev" href="/board/test?serval=kw&amp;s_pos=-20826199&amp;page=1">이전</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed, "https://m.dcinside.com/board/test", -20816199
    )

    assert nav["prev_pos"] == -20826199
    assert nav["next_pos"] is None


def test_pc_search_navigation_includes_numeric_links_without_search_pos():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="bottom_paging_box iconpaging">
          <a href="/mgallery/board/lists/?id=test&amp;page=2&amp;s_keyword=kw">2</a>
          <a href="/mgallery/board/lists/?id=test&amp;page=10&amp;s_keyword=kw">10</a>
          <a class="search_next" href="/mgallery/board/lists/?id=test&amp;page=1&amp;s_keyword=kw&amp;search_pos=-20">다음</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed,
        "https://gall.dcinside.com/mgallery/board/lists/?id=test&page=1&s_keyword=kw",
        None,
    )

    assert nav == {
        "prev_pos": None,
        "next_page": 2,
        "next_pos": -20,
        "block_max_page": 10,
    }


def test_mobile_search_navigation_uses_regular_next_page_beyond_visible_numbers():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="paging">
          <a href="/board/test?page=4&amp;serval=kw">4</a>
          <a href="/board/test?page=5&amp;serval=kw">5</a>
          <a class="next" href="/board/test?page=6&amp;serval=kw">다음</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed,
        "https://m.dcinside.com/board/test?page=5&serval=kw",
        None,
    )

    assert nav["block_max_page"] == 5
    assert nav["next_page"] == 6
    assert nav["next_pos"] is None


def test_pc_search_navigation_prefers_regular_next_page_before_next_block():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="bottom_paging_box iconpaging">
          <a href="/mgallery/board/lists/?id=test&amp;page=14&amp;s_keyword=kw">14</a>
          <a class="next" href="/mgallery/board/lists/?id=test&amp;page=16&amp;s_keyword=kw">다음</a>
          <a class="search_next" href="/mgallery/board/lists/?id=test&amp;page=1&amp;s_keyword=kw&amp;search_pos=-20">다음 검색</a>
        </div>
        """
    )

    nav = api._API__parse_search_navigation(
        parsed,
        "https://gall.dcinside.com/mgallery/board/lists/?id=test&page=15&s_keyword=kw",
        None,
    )

    assert nav["block_max_page"] == 14
    assert nav["next_page"] == 16
    assert nav["next_pos"] == -20


def test_document_str_does_not_require_comment_count():
    doc = Document(
        id="123",
        board_id="test",
        title="title",
        author="author",
        author_id=None,
        contents="contents",
        images=[],
        html="<p>contents</p>",
        view_count=10,
        voteup_count=2,
        votedown_count=1,
        logined_voteup_count=0,
        time=datetime(2026, 1, 1, 12, 0),
        comments=[],
        subject="subject",
    )

    rendered = str(doc)

    assert "title +2 -1" in rendered
    assert "contents" in rendered


def test_parse_time_rolls_future_month_day_back_to_previous_year(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 1, 12, 0, 0)

    monkeypatch.setattr(parsers, "datetime", FrozenDatetime)
    api = API.__new__(API)

    parsed = api._API__parse_time("12.31 23:59")

    assert parsed == datetime(2025, 12, 31, 23, 59)


def test_parse_time_preserves_two_digit_year(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 7, 10, 12, 0, 0)

    monkeypatch.setattr(parsers, "datetime", FrozenDatetime)
    api = API.__new__(API)

    parsed = api._API__parse_time("25.12.31")

    assert parsed == datetime(2025, 12, 31, 23, 59, 59)


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


def test_document_images_include_video_source_and_poster():
    api = API.__new__(API)
    api.session = object()
    doc_content = lxml.html.fromstring(
        """
        <div class="writing_view_box">
          <video poster="https://dcimg7.dcinside.co.kr/poster.jpg">
            <source src="https://dcimg7.dcinside.co.kr/movie.mp4" type="video/mp4">
          </video>
        </div>
        """
    )

    images = api._API__document_images(doc_content, "test", "123")

    assert [image.src for image in images] == [
        "https://dcimg7.dcinside.co.kr/movie.mp4",
        "https://dcimg7.dcinside.co.kr/poster.jpg",
    ]


def test_document_images_include_nested_source_data_src():
    api = API.__new__(API)
    api.session = object()
    doc_content = lxml.html.fromstring(
        """
        <div class="writing_view_box">
          <video>
            <source data-src="https://dcimg7.dcinside.co.kr/lazy-source.mp4" type="video/mp4">
          </video>
        </div>
        """
    )

    images = api._API__document_images(doc_content, "test", "123")

    assert [image.src for image in images] == ["https://dcimg7.dcinside.co.kr/lazy-source.mp4"]


def test_document_images_preserve_duplicate_media_sources():
    api = API.__new__(API)
    api.session = object()
    doc_content = lxml.html.fromstring(
        """
        <div class="writing_view_box">
          <img src="https://dcimg7.dcinside.co.kr/repeated.jpg">
          <img src="https://dcimg7.dcinside.co.kr/repeated.jpg">
          <video>
            <source src="https://dcimg7.dcinside.co.kr/repeated.mp4" type="video/mp4">
          </video>
          <video>
            <source src="https://dcimg7.dcinside.co.kr/repeated.mp4" type="video/mp4">
          </video>
        </div>
        """
    )

    images = api._API__document_images(doc_content, "test", "123")

    assert [image.src for image in images] == [
        "https://dcimg7.dcinside.co.kr/repeated.jpg",
        "https://dcimg7.dcinside.co.kr/repeated.jpg",
        "https://dcimg7.dcinside.co.kr/repeated.mp4",
        "https://dcimg7.dcinside.co.kr/repeated.mp4",
    ]


def test_pc_media_sources_prefer_change_gif_fallback_image_over_broken_mp4():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="writing_view_box">
          <video data-src="https://dcimg7.dcinside.co.kr/viewimage.php?id=test&amp;no=webp">
            <source src="https://dcimg7.dcinside.co.kr/viewimage.php?id=test&amp;no=mp4"
                    type="video/mp4"
                    onerror="change_gif(this)">
          </video>
        </div>
        """
    )

    sources = api._API__real_document_media_sources(doc_content)

    assert sources == [
        {"type": "image", "src": "https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=webp"}
    ]


def test_pc_media_sources_keep_regular_video_without_change_gif_fallback():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="writing_view_box">
          <video data-src="https://dcimg7.dcinside.co.kr/movie-preview.webp">
            <source src="https://dcimg7.dcinside.co.kr/movie.mp4" type="video/mp4">
          </video>
        </div>
        """
    )

    sources = api._API__real_document_media_sources(doc_content)

    assert sources == [{"type": "video", "src": "https://dcimg7.dcinside.co.kr/movie.mp4"}]


@pytest.mark.asyncio
async def test_repair_placeholder_images_uses_pc_change_gif_fallback_image():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <img src="https://nstatic.dcinside.com/dc/m/img/gallview_loading_ori.gif"
               data-gif="https://nstatic.dcinside.com/dc/m/img/m_webp.png">
        </div>
        """
    )

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        return (
            200,
            {},
            """
            <html>
              <body>
                <div class="writing_view_box">
                  <video data-src="https://dcimg7.dcinside.co.kr/viewimage.php?id=test&amp;no=webp">
                    <source src="https://dcimg7.dcinside.co.kr/viewimage.php?id=test&amp;no=mp4"
                            type="video/mp4"
                            onerror="change_gif(this)">
                  </video>
                </div>
              </body>
            </html>
            """,
        )

    api._API__request_text = fake_request_text

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1206783", kind="minor")

    assert repaired.xpath(".//img/@src") == [
        "https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=webp"
    ]
    assert repaired.xpath(".//video") == []


@pytest.mark.asyncio
async def test_repair_placeholder_images_uses_pc_video_source_when_image_source_missing():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <p>본문</p>
          <img src="https://nstatic.dcinside.com/dc/m/img/dccon_loading_nobg200.png"
               data-gif="https://nstatic.dcinside.com/dc/m/img/m_webp.png"
               data-fileno="1913158">
        </div>
        """
    )

    async def fake_pc_media_sources(board_id, document_id, kind=None):
        return [{"type": "video", "src": "https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=mp4"}]

    api._API__pc_document_media_sources = fake_pc_media_sources

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1201641", kind="minor")

    assert repaired.xpath(".//img") == []
    assert repaired.xpath(".//video/source/@src") == ["https://dcimg7.dcinside.co.kr/viewimage.php?id=test&no=mp4"]
    assert repaired.xpath(".//video/@playsinline") == ["playsinline"]


@pytest.mark.asyncio
async def test_repair_placeholder_images_preserves_pc_media_order():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <p>
            <img src="https://nstatic.dcinside.com/dc/m/img/m_webp.png" data-fileno="1">
            <img src="https://nstatic.dcinside.com/dc/m/img/gallview_loading_ori.gif" data-fileno="2">
          </p>
        </div>
        """
    )

    async def fake_pc_media_sources(board_id, document_id, kind=None):
        return [
            {"type": "video", "src": "https://dcimg7.dcinside.co.kr/movie.mp4"},
            {"type": "image", "src": "https://dcimg7.dcinside.co.kr/photo.jpg"},
        ]

    api._API__pc_document_media_sources = fake_pc_media_sources

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1201641", kind="minor")

    assert repaired.xpath(".//video/source/@src") == ["https://dcimg7.dcinside.co.kr/movie.mp4"]
    assert repaired.xpath(".//img/@src") == ["https://dcimg7.dcinside.co.kr/photo.jpg"]


@pytest.mark.asyncio
async def test_repair_placeholder_images_consumes_existing_mobile_media_before_placeholder():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <p>
            <img src="https://dcimg7.dcinside.co.kr/already.jpg">
            <img src="https://nstatic.dcinside.com/dc/m/img/m_webp.png" data-fileno="1">
          </p>
        </div>
        """
    )

    async def fake_pc_media_sources(board_id, document_id, kind=None):
        return [
            {"type": "image", "src": "https://dcimg7.dcinside.co.kr/already.jpg"},
            {"type": "video", "src": "https://dcimg7.dcinside.co.kr/movie.mp4"},
        ]

    api._API__pc_document_media_sources = fake_pc_media_sources

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1201641", kind="minor")

    assert repaired.xpath(".//img/@src") == ["https://dcimg7.dcinside.co.kr/already.jpg"]
    assert repaired.xpath(".//video/source/@src") == ["https://dcimg7.dcinside.co.kr/movie.mp4"]


@pytest.mark.asyncio
async def test_repair_placeholder_images_consumes_existing_mobile_video_before_placeholder():
    api = API.__new__(API)
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <p>
            <video>
              <source src="https://dcimg7.dcinside.co.kr/already.mp4" type="video/mp4">
            </video>
            <img src="https://nstatic.dcinside.com/dc/m/img/m_webp.png" data-fileno="1">
          </p>
        </div>
        """
    )

    async def fake_pc_media_sources(board_id, document_id, kind=None):
        return [
            {"type": "video", "src": "https://dcimg7.dcinside.co.kr/already.mp4"},
            {"type": "image", "src": "https://dcimg7.dcinside.co.kr/photo.jpg"},
        ]

    api._API__pc_document_media_sources = fake_pc_media_sources

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1201641", kind="minor")

    assert repaired.xpath(".//video/source/@src") == ["https://dcimg7.dcinside.co.kr/already.mp4"]
    assert repaired.xpath(".//img/@src") == ["https://dcimg7.dcinside.co.kr/photo.jpg"]


@pytest.mark.asyncio
async def test_repair_placeholder_images_removes_stale_lazy_attrs_for_image_replacement():
    api = API.__new__(API)
    api.session = object()
    doc_content = lxml.html.fromstring(
        """
        <div class="thum-txtin">
          <img src="https://nstatic.dcinside.com/dc/m/img/gallview_loading_ori.gif"
               data-gif="https://nstatic.dcinside.com/dc/m/img/m_webp.png"
               data-src="https://nstatic.dcinside.com/dc/m/img/m_webp.png"
               data-fileno="1">
        </div>
        """
    )

    async def fake_pc_media_sources(board_id, document_id, kind=None):
        return [{"type": "image", "src": "https://dcimg7.dcinside.co.kr/photo.jpg"}]

    api._API__pc_document_media_sources = fake_pc_media_sources

    repaired = await api._API__repair_placeholder_images_from_pc(doc_content, "idolism", "1201641", kind="minor")
    img = repaired.xpath(".//img")[0]
    images = api._API__document_images(repaired, "idolism", "1201641")

    assert api._API__pick_document_image_src(img) == "https://dcimg7.dcinside.co.kr/photo.jpg"
    assert img.get("data-gif") is None
    assert img.get("data-src") is None
    assert [image.src for image in images] == ["https://dcimg7.dcinside.co.kr/photo.jpg"]


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


class _DummyComment:
    def __init__(self, comment_id):
        self.id = comment_id


@pytest.mark.asyncio
async def test_comments_mobile_success_skips_pc_fallback():
    api = API.__new__(API)
    calls = []

    async def fake_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        calls.append(("mobile", num, start_page, fail_fast))
        yield _DummyComment("m1")

    async def fail_pc(*args, **kwargs):
        raise AssertionError("successful mobile comments should not fall back to pc")
        if False:
            yield None

    api._API__comments_from_mobile = fake_mobile
    api._API__comments_from_pc = fail_pc

    comments = [item.id async for item in api.comments("test", "123", num=5, prefer_mobile=True)]

    assert comments == ["m1"]
    assert calls == [("mobile", 5, 1, True)]


@pytest.mark.asyncio
async def test_comments_mobile_partial_failure_falls_back_to_pc_with_remaining_limit():
    api = API.__new__(API)
    pc_calls = []

    async def partial_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        yield _DummyComment("1")
        raise RuntimeError("mobile page failed")

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        pc_calls.append((num, start_page, kind))
        yield _DummyComment("1")
        yield _DummyComment("2")
        yield _DummyComment("3")
        yield _DummyComment("4")

    api._API__comments_from_mobile = partial_mobile
    api._API__comments_from_pc = fake_pc

    comments = [
        item.id
        async for item in api.comments("test", "123", num=3, start_page=2, kind="minor", prefer_mobile=True)
    ]

    assert comments == ["1", "2", "3"]
    assert pc_calls == [(3, 2, "minor")]


@pytest.mark.asyncio
async def test_comments_pc_duplicates_are_skipped_on_mobile_fallback():
    api = API.__new__(API)

    async def partial_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        yield _DummyComment("1")
        raise RuntimeError("pc page failed")

    async def fake_mobile(board_id, document_id, num=-1, start_page=1):
        assert num == -1
        yield _DummyComment("1")
        yield _DummyComment("2")
        yield _DummyComment("3")

    api._API__comments_from_pc = partial_pc
    api._API__comments_from_mobile = fake_mobile

    comments = [item.id async for item in api.comments("test", "123", num=2, prefer_mobile=False)]

    assert comments == ["1", "2"]


@pytest.mark.asyncio
async def test_comments_pc_fetch_expands_limit_to_cover_mobile_duplicates():
    api = API.__new__(API)
    pc_calls = []

    async def partial_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        yield _DummyComment("1")
        yield _DummyComment("2")
        raise RuntimeError("mobile page failed")

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        pc_calls.append(num)
        yield _DummyComment("1")
        yield _DummyComment("2")
        yield _DummyComment("3")
        yield _DummyComment("4")
        yield _DummyComment("5")

    api._API__comments_from_mobile = partial_mobile
    api._API__comments_from_pc = fake_pc

    comments = [item.id async for item in api.comments("test", "123", num=4, prefer_mobile=True)]

    assert comments == ["1", "2", "3", "4"]
    assert pc_calls == [4]
