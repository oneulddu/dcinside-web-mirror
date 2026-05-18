import pytest

from app.services import core
from app.services.core import _fill_missing_author_code
from app.services.dc_api import API, DocumentIndex


@pytest.mark.asyncio
async def test_fill_missing_author_code_skips_mobile_source_rows():
    class FailingAPI:
        async def document(self, *args, **kwargs):
            raise AssertionError("mobile list rows must not trigger document backfill")

    row = {
        "id": "123",
        "author": "익명",
        "author_code": None,
        "is_mobile_source": True,
    }

    result = await _fill_missing_author_code(FailingAPI(), "test", None, row)

    assert result is row
    assert row["author_code"] is None


@pytest.mark.asyncio
async def test_fill_missing_author_code_does_not_cache_exceptions():
    core._AUTHOR_CODE_CACHE.clear()

    class FailingAPI:
        def __init__(self):
            self.calls = 0

        async def document(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("temporary upstream failure")

    api = FailingAPI()
    row = {
        "id": "123",
        "author": "익명",
        "author_code": None,
        "is_mobile_source": False,
    }

    await _fill_missing_author_code(api, "test", None, dict(row))
    await _fill_missing_author_code(api, "test", None, dict(row))

    assert api.calls == 2
    core._AUTHOR_CODE_CACHE.clear()


@pytest.mark.asyncio
async def test_fill_missing_author_code_does_not_cache_missing_document():
    core._AUTHOR_CODE_CACHE.clear()

    class MissingAPI:
        def __init__(self):
            self.calls = 0

        async def document(self, *args, **kwargs):
            self.calls += 1
            return None

    api = MissingAPI()
    row = {
        "id": "123",
        "author": "익명",
        "author_code": None,
        "is_mobile_source": False,
    }

    await _fill_missing_author_code(api, "test", None, dict(row))
    await _fill_missing_author_code(api, "test", None, dict(row))

    assert api.calls == 2
    core._AUTHOR_CODE_CACHE.clear()


def test_document_index_tracks_mobile_source_flag():
    item = DocumentIndex(
        id="1",
        board_id="test",
        title="title",
        has_image=False,
        author="익명",
        author_id=None,
        time="-",
        view_count=0,
        comment_count=0,
        voteup_count=0,
        document=lambda: None,
        comments=lambda: None,
        subject=None,
        isimage=False,
        isrecommend=False,
        isdcbest=False,
        ishit=False,
        is_mobile_source=True,
    )

    assert item.is_mobile_source is True


@pytest.mark.asyncio
async def test_comments_prefer_mobile_skips_pc_context_fetch():
    api = API.__new__(API)

    async def failing_pc(*args, **kwargs):
        raise AssertionError("mobile documents should not fetch pc comment context first")
        if False:
            yield None

    async def mobile_comments(*args, **kwargs):
        yield "mobile-comment"

    api._API__comments_from_pc = failing_pc
    api._API__comments_from_mobile = mobile_comments

    comments = [item async for item in api.comments("test", "123", prefer_mobile=True)]

    assert comments == ["mobile-comment"]
