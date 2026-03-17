from app.services.dc_api import API
import pytest


def test_list_urls_prefer_pc_before_mobile():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=False, kind=None)
    assert urls[0].startswith("https://gall.dcinside.com/")
    assert urls[-1].startswith("https://m.dcinside.com/")


def test_list_urls_keep_recommend_flag_on_pc():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=True, kind="minor")
    assert "recommend=1" in urls[0]
    assert urls[0].startswith("https://gall.dcinside.com/mgallery/")


def test_view_urls_prefer_pc_before_mobile():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor")
    assert urls[0].startswith("https://gall.dcinside.com/")
    assert urls[-1].startswith("https://m.dcinside.com/")


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

    comments = [item async for item in api.comments("aoegame", "30150503", kind="minor")]
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

    comments = [item.id async for item in api.comments("aoegame", "30150503", num=2, kind="minor")]
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

    comments = [item.id async for item in api.comments("aoegame", "30150503", num=-1, kind="minor")]
    assert comments == ["1", "2"]
