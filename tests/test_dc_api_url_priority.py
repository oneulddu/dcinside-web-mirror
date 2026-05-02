from app.services.dc_api import API
import lxml.html
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


def test_view_urls_keep_recommend_flag_on_mobile_first():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor", recommend=True)
    assert urls[0] == "https://m.dcinside.com/board/aoegame/30389383?recommend=1"
    assert any(url.startswith("https://gall.dcinside.com/mgallery/") and "recommend=1" in url for url in urls[1:])


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
                <li>작성자(3.4)</li>
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
                <li>작성자(3.4)</li>
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

    text_item = api._API__parse_pc_board_row(text_row, "test", kind="minor")
    image_item = api._API__parse_pc_board_row(image_row, "test", kind="minor")

    assert text_item.isimage is False
    assert text_item.has_image is False
    assert image_item.isimage is True
    assert image_item.has_image is True


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


class _FakeResponse:
    def __init__(self, text, status=200, headers=None, url=""):
        self._text = text
        self.status = status
        self.headers = headers or {}
        self.url = url

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, headers=None, data=None, cookies=None):
        self.requests.append(("POST", url, data))
        if not self.responses:
            raise AssertionError(f"unexpected POST: {url}")
        expected_url, response = self.responses.pop(0)
        assert url == expected_url
        return response


def _write_form_html():
    return """
    <html><head>
      <meta name="csrf-token" content="csrf-token">
    </head><body>
      <a class="gall-tit-lnk">테스트갤</a>
      <input id="mobile_key" value="mobile-key">
      <input class="hide-robot" name="robot-check">
    </body></html>
    """


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


def test_password_check_response_rejects_json_and_alert_failures():
    api = API.__new__(API)

    api._API__validate_password_check_response('{"result": true}')

    with pytest.raises(Exception, match="비밀번호"):
        api._API__validate_password_check_response('{"result": false, "msg": "비밀번호가 틀립니다"}')

    with pytest.raises(Exception, match="오류"):
        api._API__validate_password_check_response("<script>alert('오류입니다');</script>")


def test_write_response_extracts_document_id_only_from_clear_signals():
    api = API.__new__(API)

    assert (
        api._API__extract_document_id_from_write_response(
            "<script>location.href='/board/test/123';</script>"
        )
        == "123"
    )
    assert api._API__extract_document_id_from_write_response('{"no":"456"}') == "456"
    assert api._API__extract_document_id_from_write_response("<html>이전 글 no=999</html>") is None


@pytest.mark.asyncio
async def test_modify_document_rejects_2xx_alert_error_page():
    api = API.__new__(API)

    async def fake_access(*args, **kwargs):
        return "con-key"

    api._API__access = fake_access
    api.session = _FakeSession(
        [
            (
                "https://m.dcinside.com/ajax/w_filter",
                _FakeResponse('{"result": true}'),
            ),
            (
                "https://mupload.dcinside.com/write_new.php",
                _FakeResponse("<script>alert('오류입니다'); location.href='/board/test/123';</script>"),
            ),
        ]
    )

    with pytest.raises(Exception, match="오류"):
        await api._API__write_or_modify_document(
            "test",
            title="제목",
            contents="본문",
            name="닉네임",
            password="비밀번호",
            intermediate=_write_form_html(),
            intermediate_referer="https://m.dcinside.com/write/test/modify/123",
            document_id="123",
        )


@pytest.mark.asyncio
async def test_modify_document_requires_clear_success_signal():
    api = API.__new__(API)

    async def fake_access(*args, **kwargs):
        return "con-key"

    api._API__access = fake_access
    api.session = _FakeSession(
        [
            (
                "https://m.dcinside.com/ajax/w_filter",
                _FakeResponse('{"result": true}'),
            ),
            (
                "https://mupload.dcinside.com/write_new.php",
                _FakeResponse("<script>alert('수정되었습니다'); location.href='/board/test/123';</script>"),
            ),
        ]
    )

    document_id = await api._API__write_or_modify_document(
        "test",
        title="제목",
        contents="본문",
        name="닉네임",
        password="비밀번호",
        intermediate=_write_form_html(),
        intermediate_referer="https://m.dcinside.com/write/test/modify/123",
        document_id="123",
    )

    assert document_id == "123"


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
