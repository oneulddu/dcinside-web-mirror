import pytest

from app.services import core
from app.services.dc_api import DocumentIndex


def _index_item(doc_id, *, author_id=None, is_mobile_source=False):
    return DocumentIndex(
        id=str(doc_id),
        board_id="test",
        title=f"title {doc_id}",
        has_image=False,
        author="익명",
        author_id=author_id,
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
        is_mobile_source=is_mobile_source,
    )


@pytest.fixture(autouse=True)
def clear_core_caches():
    core._BOARD_PAGE_CACHE.clear()
    core._RELATED_CACHE.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    yield
    core._BOARD_PAGE_CACHE.clear()
    core._RELATED_CACHE.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()


@pytest.mark.asyncio
async def test_fetch_board_page_reuses_short_cache():
    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def board(self, **kwargs):
            self.calls += 1
            yield _index_item(123, is_mobile_source=True)

    api = FakeAPI()

    first = await core._fetch_board_page(api, 1, "test", 0, kind="minor", page_size=1)
    second = await core._fetch_board_page(api, 1, "test", 0, kind="minor", page_size=1)

    assert api.calls == 1
    assert first == second
    assert first is not second


@pytest.mark.asyncio
async def test_read_document_skips_comment_request_when_hint_is_zero():
    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []

        async def comments(self):
            raise AssertionError("comment_count=0 hint should skip comment fetch")
            if False:
                yield None

    class FakeAPI:
        async def document(self, **kwargs):
            return FakeDocument()

    data, comments, images = await core._read_document_with_api(
        FakeAPI(),
        "123",
        "test",
        comment_count_hint=0,
    )

    assert data["title"] == "title"
    assert comments == []
    assert images == []


@pytest.mark.asyncio
async def test_related_uses_source_page_without_author_backfill(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"]))
            if kwargs["start_page"] == 2:
                yield _index_item(100)
                yield _index_item(99)

    api = FakeAPI()

    related = await core._related_by_position_with_api(
        api,
        "100",
        "test",
        limit=1,
        source_page=2,
    )

    assert [row["id"] for row in related] == ["99"]
    assert api.calls == [(2, core.RELATED_PAGE_FETCH_SIZE)]
