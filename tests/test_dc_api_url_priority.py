from app.services.dc_api import API
import pytest


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
    assert any(url.startswith("https://gall.dcinside.com/mgallery/") and "recommend=1" in url for url in urls[1:])


def test_list_urls_keep_recommend_flag_on_mobile_mini_first():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=True, kind="mini")
    assert urls[0].startswith("https://m.dcinside.com/mini/")
    assert "recommend=1" in urls[0]
    assert any(url.startswith("https://gall.dcinside.com/mini/") and "recommend=1" in url for url in urls[1:])


def test_view_urls_prefer_mobile_before_pc():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor")
    assert urls[0].startswith("https://m.dcinside.com/")
    assert any(url.startswith("https://gall.dcinside.com/") for url in urls[1:])


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
          <ul class="gall-detail-lst">
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
async def test_comments_prefer_mobile_falls_back_to_pc_when_mobile_pagination_is_missing():
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
        assert num == 2
        yield DummyComment("1")
        yield DummyComment("2")

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

    assert comments == ["1", "2"]


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
