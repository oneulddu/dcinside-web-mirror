import threading
from urllib.parse import parse_qs, urlparse

from aiohttp import CookieJar
import lxml.html
import pytest
from yarl import URL

from app.services.dc import api as dc_api
from app.services.dc.api import API


def _pc_board_html(doc_id="123", title="pc title"):
    return lxml.html.fromstring(
        f"""
        <html><body><table><tbody>
          <tr class="ub-content us-post" data-no="{doc_id}">
            <td class="gall_tit"><a href="/board/view/?id=test&no={doc_id}">{title}</a></td>
            <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
            <td class="gall_date" title="2026.04.16 12:00:00"></td>
            <td class="gall_count">7</td>
            <td class="gall_recommend">3</td>
          </tr>
        </tbody></table></body></html>
        """
    )


def _clear_board_kind_cache():
    with dc_api._BOARD_KIND_CACHE_LOCK:
        dc_api._BOARD_KIND_CACHE.clear()


def test_board_kind_cache_ttl_default_is_six_hours():
    assert dc_api.BOARD_KIND_CACHE_TTL == 21600


def test_dc_session_connector_defaults():
    assert dc_api.DC_CONN_LIMIT == 20
    assert dc_api.DC_DNS_CACHE_TTL == 60


def test_cache_set_defers_prune_until_cache_exceeds_limit(monkeypatch):
    prune_calls = []

    def fake_prune(cache, now, max_items):
        prune_calls.append((dict(cache), max_items))

    monkeypatch.setattr(dc_api, "cache_prune", fake_prune)
    cache = {}
    lock = threading.Lock()

    dc_api.cache_set(cache, lock, "a", "value", ttl=30, max_items=2)
    dc_api.cache_set(cache, lock, "b", "value", ttl=30, max_items=2)

    assert prune_calls == []

    dc_api.cache_set(cache, lock, "c", "value", ttl=30, max_items=2)

    assert len(prune_calls) == 1
    assert set(prune_calls[0][0]) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_request_text_logs_rate_limit_warning(caplog):
    class FakeResponse:
        status = 429
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def text(self):
            return "Too Many Requests"

    class FakeSession:
        def request(self, *args, **kwargs):
            return FakeResponse()

    api = API.__new__(API)
    api.session = FakeSession()
    caplog.set_level("WARNING", logger="app.services.dc.api")

    with pytest.raises(RuntimeError, match="rate limited: 429"):
        await api._API__request_text("GET", "https://m.dcinside.com/board/test")

    assert any(
        "rate limited: status=429 url=https://m.dcinside.com/board/test" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_request_text_prunes_shared_session_cookies():
    class FakeResponse:
        status = 200
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def text(self):
            return "ok"

    class FakeSession:
        def __init__(self):
            self.cookie_jar = CookieJar(unsafe=True)
            self.cookie_jar.update_cookies(
                {
                    "_ga": "ga-value",
                    "ci_c": "ci-value",
                    "penalty-box": "rate-limit",
                    "tracking": "drop-me",
                },
                URL("https://gall.dcinside.com/"),
            )

        def request(self, *args, **kwargs):
            return FakeResponse()

    api = API.__new__(API)
    api.session = FakeSession()

    status, _, text = await api._API__request_text("GET", "https://gall.dcinside.com/m")
    cookies = api.session.cookie_jar.filter_cookies(URL("https://gall.dcinside.com/"))

    assert status == 200
    assert text == "ok"
    assert set(cookies) == {"_ga", "ci_c"}
    assert cookies["_ga"].value == "ga-value"
    assert cookies["ci_c"].value == "ci-value"


def test_list_urls_prefer_mobile_before_pc():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=False, kind=None)
    assert urls[0].startswith("https://m.dcinside.com/")
    assert any(url.startswith("https://gall.dcinside.com/") for url in urls[1:])


def test_list_urls_keep_recommend_flag_on_mobile_first():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=True, kind="minor")
    assert "recommend=1" in urls[0]
    assert urls[0].startswith("https://m.dcinside.com/")
    assert any(url.startswith("https://gall.dcinside.com/mgallery/") and "exception_mode=recommend" in url for url in urls[1:])
    assert all("recommend=1" not in url for url in urls[1:])


def test_list_urls_keep_recommend_flag_on_mobile_mini_first():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=True, kind="mini")
    assert urls[0].startswith("https://m.dcinside.com/mini/")
    assert "recommend=1" in urls[0]
    assert any(url.startswith("https://gall.dcinside.com/mini/") and "exception_mode=recommend" in url for url in urls[1:])
    assert all("recommend=1" not in url for url in urls[1:])


def test_list_urls_include_mobile_head_id_filter():
    api = API.__new__(API)
    urls = api._API__build_list_urls("thesingularity", 2, head_id="10")

    assert urls[0] == "https://m.dcinside.com/board/thesingularity?page=2&headid=10"
    assert any(url.startswith("https://gall.dcinside.com/") and "search_head=10" in url for url in urls[1:])


def test_redirect_url_preserves_head_id_for_mobile_and_pc_targets():
    api = API.__new__(API)

    mobile_redirect = api._API__normalize_redirect_url(
        "https://m.dcinside.com/board/thesingularity?page=1&headid=10",
        "/board/thesingularity?page=2",
    )
    pc_redirect = api._API__normalize_redirect_url(
        "https://m.dcinside.com/board/thesingularity?page=1&headid=10&recommend=1",
        "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=2",
    )

    assert mobile_redirect == "https://m.dcinside.com/board/thesingularity?page=2&headid=10"
    assert pc_redirect == "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=2&exception_mode=recommend&search_head=10"


def test_redirect_url_does_not_drop_target_recommend_when_preserving_head_id():
    api = API.__new__(API)

    redirect = api._API__normalize_redirect_url(
        "https://m.dcinside.com/board/thesingularity?page=1&headid=10",
        "/board/thesingularity?page=2&recommend=1",
    )

    assert redirect == "https://m.dcinside.com/board/thesingularity?page=2&recommend=1&headid=10"


def test_redirect_url_preserves_pc_exception_mode_recommend():
    api = API.__new__(API)

    redirect = api._API__normalize_redirect_url(
        "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=1&exception_mode=recommend",
        "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=2",
    )

    assert redirect == "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=2&exception_mode=recommend"


def test_search_list_redirect_preserves_page_and_recognizes_path_without_slash():
    api = API.__new__(API)

    redirect = api._API__normalize_redirect_url(
        "https://gall.dcinside.com/mgallery/board/lists/?id=airforce&page=3&s_keyword=공군",
        "https://gall.dcinside.com/board/lists?id=airforce",
    )

    assert parse_qs(urlparse(redirect).query)["page"] == ["3"]
    assert api._API__list_url_pattern(redirect) == "normal"


def test_redirect_url_maps_mobile_search_context_to_pc_target():
    api = API.__new__(API)

    redirect = api._API__normalize_redirect_url(
        "https://m.dcinside.com/board/test?page=2&s_type=subject&serval=hello&s_pos=-20",
        "https://gall.dcinside.com/mgallery/board/lists/?id=test&page=2",
    )

    query = parse_qs(urlparse(redirect).query)
    assert query["s_type"] == ["search_subject"]
    assert query["s_keyword"] == ["hello"]
    assert query["search_pos"] == ["-20"]
    assert "serval" not in query
    assert "s_pos" not in query


def test_redirect_url_maps_pc_search_context_to_mobile_target():
    api = API.__new__(API)

    redirect = api._API__normalize_redirect_url(
        "https://gall.dcinside.com/mgallery/board/lists/?id=test&page=2&s_type=search_memo&s_keyword=hello&search_pos=-20",
        "https://m.dcinside.com/board/test?page=2",
    )

    query = parse_qs(urlparse(redirect).query)
    assert query["s_type"] == ["memo"]
    assert query["serval"] == ["hello"]
    assert query["s_pos"] == ["-20"]
    assert "s_keyword" not in query
    assert "search_pos" not in query


