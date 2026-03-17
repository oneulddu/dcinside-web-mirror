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