def test_parse_mobile_headtext_tabs():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <div class="mal-sw-wrap">
          <ul class="mal-lst swiper-wrapper">
            <li class="swiper-slide"><a href="javascript:headText_change();">전체</a></li>
            <li class="swiper-slide"><a href="javascript:headText_change(0);">일반</a></li>
            <li class="swiper-slide on"><a href="javascript:headText_change(10);">📪정보</a></li>
          </ul>
        </div>
        """
    )

    assert api._API__parse_mobile_headtext_tabs(parsed) == [
        {"head_id": None, "label": "전체", "active": False},
        {"head_id": "0", "label": "일반", "active": False},
        {"head_id": "10", "label": "📪정보", "active": True},
    ]


@pytest.mark.asyncio
async def test_board_keeps_headtext_tabs_from_first_scanned_page(monkeypatch):
    api = API.__new__(API)
    api.last_board_headtexts = []
    pages = [
        lxml.html.fromstring(
            """
            <html><body>
              <ul class="mal-lst">
                <li class="on"><a href="javascript:headText_change();">전체</a></li>
                <li><a href="javascript:headText_change(10);">첫페이지</a></li>
              </ul>
              <ul class="gall-detail-lst">
                <li><a class="lt" href="https://m.dcinside.com/board/test/200">
                  <span class="subjectin">first</span>
                  <ul class="ginfo"><li>일반</li><li>ㅇㅇ</li><li>00:01</li><li>조회 1</li><li>추천 0</li></ul>
                </a></li>
              </ul>
            </body></html>
            """
        ),
        lxml.html.fromstring(
            """
            <html><body>
              <ul class="mal-lst">
                <li><a href="javascript:headText_change();">전체</a></li>
                <li class="on"><a href="javascript:headText_change(20);">두번째페이지</a></li>
              </ul>
              <ul class="gall-detail-lst">
                <li><a class="lt" href="https://m.dcinside.com/board/test/199">
                  <span class="subjectin">second</span>
                  <ul class="ginfo"><li>일반</li><li>ㅇㅇ</li><li>00:02</li><li>조회 1</li><li>추천 0</li></ul>
                </a></li>
              </ul>
            </body></html>
            """
        ),
    ]

    async def fake_fetch(urls, validator=None):
        parsed = pages.pop(0)
        return parsed, "ok", "https://m.dcinside.com/board/test"

    monkeypatch.setattr(api, "_API__fetch_parsed_from_urls", fake_fetch)

    rows = [row async for row in api.board("test", num=2, max_scan_pages=2)]

    assert [row.id for row in rows] == ["200", "199"]
    assert api.last_board_headtexts == [
        {"head_id": None, "label": "전체", "active": True},
        {"head_id": "10", "label": "첫페이지", "active": False},
    ]


@pytest.mark.asyncio
async def test_board_tries_cached_successful_list_url_pattern_first(monkeypatch):
    _clear_board_kind_cache()
    api = API.__new__(API)
    api.last_board_headtexts = []
    seen_url_batches = []

    async def fake_fetch(urls, validator=None):
        seen_url_batches.append(list(urls))
        used_url = next(url for url in urls if "/mgallery/board/lists/" in url)
        return _pc_board_html(), "ok", used_url

    monkeypatch.setattr(api, "_API__fetch_parsed_from_urls", fake_fetch)

    first_rows = [row async for row in api.board("cachetest", num=1, start_page=1)]
    second_rows = [row async for row in api.board("cachetest", num=1, start_page=1)]

    assert [row.id for row in first_rows] == ["123"]
    assert [row.id for row in second_rows] == ["123"]
    assert seen_url_batches[0][0] == "https://m.dcinside.com/board/cachetest?page=1"
    assert any("/board/lists/" in url for url in seen_url_batches[0][1:])
    assert seen_url_batches[1] == ["https://gall.dcinside.com/mgallery/board/lists/?id=cachetest&page=1"]
    _clear_board_kind_cache()


@pytest.mark.asyncio
async def test_board_invalidates_stale_cached_list_url_pattern(monkeypatch):
    _clear_board_kind_cache()
    api = API.__new__(API)
    api.last_board_headtexts = []
    original_urls = api._API__build_list_urls("staletest", 1)
    cache_key = api._API__board_kind_cache_key("staletest")
    stale_url = next(url for url in original_urls if "/mgallery/board/lists/" in url)
    fallback_url = next(url for url in original_urls if "/board/lists/" in url and "/mgallery/" not in url)
    api._API__cache_list_url_pattern(cache_key, stale_url)
    seen_url_batches = []

    async def fake_fetch(urls, validator=None):
        seen_url_batches.append(list(urls))
        if urls == [stale_url]:
            return None, "", None
        return _pc_board_html(), "ok", fallback_url

    monkeypatch.setattr(api, "_API__fetch_parsed_from_urls", fake_fetch)

    rows = [row async for row in api.board("staletest", num=1, start_page=1)]
    cached_url, cached_pattern = api._API__get_cached_list_url(original_urls, cache_key)

    assert [row.id for row in rows] == ["123"]
    assert seen_url_batches == [[stale_url], original_urls]
    assert cached_url == fallback_url
    assert cached_pattern == "normal"
    _clear_board_kind_cache()


def test_view_urls_prefer_mobile_before_pc():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor")
    assert urls[0].startswith("https://m.dcinside.com/")
    assert any(url.startswith("https://gall.dcinside.com/") for url in urls[1:])


def test_view_urls_keep_recommend_flag_on_mobile_first():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor", recommend=True)
    assert urls[0] == "https://m.dcinside.com/board/aoegame/30389383?recommend=1"
    assert any(url.startswith("https://gall.dcinside.com/mgallery/") and "recommend=1" in url for url in urls[1:])


def test_view_urls_include_head_id_filter_for_initial_related_posts():
    api = API.__new__(API)
    urls = api._API__build_view_urls("thesingularity", "123", kind="minor", recommend=True, head_id="10")

    assert urls[0] == "https://m.dcinside.com/board/thesingularity/123?recommend=1&headid=10"
    assert any(
        url.startswith("https://gall.dcinside.com/mgallery/")
        and "recommend=1" in url
        and "search_head=10" in url
        for url in urls[1:]
    )


def test_parse_mobile_list_item_uses_five_cell_ginfo_offsets():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/122?recommend=1">
              <span class="subject-add">
                <span class="sp-lst sp-lst-txt">텍스트</span>
                <span class="subjectin"><b>일반</b>embedded title</span>
              </span>
              <ul class="ginfo">
                <li>일반</li>
                <li>작성자(3.4)<span class="sp-nick m-nogonick"></span></li>
                <li>04.16 12:00</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
            <a class="rt" href="https://m.dcinside.com/board/test/122#comment_box">
              <span class="ct">5</span>
            </a>
          </div>
          <span class="blockInfo" data-info="3.4"></span>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor", recommend=True)

    assert item is not None
    assert item.id == "122"
    assert item.subject == "일반"
    assert item.author == "작성자(3.4)"
    assert item.view_count == 7
    assert item.voteup_count == 2
    assert item.comment_count == 5
    assert item.isimage is False


def test_parse_mobile_list_item_keeps_four_cell_ginfo_offsets():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/122">
              <span class="subject-add">
                <span class="sp-lst sp-lst-img">이미지</span>
                <span class="subjectin">embedded title</span>
              </span>
              <ul class="ginfo">
                <li>작성자(3.4)<span class="sp-nick m-nogonick"></span></li>
                <li>04.16 12:00</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
            <a class="rt" href="https://m.dcinside.com/board/test/122#comment_box">
              <span class="ct">5</span>
            </a>
          </div>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor")

    assert item is not None
    assert item.subject is None
    assert item.author == "작성자(3.4)"
    assert item.view_count == 7
    assert item.voteup_count == 2
    assert item.comment_count == 5
    assert item.isimage is True
    assert item.time_text == "04.16 12:00"
    assert item.time_is_precise is True
    assert item.author_role == "manager"


def test_parse_mobile_list_item_preserves_manager_role():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/122">
              <span class="subject-add">
                <span class="subjectin">manager title</span>
              </span>
              <ul class="ginfo">
                <li>일반</li>
                <li class="list-nick">매니저<span class="sp-nick m-gonick"></span></li>
                <li>04.16 12:00</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
          </div>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor")

    assert item is not None
    assert item.author == "매니저"
    assert item.author_role == "manager"


def test_parse_pc_board_row_preserves_manager_roles():
    api = API.__new__(API)
    manager_row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="123">
          <td class="gall_tit"><a href="/mgallery/board/view/?id=test&no=123">manager title</a></td>
          <td class="gall_writer" data-nick="매니저" data-uid="manager-id">
            <span class="nickname in">매니저</span>
            <img src="https://nstatic.dcinside.com/dc/w/images/managernik.gif">
          </td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">7</td>
          <td class="gall_recommend">3</td>
        </tr>
        """
    )
    submanager_row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="124">
          <td class="gall_tit"><a href="/mgallery/board/view/?id=test&no=124">sub title</a></td>
          <td class="gall_writer" data-nick="부매니저" data-uid="sub-id">
            <span class="nickname in">부매니저</span>
            <img src="https://nstatic.dcinside.com/dc/w/images/fix_sub_managernik.gif">
          </td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">7</td>
          <td class="gall_recommend">3</td>
        </tr>
        """
    )

    manager = api._API__parse_pc_board_row(manager_row, "test", kind="minor")
    submanager = api._API__parse_pc_board_row(submanager_row, "test", kind="minor")

    assert manager.author_role == "manager"
    assert submanager.author_role == "submanager"


def test_parse_mobile_list_item_marks_date_only_time_as_imprecise():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/122">
              <span class="subject-add">
                <span class="subjectin">date only title</span>
              </span>
              <ul class="ginfo">
                <li>작성자</li>
                <li>04.16</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
          </div>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor")

    assert item is not None
    assert item.time_text == "04.16"
    assert item.time_is_precise is False


def test_parse_mobile_list_item_ignores_text_icon_named_image():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/123">
              <span class="subject-add">
                <span class="sp-lst sp-lst-txt">이미지</span>
                <span class="subjectin">text-only title</span>
              </span>
              <ul class="ginfo">
                <li>작성자</li>
                <li>04.16 12:00</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
          </div>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor")

    assert item is not None
    assert item.title == "text-only title"
    assert item.isimage is False
    assert item.isvideo is False


def test_parse_mobile_list_item_tracks_play_icon_as_video():
    api = API.__new__(API)
    row = lxml.html.fromstring(
        """
        <li>
          <div class="gall-detail-lnktb">
            <a class="lt" href="https://m.dcinside.com/board/test/124">
              <span class="subject-add">
                <span class="sp-lst sp-lst-play">동영상</span>
                <span class="subjectin">video title</span>
              </span>
              <ul class="ginfo">
                <li>작성자</li>
                <li>04.16 12:00</li>
                <li>조회 7</li>
                <li>추천 <span>2</span></li>
              </ul>
            </a>
          </div>
        </li>
        """
    )

    item = api._API__parse_mobile_list_item(row, "test", kind="minor")

    assert item is not None
    assert item.title == "video title"
    assert item.isimage is False
    assert item.isvideo is True
    assert item.has_video is True


def test_parse_pc_board_row_uses_pic_icon_not_generic_icon_img():
    api = API.__new__(API)
    text_row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="123" data-type="icon_txt">
          <td class="gall_tit">
            <em class="icon_img icon_txt"></em>
            <a href="/mgallery/board/view/?id=test&no=123">text title</a>
          </td>
          <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">7</td>
          <td class="gall_recommend">3</td>
        </tr>
        """
    )
    image_row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="124" data-type="icon_pic">
          <td class="gall_tit">
            <em class="icon_img icon_pic"></em>
            <a href="/mgallery/board/view/?id=test&no=124">image title</a>
          </td>
          <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">7</td>
          <td class="gall_recommend">3</td>
        </tr>
        """
    )
    video_row = lxml.html.fromstring(
        """
        <tr class="ub-content us-post" data-no="125" data-type="icon_movie">
          <td class="gall_tit">
            <em class="icon_img icon_movie"></em>
            <a href="/mgallery/board/view/?id=test&no=125">video title</a>
          </td>
          <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
          <td class="gall_date" title="2026.04.16 12:00:00"></td>
          <td class="gall_count">7</td>
          <td class="gall_recommend">3</td>
        </tr>
        """
    )

    text_item = api._API__parse_pc_board_row(text_row, "test", kind="minor")
    image_item = api._API__parse_pc_board_row(image_row, "test", kind="minor")
    video_item = api._API__parse_pc_board_row(video_row, "test", kind="minor")

    assert text_item.isimage is False
    assert text_item.has_image is False
    assert text_item.isvideo is False
    assert image_item.isimage is True
    assert image_item.has_image is True
    assert image_item.isvideo is False
    assert video_item.isimage is False
    assert video_item.has_image is False
    assert video_item.isvideo is True
    assert video_item.has_video is True


@pytest.mark.asyncio
async def test_board_ignores_invalid_document_id_limits_instead_of_crashing():
    api = API.__new__(API)
    parsed = lxml.html.fromstring(
        """
        <html><body>
          <ul class="gall-detail-lst">
            <li>
              <div class="gall-detail-lnktb">
                <a class="lt" href="https://m.dcinside.com/board/test/122">
                  <span class="subject-add">
                    <span class="subjectin">embedded title</span>
                  </span>
                  <ul class="ginfo">
                    <li>작성자</li>
                    <li>04.16 12:00</li>
                    <li>조회 7</li>
                    <li>추천 <span>2</span></li>
                  </ul>
                </a>
              </div>
            </li>
          </ul>
        </body></html>
        """
    )

    async def fake_fetch(*args, **kwargs):
        return parsed, "ready", "https://m.dcinside.com/board/test?page=1"

    api._API__fetch_parsed_from_urls = fake_fetch

    items = [
        item async for item in api.board(
            "test",
            num=1,
            document_id_upper_limit="not-a-number",
            document_id_lower_limit="",
        )
    ]

    assert [item.id for item in items] == ["122"]


@pytest.mark.asyncio
async def test_comments_fallback_to_mobile_when_pc_yields_nothing():
    api = API.__new__(API)

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        if False:
            yield None

    async def fake_mobile(board_id, document_id, num=-1, start_page=1):
        yield "mobile-comment"

    api._API__comments_from_pc = fake_pc
    api._API__comments_from_mobile = fake_mobile

    comments = [item async for item in api.comments("aoegame", "30150503", kind="minor", prefer_mobile=False)]
    assert comments == ["mobile-comment"]


@pytest.mark.asyncio
async def test_comments_preserve_remaining_limit_and_skip_duplicates_on_mobile_fallback():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        yield DummyComment("1")
        raise RuntimeError("transient pc failure")

    async def fake_mobile(board_id, document_id, num=-1, start_page=1):
        assert num == -1
        yield DummyComment("1")
        yield DummyComment("2")
        yield DummyComment("3")

    api._API__comments_from_pc = fake_pc
    api._API__comments_from_mobile = fake_mobile

    comments = [item.id async for item in api.comments("aoegame", "30150503", num=2, kind="minor", prefer_mobile=False)]
    assert comments == ["1", "2"]


@pytest.mark.asyncio
async def test_comments_fallback_to_mobile_after_partial_pc_fetch_with_unlimited_num():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def fake_context(board_id, document_id, kind=None):
        return {
            "referer": "https://gall.dcinside.com/mgallery/board/view/?id=aoegame&no=30150503",
            "e_s_n_o": "token",
            "board_type": "",
            "_GALLTYPE_": "M",
            "secret_article_key": "",
        }

    call_state = {"count": 0}

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        call_state["count"] += 1
        if call_state["count"] == 1:
            body = (
                '{"comments":[{"no":"1","parent":"30150503","user_id":"","name":"a","ip":"",'
                '"reg_date":"02.11 17:42:17","memo":"first","depth":0}],"pagination":"<a>2</a>"}'
            )
            return 200, {}, body
        return 200, {}, ""

    async def fake_mobile(board_id, document_id, num=-1, start_page=1):
        yield DummyComment("2")

    api._API__get_pc_comment_context = fake_context
    api._API__request_text = fake_request_text
    api._API__comments_from_mobile = fake_mobile

    comments = [item.id async for item in api.comments("aoegame", "30150503", num=-1, kind="minor", prefer_mobile=False)]
    assert comments == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_parsed_from_urls_ignores_nested_location_href_inside_real_page():
    api = API.__new__(API)

    html = """
    <!doctype html>
    <html>
      <head><title>board</title></head>
      <body>
        <a href="javascript:if(confirm('login')) location.href='https://m.dcinside.com/auth/login?r_url=';">menu</a>
        <ul class="gall-detail-lst">
          <li><div class="gall-detail-lnktb"><a href="https://m.dcinside.com/board/test/1" class="lt"></a></div></li>
        </ul>
      </body>
    </html>
    """

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        return 200, {}, html

    api._API__request_text = fake_request_text

    parsed, text, used_url = await api._API__fetch_parsed_from_urls(
        ["https://m.dcinside.com/board/test?page=1"]
    )

    assert used_url == "https://m.dcinside.com/board/test?page=1"
    assert "gall-detail-lst" in text
    assert len(parsed.xpath("//ul[contains(@class, 'gall-detail-lst')]/li")) == 1


def _make_fake_request_text(responses):
    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        return 200, {}, responses[url]

    return fake_request_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_html", "expected_text"),
    [
        ("<script>location.href='https://example.com/target';</script>", "ready"),
        ("<script>if (true) { window.location.href='https://example.com/target'; }</script>", "guarded-ready"),
        ("<script>window.top.location.href='https://example.com/target';</script>", "window-top-ready"),
        ("<script>document.location='https://example.com/target';</script>", "document-assignment-ready"),
        ("<script>window.top.location.assign('https://example.com/target');</script>", "assign-ready"),
        ('<meta content="0;url=https://example.com/target" http-equiv="refresh">', "meta-ready"),
    ],
)
async def test_fetch_parsed_from_urls_follows_top_level_redirect_variants(start_html, expected_text):
    api = API.__new__(API)

    responses = {
        "https://example.com/start": f"<html><head>{start_html}</head><body></body></html>",
        "https://example.com/target": f"<html><body><div id='ok'>{expected_text}</div></body></html>",
    }

    api._API__request_text = _make_fake_request_text(responses)

    parsed, _, used_url = await api._API__fetch_parsed_from_urls(["https://example.com/start"])

    assert used_url == "https://example.com/target"
    assert parsed.xpath("string(//*[@id='ok'])") == expected_text


@pytest.mark.asyncio
async def test_fetch_parsed_from_urls_follows_top_level_redirect_after_large_prefix():
    api = API.__new__(API)

    long_prefix = "<!--{}-->".format("x" * 6000)
    responses = {
        "https://example.com/start": (
            f"<html><head>{long_prefix}<meta http-equiv='refresh' content='0;url=https://example.com/target'></head></html>"
        ),
        "https://example.com/target": "<html><body><div id='ok'>late-meta-ready</div></body></html>",
    }

    api._API__request_text = _make_fake_request_text(responses)

    parsed, _, used_url = await api._API__fetch_parsed_from_urls(["https://example.com/start"])

    assert used_url == "https://example.com/target"
    assert parsed.xpath("string(//*[@id='ok'])") == "late-meta-ready"


@pytest.mark.asyncio
async def test_fetch_parsed_from_urls_preserves_recommend_on_top_level_redirect():
    api = API.__new__(API)

    start_url = "https://gall.dcinside.com/mgallery/board/view/?id=test&no=123&recommend=1"
    target_url = "https://gall.dcinside.com/board/test/123?recommend=1"
    responses = {
        start_url: "<html><head><script>location.href='/board/test/123';</script></head></html>",
        target_url: "<html><body><div id='ok'>recommend-ready</div></body></html>",
    }
    requested_urls = []

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        requested_urls.append(url)
        return 200, {}, responses[url]

    api._API__request_text = fake_request_text

    parsed, _, used_url = await api._API__fetch_parsed_from_urls([start_url])

    assert requested_urls == [start_url, target_url]
    assert used_url == target_url
    assert parsed.xpath("string(//*[@id='ok'])") == "recommend-ready"


@pytest.mark.asyncio
async def test_fetch_parsed_from_urls_ignores_nested_body_script_redirect():
    api = API.__new__(API)

    responses = {
        "https://example.com/start": """
        <html>
          <body>
            <div>
              <script>window.location.href='https://example.com/target';</script>
            </div>
            <div id='ok'>real-page</div>
          </body>
        </html>
        """,
    }

    api._API__request_text = _make_fake_request_text(responses)

    parsed, _, used_url = await api._API__fetch_parsed_from_urls(["https://example.com/start"])

    assert used_url == "https://example.com/start"
    assert parsed.xpath("string(//*[@id='ok'])") == "real-page"


@pytest.mark.asyncio
async def test_board_falls_back_to_pc_when_mobile_page_is_not_parseable():
    api = API.__new__(API)
    mobile_url = "https://m.dcinside.com/board/test?page=1"
    pc_url = "https://gall.dcinside.com/board/lists/?id=test&page=1"
    responses = {
        mobile_url: "<html><body>mobile placeholder without list rows</body></html>",
        pc_url: """
        <html><body><table><tbody>
          <tr class="ub-content us-post" data-no="123">
            <td class="gall_tit"><a href="/board/view/?id=test&no=123">pc title</a></td>
            <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
            <td class="gall_date" title="2026.04.16 12:00:00"></td>
            <td class="gall_count">7</td>
            <td class="gall_recommend">3</td>
          </tr>
        </tbody></table></body></html>
        """,
    }

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url in responses
        return 200, {}, responses[url]

    api._API__request_text = fake_request_text

    rows = [item async for item in api.board("test", num=1, start_page=1, kind="normal")]

    assert len(rows) == 1
    assert rows[0].id == "123"
    assert rows[0].title == "pc title"
    assert rows[0].is_mobile_source is False
    assert rows[0].time_text == "2026.04.16 12:00:00"
    assert rows[0].time_is_precise is True


@pytest.mark.asyncio
async def test_board_list_pattern_pins_related_fetch_to_mobile_source():
    api = API.__new__(API)
    seen_batches = []

    async def fake_fetch(urls, validator=None):
        seen_batches.append(list(urls))
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        doc_id = 123 if page == 9 else 124
        return (
            lxml.html.fromstring(
                """
                <html><body>
                  <ul class="gall-detail-lst">
                    <li><a class="lt" href="https://m.dcinside.com/board/test/{doc_id}">
                      <span class="subjectin">mobile title</span>
                      <ul class="ginfo"><li>일반</li><li>ㅇㅇ</li><li>00:01</li><li>조회 1</li><li>추천 0</li></ul>
                    </a></li>
                  </ul>
                </body></html>
                """.format(doc_id=doc_id)
            ),
            "ok",
            urls[0],
        )

    api._API__fetch_parsed_from_urls = fake_fetch

    rows = [
        item async for item in api.board(
            "test",
            num=1,
            start_page=9,
            search_keyword="질문",
            list_pattern="mobile",
        )
    ]

    assert [row.id for row in rows] == ["123"]
    assert len(seen_batches) == 2
    assert all(len(batch) == 1 for batch in seen_batches)
    assert all(batch[0].startswith("https://m.dcinside.com/") for batch in seen_batches)


@pytest.mark.asyncio
async def test_board_recovers_when_explicit_list_pattern_fails():
    api = API.__new__(API)
    seen_batches = []

    async def fake_fetch(urls, validator=None):
        seen_batches.append(list(urls))
        if len(seen_batches) == 1:
            return None, "", None
        mobile_url = next(url for url in urls if url.startswith("https://m.dcinside.com/"))
        return (
            lxml.html.fromstring(
                """
                <html><body><ul class="gall-detail-lst">
                  <li><a class="lt" href="https://m.dcinside.com/board/test/123">
                    <span class="subjectin">recovered</span>
                    <ul class="ginfo"><li>일반</li><li>ㅇㅇ</li><li>00:01</li><li>조회 1</li><li>추천 0</li></ul>
                  </a></li>
                </ul></body></html>
                """
            ),
            "ok",
            mobile_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=1,
            search_keyword="공군",
            list_pattern="person",
            search_nav_collector=nav,
        )
    ]

    assert [row.id for row in rows] == ["123"]
    assert len(seen_batches[0]) == 1
    assert len(seen_batches[1]) > 1
    assert nav["source_pattern"] == "mobile"


@pytest.mark.asyncio
async def test_search_page_fallback_keeps_same_platform_page_size():
    api = API.__new__(API)
    seen_batches = []

    async def fake_fetch(urls, validator=None):
        seen_batches.append(list(urls))
        if len(seen_batches) == 1:
            return None, "", None
        assert all(not url.startswith("https://m.dcinside.com/") for url in urls)
        normal_url = next(
            url for url in urls
            if "/board/lists/" in url and "/mgallery/" not in url
        )
        page = int(parse_qs(urlparse(normal_url).query)["page"][0])
        doc_id = 123 if page == 2 else 124
        return (
            lxml.html.fromstring(
                """
                <html><body><table><tbody>
                  <tr class="ub-content us-post" data-no="{doc_id}">
                    <td class="gall_tit"><a href="/board/view/?id=test&no={doc_id}">recovered</a></td>
                    <td class="gall_writer" data-nick="익명"></td>
                    <td class="gall_date" title="2026.07.13 12:00:00"></td>
                    <td class="gall_count">1</td><td class="gall_recommend">0</td>
                  </tr>
                </tbody></table></body></html>
                """.format(doc_id=doc_id)
            ),
            "ok",
            normal_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=1,
            start_page=2,
            search_keyword="공군",
            list_pattern="person",
            search_nav_collector=nav,
        )
    ]

    assert [row.id for row in rows] == ["123"]
    assert len(seen_batches[0]) == 1
    assert nav["source_pattern"] == "normal"


@pytest.mark.asyncio
async def test_search_page_maps_cross_platform_fallback_by_row_offset():
    api = API.__new__(API)
    seen_pages = []

    async def fake_fetch(urls, validator=None):
        if all(url.startswith("https://m.dcinside.com/") for url in urls):
            return None, "", None
        assert all(url.startswith("https://gall.dcinside.com/") for url in urls)
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        seen_pages.append(page)
        newest = 1000 - ((page - 1) * 20)
        body_rows = "".join(
            """
              <tr class="ub-content us-post" data-no="{doc_id}">
                <td class="gall_tit"><a href="/board/view/?id=test&amp;no={doc_id}">row {doc_id}</a></td>
                <td class="gall_writer" data-nick="익명"></td>
                <td class="gall_date" title="2026.07.13 12:00:00"></td>
                <td class="gall_count">1</td><td class="gall_recommend">0</td>
              </tr>
            """.format(doc_id=doc_id)
            for doc_id in range(newest, newest - 20, -1)
        )
        used_url = urls[0]
        return (
            lxml.html.fromstring(
                "<html><body><table><tbody>{}</tbody></table>"
                "<div class='paging'><a href='{}'>next</a></div>"
                "</body></html>".format(
                    body_rows,
                    api._API__replace_list_page(used_url, page + 1),
                )
            ),
            "ok",
            used_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=2,
            search_keyword="공군",
            search_nav_collector=nav,
        )
    ]

    assert [row.id for row in rows] == [str(doc_id) for doc_id in range(970, 940, -1)]
    assert seen_pages == [2, 3]
    assert nav["next_page"] == 3
    assert nav["source_pattern"] == "mobile"


@pytest.mark.asyncio
async def test_pc_search_page_maps_mobile_fallback_by_row_offset():
    api = API.__new__(API)
    seen_pages = []

    async def fake_fetch(urls, validator=None):
        if all(url.startswith("https://gall.dcinside.com/") for url in urls):
            return None, "", None
        assert all(url.startswith("https://m.dcinside.com/") for url in urls)
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        seen_pages.append(page)
        newest = 1000 - ((page - 1) * 30)
        body_rows = "".join(
            """
              <li><a class="lt" href="https://m.dcinside.com/board/test/{doc_id}">
                <span class="subjectin">row {doc_id}</span>
                <ul class="ginfo"><li>일반</li><li>익명</li><li>00:01</li><li>조회 1</li><li>추천 0</li></ul>
              </a></li>
            """.format(doc_id=doc_id)
            for doc_id in range(newest, newest - 30, -1)
        )
        used_url = urls[0]
        return (
            lxml.html.fromstring(
                "<html><body><ul class='gall-detail-lst'>{}</ul>"
                "<div class='paging'><a href='{}'>next</a></div>"
                "</body></html>".format(
                    body_rows,
                    api._API__replace_list_page(used_url, page + 1),
                )
            ),
            "ok",
            used_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=20,
            start_page=2,
            search_keyword="공군",
            list_pattern="normal",
            search_nav_collector=nav,
        )
    ]

    assert [row.id for row in rows] == [str(doc_id) for doc_id in range(980, 960, -1)]
    assert seen_pages == [1, 2]
    assert nav["next_page"] == 3
    assert nav["source_pattern"] == "normal"


@pytest.mark.asyncio
async def test_cross_platform_fallback_stops_repeated_partial_tail_page():
    api = API.__new__(API)
    seen_pages = []

    async def fake_fetch(urls, validator=None):
        if all(url.startswith("https://m.dcinside.com/") for url in urls):
            return None, "", None
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        seen_pages.append(page)
        doc_ids = (
            (800, 799, 798, 797)
            if page == 15
            else (700, 699, 698, 697)
        )
        body_rows = "".join(
            """
              <tr class="ub-content us-post" data-no="{doc_id}">
                <td class="gall_tit"><a href="/board/view/?id=test&amp;no={doc_id}">row {doc_id}</a></td>
                <td class="gall_writer" data-nick="익명"></td>
                <td class="gall_date" title="2026.07.13 12:00:00"></td>
                <td class="gall_count">1</td><td class="gall_recommend">0</td>
              </tr>
            """.format(doc_id=doc_id)
            for doc_id in doc_ids
        )
        return (
            lxml.html.fromstring(
                "<html><body><table><tbody>{}</tbody></table></body></html>".format(
                    body_rows
                )
            ),
            "ok",
            urls[0],
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=11,
            search_keyword="ㅇㅇ",
            list_pattern="mobile",
            search_nav_collector=nav,
            max_scan_pages=1,
        )
    ]

    assert [row.id for row in rows] == ["700", "699", "698", "697"]
    assert seen_pages == [16, 15, 17]
    assert nav["next_page"] is None
    assert nav["source_pattern"] == "mobile"


@pytest.mark.asyncio
async def test_cross_platform_fallback_rejects_first_out_of_range_page():
    api = API.__new__(API)

    async def fake_fetch(urls, validator=None):
        if all(url.startswith("https://m.dcinside.com/") for url in urls):
            return None, "", None
        used_url = urls[0]
        paging_url = api._API__replace_list_page(used_url, 10)
        return (
            lxml.html.fromstring(
                """
                <html><body><table><tbody>
                  <tr class="ub-content us-post" data-no="700">
                    <td class="gall_tit"><a href="/board/view/?id=test&amp;no=700">repeated</a></td>
                    <td class="gall_writer" data-nick="익명"></td>
                    <td class="gall_date" title="2026.07.13 12:00:00"></td>
                    <td class="gall_count">1</td><td class="gall_recommend">0</td>
                  </tr>
                </tbody></table><div class="paging"><a href="{}">10</a></div></body></html>
                """.format(paging_url)
            ),
            "ok",
            used_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=8,
            search_keyword="ㅇㅇ",
            list_pattern="mobile",
            max_scan_pages=1,
        )
    ]

    assert rows == []


@pytest.mark.asyncio
async def test_search_page_rejects_navless_repeat_of_previous_page():
    api = API.__new__(API)

    async def fake_fetch(urls, validator=None):
        return (
            lxml.html.fromstring(
                """
                <html><body><ul class="gall-detail-lst">
                  <li><a class="lt" href="https://m.dcinside.com/board/test/123">
                    <span class="subjectin">repeated</span>
                    <ul class="ginfo"><li>일반</li><li>익명</li><li>00:01</li><li>조회 1</li><li>추천 0</li></ul>
                  </a></li>
                </ul></body></html>
                """
            ),
            "ok",
            urls[0],
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=2,
            search_keyword="ㅇㅇ",
            list_pattern="mobile",
            max_scan_pages=1,
        )
    ]

    assert rows == []


@pytest.mark.asyncio
async def test_cross_platform_partial_window_preserves_empty_page_next_pos():
    api = API.__new__(API)
    current_pos = -300
    next_pos = -200

    async def fake_fetch(urls, validator=None):
        if all(url.startswith("https://m.dcinside.com/") for url in urls):
            return None, "", None
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        newest = 1020 - ((page - 1) * 20)
        body_rows = ""
        if page < 3:
            body_rows = "".join(
                """
                  <tr class="ub-content us-post" data-no="{doc_id}">
                    <td class="gall_tit"><a href="/board/view/?id=test&amp;no={doc_id}">row</a></td>
                    <td class="gall_writer" data-nick="익명"></td>
                    <td class="gall_date" title="2026.07.13 12:00:00"></td>
                    <td class="gall_count">1</td><td class="gall_recommend">0</td>
                  </tr>
                """.format(doc_id=doc_id)
                for doc_id in range(newest, newest - 20, -1)
            )
        next_url = urls[0].replace(
            "search_pos={}".format(current_pos),
            "search_pos={}".format(next_pos),
        )
        return (
            lxml.html.fromstring(
                "<html><body><table><tbody>{}</tbody></table>"
                "<div class='paging'><a class='next' href='{}'>다음</a></div>"
                "</body></html>".format(body_rows, next_url)
            ),
            "ok",
            urls[0],
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    nav = {}
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=2,
            search_keyword="ㅇㅇ",
            search_pos=current_pos,
            list_pattern="mobile",
            search_nav_collector=nav,
            max_scan_pages=1,
        )
    ]

    assert len(rows) == 10
    assert nav["next_page"] is None
    assert nav["next_pos"] == next_pos


@pytest.mark.asyncio
async def test_cross_platform_fallback_does_not_mix_pc_gallery_patterns():
    api = API.__new__(API)
    seen_batches = []

    async def fake_fetch(urls, validator=None):
        seen_batches.append(list(urls))
        if all(url.startswith("https://m.dcinside.com/") for url in urls):
            return None, "", None
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        if page == 2:
            used_url = next(url for url in urls if "/mgallery/" in url)
        else:
            assert all("/mgallery/" in url for url in urls)
            used_url = urls[0].replace("/mgallery/", "/")
        body_rows = "".join(
            """
              <tr class="ub-content us-post" data-no="{doc_id}">
                <td class="gall_tit"><a href="/board/view/?id=test&amp;no={doc_id}">row</a></td>
                <td class="gall_writer" data-nick="익명"></td>
                <td class="gall_date" title="2026.07.13 12:00:00"></td>
                <td class="gall_count">1</td><td class="gall_recommend">0</td>
              </tr>
            """.format(doc_id=doc_id)
            for doc_id in range(1000 - (page * 20), 980 - (page * 20), -1)
        )
        next_url = api._API__replace_list_page(used_url, page + 1)
        return (
            lxml.html.fromstring(
                "<html><body><table><tbody>{}</tbody></table>"
                "<div class='paging'><a href='{}'>next</a></div>"
                "</body></html>".format(body_rows, next_url)
            ),
            "ok",
            used_url,
        )

    api._API__fetch_parsed_from_urls = fake_fetch
    rows = [
        item async for item in api.board(
            "test",
            num=30,
            start_page=2,
            search_keyword="공군",
            list_pattern="mobile",
            max_scan_pages=1,
        )
    ]

    assert rows == []


@pytest.mark.asyncio
async def test_board_precise_times_fetches_pc_list_only():
    api = API.__new__(API)
    seen_urls = []

    async def fake_fetch(urls, validator=None):
        seen_urls.extend(urls)
        parsed = lxml.html.fromstring(
            """
            <html><body><table><tbody>
              <tr class="ub-content us-post" data-no="123">
                <td class="gall_tit"><a href="/board/view/?id=test&no=123">pc title</a></td>
                <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
                <td class="gall_date" title="2026.04.16 12:00:00"></td>
                <td class="gall_count">7</td>
                <td class="gall_recommend">3</td>
              </tr>
            </tbody></table></body></html>
            """
        )
        return parsed, "ok", "https://gall.dcinside.com/board/lists/?id=test&page=2"

    api._API__fetch_parsed_from_urls = fake_fetch

    times = await api.board_precise_times("test", page=2, kind="normal", head_id="10")

    assert list(times) == ["123"]
    assert str(times["123"]) == "2026-04-16 12:00:00"
    assert seen_urls
    assert all("m.dcinside.com" not in url for url in seen_urls)
    assert all("list_num=30" in url for url in seen_urls)


@pytest.mark.asyncio
async def test_board_precise_times_maps_mobile_search_page_to_pc_pages():
    api = API.__new__(API)
    seen_pages = []

    async def fake_fetch(urls, validator=None):
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        seen_pages.append(page)
        row = ""
        if page == 11:
            row = """
              <tr class="ub-content us-post" data-no="1615902">
                <td class="gall_tit"><a href="/board/view/?id=test&amp;no=1615902">target</a></td>
                <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
                <td class="gall_date" title="2026.06.06 18:23:16"></td>
                <td class="gall_count">7</td>
                <td class="gall_recommend">3</td>
              </tr>
            """
        parsed = lxml.html.fromstring(
            f"<html><body><table><tbody>{row}</tbody></table></body></html>"
        )
        return parsed, "ok", urls[0]

    api._API__fetch_parsed_from_urls = fake_fetch

    times = await api.board_precise_times(
        "test",
        page=8,
        search_type="subject_m",
        search_keyword="질문",
        target_ids=["1615902"],
    )

    assert str(times["1615902"]) == "2026-06-06 18:23:16"
    assert seen_pages == [8, 9, 11]


@pytest.mark.asyncio
async def test_board_precise_times_uses_pc_recommend_list_parameter():
    api = API.__new__(API)
    seen_urls = []

    async def fake_fetch(urls, validator=None):
        seen_urls.extend(urls)
        parsed = lxml.html.fromstring(
            """
            <html><body><table><tbody>
              <tr class="ub-content us-post" data-no="123">
                <td class="gall_tit"><a href="/board/view/?id=test&no=123">pc title</a></td>
                <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
                <td class="gall_date" title="2026.04.16 12:00:00"></td>
                <td class="gall_count">7</td>
                <td class="gall_recommend">3</td>
              </tr>
              <tr class="ub-content us-post" data-no="999">
                <td class="gall_tit"><a href="/board/view/?id=test&no=999">other title</a></td>
                <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
                <td class="gall_date" title="2026.04.16 13:00:00"></td>
                <td class="gall_count">7</td>
                <td class="gall_recommend">3</td>
              </tr>
            </tbody></table></body></html>
            """
        )
        return parsed, "ok", "https://gall.dcinside.com/board/lists/?id=test&page=1"

    api._API__fetch_parsed_from_urls = fake_fetch

    times = await api.board_precise_times("test", page=1, recommend=True, kind="normal", target_ids=["123"])

    assert list(times) == ["123"]
    assert seen_urls
    assert all("exception_mode=recommend" in url for url in seen_urls)
    assert all("recommend=1" not in url for url in seen_urls)


@pytest.mark.asyncio
async def test_board_precise_times_looks_ahead_for_rendered_overflow_row():
    api = API.__new__(API)
    seen_urls = []

    async def fake_fetch(urls, validator=None):
        seen_urls.extend(urls)
        doc_id = "124" if "page=3" in urls[0] else "123"
        parsed = lxml.html.fromstring(
            f"""
            <html><body><table><tbody>
              <tr class="ub-content us-post" data-no="{doc_id}">
                <td class="gall_tit"><a href="/board/view/?id=test&no={doc_id}">pc title</a></td>
                <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
                <td class="gall_date" title="2026.04.16 12:00:00"></td>
                <td class="gall_count">7</td>
                <td class="gall_recommend">3</td>
              </tr>
            </tbody></table></body></html>
            """
        )
        return parsed, "ok", urls[0]

    api._API__fetch_parsed_from_urls = fake_fetch

    times = await api.board_precise_times("test", page=2, kind="normal", target_ids=["123", "124"])

    assert set(times) == {"123", "124"}
    assert any("page=2" in url for url in seen_urls)
    assert any("page=3" in url for url in seen_urls)


@pytest.mark.asyncio
async def test_board_falls_back_to_pc_when_mobile_list_has_only_ads():
    api = API.__new__(API)
    mobile_url = "https://m.dcinside.com/board/test?page=1"
    pc_url = "https://gall.dcinside.com/board/lists/?id=test&page=1"
    responses = {
        mobile_url: """
        <html><body>
          <ul class="gall-detail-lst">
            <li class="ad"><div><a href="https://ad.example.test/">ad</a></div></li>
          </ul>
        </body></html>
        """,
        pc_url: """
        <html><body><table><tbody>
          <tr class="ub-content us-post" data-no="123">
            <td class="gall_tit"><a href="/board/view/?id=test&no=123">pc title</a></td>
            <td class="gall_writer" data-nick="pc author" data-ip="1.2"></td>
            <td class="gall_date" title="2026.04.16 12:00:00"></td>
            <td class="gall_count">7</td>
            <td class="gall_recommend">3</td>
          </tr>
        </tbody></table></body></html>
        """,
    }

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url in responses
        return 200, {}, responses[url]

    api._API__request_text = fake_request_text

    rows = [item async for item in api.board("test", num=1, start_page=1, kind="normal")]

    assert len(rows) == 1
    assert rows[0].id == "123"
    assert rows[0].is_mobile_source is False


@pytest.mark.asyncio
async def test_board_accepts_mobile_mini_list_links():
    api = API.__new__(API)
    mini_url = "https://m.dcinside.com/mini/test?page=1"

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url == mini_url
        return 200, {}, """
        <html><body>
          <ul class="gall-detail-lst">
            <li>
              <div>
                <a class="lt" href="https://m.dcinside.com/mini/test/123">
                  <span><span class="sp-lst-txt"></span><span>mini title</span></span>
                  <span>
                    <span>mini author</span>
                    <span>04.16 12:00</span>
                    <span>조회 7</span>
                    <span>추천 3</span>
                  </span>
                </a>
              </div>
              <span><span>0</span></span>
            </li>
          </ul>
        </body></html>
        """

    api._API__request_text = fake_request_text

    rows = [item async for item in api.board("test", num=1, start_page=1, kind="mini")]

    assert len(rows) == 1
    assert rows[0].id == "123"
    assert rows[0].title == "mini title"
    assert rows[0].is_mobile_source is True


@pytest.mark.asyncio
async def test_document_falls_back_to_pc_when_mobile_page_is_not_parseable():
    api = API.__new__(API)
    mobile_url = "https://m.dcinside.com/board/test/123"
    pc_url = "https://gall.dcinside.com/board/view/?id=test&no=123"
    responses = {
        mobile_url: "<html><body>mobile placeholder without document body</body></html>",
        pc_url: """
        <html><body>
          <div class="gallview_head">
            <span class="title_subject">pc title</span>
            <span class="nickname">pc author</span>
            <span class="gall_date">2026.04.16 12:00:00</span>
          </div>
          <div class="writing_view_box"><p>pc body</p></div>
        </body></html>
        """,
    }

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url in responses
        return 200, {}, responses[url]

    api._API__request_text = fake_request_text

    doc = await api.document("test", "123", kind="normal")

    assert doc is not None
    assert doc.title == "pc title"
    assert "pc body" in doc.contents
    assert doc.is_mobile_source is False


@pytest.mark.asyncio
async def test_document_reuses_embedded_mobile_post_list():
    api = API.__new__(API)
    mobile_url = "https://m.dcinside.com/board/test/123"

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url == mobile_url
        return 200, {}, """
        <html><body>
          <div class="gall-tit-box">
            <span class="tit">mobile title</span>
            <ul class="ginfo2"><li>익명(1.2)</li><li>2026.04.16 12:00</li></ul>
          </div>
          <div class="thum-txtin"><p>mobile body</p></div>
          <div class="all-comment-tit">
            <span class="ct">[1]</span>
          </div>
          <ul class="all-comment-lst">
            <input id="reple_totalCnt" name="reple_totalCnt" type="hidden" value="1">
            <li class="comment" no="10" m_no="1">
              <div class="ginfo-area"><button class="nick">댓글작성자</button></div>
              <p class="txt">embedded comment</p>
              <span class="date">04.16 18:18</span>
            </li>
          </ul>
          <ul class="gall-detail-lst">
            <li>
              <div class="gall-detail-lnktb">
                <a class="lt" href="https://m.dcinside.com/board/test/121">
                  <span class="subject-add">
                    <span class="subjectin">outside list title</span>
                  </span>
                  <ul class="ginfo">
                    <li>무시작성자</li>
                    <li>11:58</li>
                    <li>조회 99</li>
                    <li>추천 <span>9</span></li>
                  </ul>
                </a>
              </div>
            </li>
          </ul>
          <ul id="view_next" class="gall-detail-lst">
            <li>
              <div class="gall-detail-lnktb">
                <a class="lt" href="https://m.dcinside.com/board/test/122">
                  <span class="subject-add">
                    <span class="sp-lst sp-lst-txt">이미지</span>
                    <span class="subjectin">embedded title</span>
                  </span>
                  <ul class="ginfo">
                    <li>작성자(3.4)</li>
                    <li>11:59</li>
                    <li>조회 7</li>
                    <li>추천 <span>2</span></li>
                  </ul>
                </a>
                <a class="rt" href="https://m.dcinside.com/board/test/122#comment_box">
                  <span class="ct">5</span>
                </a>
              </div>
              <span class="blockInfo" data-info="3.4"></span>
            </li>
          </ul>
        </body></html>
        """

    api._API__request_text = fake_request_text

    doc = await api.document("test", "123", kind="normal")

    assert doc is not None
    assert doc.is_mobile_source is True
    assert len(doc.related_posts) == 1
    assert doc.related_posts[0].id == "122"
    assert doc.related_posts[0].title == "embedded title"
    assert doc.related_posts[0].comment_count == 5
    assert doc.embedded_comment_total == 1
    assert len(doc.embedded_comments) == 1
    assert doc.embedded_comments[0].contents == "embedded comment"


@pytest.mark.asyncio
async def test_comments_prefer_mobile_falls_back_to_pc_when_mobile_fails():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def failing_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        raise RuntimeError("mobile failed")
        if False:
            yield None

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        yield DummyComment("pc-comment")

    api._API__comments_from_mobile = failing_mobile
    api._API__comments_from_pc = fake_pc

    comments = [item.id async for item in api.comments("aoegame", "30150503", kind="minor")]

    assert comments == ["pc-comment"]


@pytest.mark.asyncio
async def test_comments_prefer_mobile_falls_back_to_pc_when_mobile_yields_nothing():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def empty_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        if False:
            yield None

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        yield DummyComment("pc-comment")

    api._API__comments_from_mobile = empty_mobile
    api._API__comments_from_pc = fake_pc

    comments = [item.id async for item in api.comments("aoegame", "30150503", kind="minor")]

    assert comments == ["pc-comment"]


@pytest.mark.asyncio
async def test_comments_prefer_mobile_falls_back_to_pc_after_partial_mobile_failure():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def partial_mobile(board_id, document_id, num=-1, start_page=1, fail_fast=False):
        yield DummyComment("1")
        raise RuntimeError("mobile page 2 failed")

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        assert num == 2
        yield DummyComment("1")
        yield DummyComment("2")
        yield DummyComment("3")

    api._API__comments_from_mobile = partial_mobile
    api._API__comments_from_pc = fake_pc

    comments = [
        item.id
        async for item in api.comments(
            "aoegame",
            "30150503",
            num=2,
            kind="minor",
            prefer_mobile=True,
        )
    ]

    assert comments == ["1", "2"]


@pytest.mark.asyncio
async def test_comments_prefer_mobile_falls_back_to_pc_after_mobile_ends_prematurely():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    calls = {"count": 0}

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return 200, {}, """
            <html><head></head><body>
              <li no="1" m_no="0">
                <div><span>mobile author</span></div>
                <p>mobile first</p>
                <span>04.16 12:00:00</span>
              </li>
              <span class="pgnum">1 2</span>
            </body></html>
            """
        return 200, {}, """
        <html><head></head><body>
          <span class="pgnum">1 2</span>
        </body></html>
        """

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        assert num == 3
        yield DummyComment("1")
        yield DummyComment("2")
        yield DummyComment("3")

    api._API__request_text = fake_request_text
    api._API__comments_from_pc = fake_pc

    comments = [
        item.id
        async for item in api.comments(
            "aoegame",
            "30150503",
            num=3,
            kind="minor",
            prefer_mobile=True,
        )
    ]

    assert comments == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_comments_prefer_mobile_stops_without_pc_fallback_when_pagination_is_missing_after_rows():
    api = API.__new__(API)

    class DummyComment:
        def __init__(self, cid):
            self.id = cid

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        return 200, {}, """
        <html><head></head><body>
          <li no="1" m_no="0">
            <div><span>mobile author</span></div>
            <p>mobile first</p>
            <span>04.16 12:00:00</span>
          </li>
        </body></html>
        """

    async def fake_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        raise AssertionError("mobile comments with parsed rows should not require pc fallback")
        if False:
            yield DummyComment("pc")

    api._API__request_text = fake_request_text
    api._API__comments_from_pc = fake_pc

    comments = [
        item.id
        async for item in api.comments(
            "aoegame",
            "30150503",
            num=2,
            kind="minor",
            prefer_mobile=True,
        )
    ]

    assert comments == ["1"]


@pytest.mark.asyncio
async def test_mobile_comment_rows_accept_classless_comment_items():
    api = API.__new__(API)

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        return 200, {}, """
        <html><head></head><body>
          <ul class="all-comment-lst">
            <li no="1" m_no="0">
              <div><span>mobile author</span></div>
              <p>mobile first</p>
              <span>04.16 12:00:00</span>
            </li>
          </ul>
        </body></html>
        """

    async def fail_pc(board_id, document_id, num=-1, start_page=1, kind=None):
        raise AssertionError("classless mobile comments should not require pc fallback")
        if False:
            yield None

    api._API__request_text = fake_request_text
    api._API__comments_from_pc = fail_pc

    comments = [
        item.id
        async for item in api.comments(
            "aoegame",
            "30150503",
            num=1,
            kind="minor",
            prefer_mobile=True,
        )
    ]

    assert comments == ["1"]


def test_parse_mobile_comment_preserves_submanager_role():
    api = API.__new__(API)
    li = lxml.html.fromstring(
        """
        <li class="comment-add" no="10" m_no="1">
          <div class="ginfo-area">
            <button type="button" class="nick">부매니저</button>
            <a href="/gallog/sub-id">
              <span class="sp-nick sub-gonick"></span>
              <span class="blockCommentId" data-info="sub-id"></span>
            </a>
          </div>
          <p class="txt">comment body</p>
          <span class="date">04.16 12:00</span>
        </li>
        """
    )

    comment = api._API__parse_mobile_comment_li(li)

    assert comment.author == "부매니저"
    assert comment.author_role == "submanager"


@pytest.mark.asyncio
async def test_document_parses_comma_formatted_embedded_comment_total():
    api = API.__new__(API)
    mobile_url = "https://m.dcinside.com/board/test/123"

    async def fake_request_text(method, url, headers=None, data=None, cookies=None):
        assert url == mobile_url
        return 200, {}, """
        <html><body>
          <div class="gall-tit-box">
            <span class="tit">mobile title</span>
            <ul class="ginfo2"><li>익명(1.2)</li><li>2026.04.16 12:00</li></ul>
          </div>
          <div class="thum-txtin"><p>mobile body</p></div>
          <div class="all-comment-tit">
            <span class="ct">[1,234]</span>
          </div>
          <ul class="all-comment-lst">
            <li class="comment" no="1" m_no="1">
              <div class="ginfo-area"><button class="nick">댓글작성자</button></div>
              <p class="txt">embedded comment</p>
              <span class="date">04.16 18:18</span>
            </li>
          </ul>
        </body></html>
        """

    api._API__request_text = fake_request_text

    doc = await api.document("test", "123", kind="normal")

    assert doc is not None
    assert doc.embedded_comment_total == 1234
    assert len(doc.embedded_comments) == 1


@pytest.mark.asyncio
async def test_comments_zero_limit_skips_mobile_and_pc_fetches():
    api = API.__new__(API)

    async def fail_mobile(*args, **kwargs):
        raise AssertionError("num=0 should not fetch mobile comments")
        if False:
            yield None

    async def fail_pc(*args, **kwargs):
        raise AssertionError("num=0 should not fetch pc comments")
        if False:
            yield None

    api._API__comments_from_mobile = fail_mobile
    api._API__comments_from_pc = fail_pc

    comments = [item async for item in api.comments("aoegame", "30150503", num=0)]

    assert comments == []
