import pytest

from app.services import core
from app.services.dc_api import Comment, DocumentIndex


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
async def test_fetch_board_page_does_not_cache_empty_results():
    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def board(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                if False:
                    yield None
                return
            yield _index_item(123, is_mobile_source=True)

    api = FakeAPI()

    first = await core._fetch_board_page(api, 1, "test", 0, kind="minor", page_size=1)
    second = await core._fetch_board_page(api, 1, "test", 0, kind="minor", page_size=1)

    assert first == []
    assert [row["id"] for row in second] == ["123"]
    assert api.calls == 2


@pytest.mark.asyncio
async def test_read_document_fetches_comments_without_trusting_zero_hint():
    class FakeComment:
        author = "익명"
        author_id = None
        time = "-"
        contents = "new comment"
        parent_id = None
        dccon = None
        is_reply = False

    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []

        async def comments(self):
            yield FakeComment()

    class FakeAPI:
        async def document(self, **kwargs):
            return FakeDocument()

    data, comments, images = await core._read_document_with_api(
        FakeAPI(),
        "123",
        "test",
    )

    assert data["title"] == "title"
    assert [comment["contents"] for comment in comments] == ["new comment"]
    assert images == []


@pytest.mark.asyncio
async def test_read_document_uses_complete_embedded_comments_without_extra_fetch():
    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []
        related_posts = []
        embedded_comments = [
            Comment(
                id="1",
                parent_id="1",
                author="댓글작성자",
                author_id=None,
                contents="embedded comment",
                dccon=None,
                voice=None,
                time="-",
            )
        ]
        embedded_comment_total = 1

        async def comments(self):
            raise AssertionError("complete embedded comments should skip extra comment fetch")
            if False:
                yield None

    class FakeAPI:
        async def document(self, **kwargs):
            return FakeDocument()

    data, comments, images = await core._read_document_with_api(
        FakeAPI(),
        "123",
        "test",
    )

    assert data["title"] == "title"
    assert [comment["contents"] for comment in comments] == ["embedded comment"]
    assert images == []


@pytest.mark.asyncio
async def test_read_document_fetches_comments_when_embedded_total_is_unknown():
    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []
        related_posts = []
        embedded_comments = [
            Comment(
                id="1",
                parent_id="1",
                author="댓글작성자",
                author_id=None,
                contents="embedded comment",
                dccon=None,
                voice=None,
                time="-",
            )
        ]
        embedded_comment_total = 0

        async def comments(self):
            yield Comment(
                id="1",
                parent_id="1",
                author="댓글작성자",
                author_id=None,
                contents="embedded comment",
                dccon=None,
                voice=None,
                time="-",
            )
            yield Comment(
                id="2",
                parent_id="1",
                author="추가작성자",
                author_id=None,
                contents="api comment",
                dccon=None,
                voice=None,
                time="-",
            )

    class FakeAPI:
        async def document(self, **kwargs):
            return FakeDocument()

    data, comments, images = await core._read_document_with_api(
        FakeAPI(),
        "123",
        "test",
    )

    assert data["title"] == "title"
    assert [comment["contents"] for comment in comments] == ["embedded comment", "api comment"]
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


@pytest.mark.asyncio
async def test_related_uses_recommend_board_pages(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"], kwargs["recommend"]))
            if kwargs["recommend"] == 1 and kwargs["start_page"] == 1:
                yield _index_item(100)
                yield _index_item(99)

    api = FakeAPI()

    related = await core._related_by_position_with_api(
        api,
        "100",
        "test",
        limit=1,
        source_page=1,
        recommend=1,
    )

    assert [row["id"] for row in related] == ["99"]
    assert api.calls == [(1, core.RELATED_PAGE_FETCH_SIZE, 1)]


@pytest.mark.asyncio
async def test_related_recommend_source_page_miss_scans_past_default_probe(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"], kwargs["recommend"]))
            if kwargs["recommend"] == 1 and kwargs["start_page"] == 7:
                yield _index_item(300)
                yield _index_item(200)
            elif kwargs["recommend"] == 1 and 1 <= kwargs["start_page"] <= core.RELATED_PAGE_PROBE_STEPS:
                yield _index_item(300 + kwargs["start_page"])
                yield _index_item(200 + kwargs["start_page"])
            elif kwargs["recommend"] == 1 and kwargs["start_page"] == core.RELATED_PAGE_PROBE_STEPS + 1:
                yield _index_item(100)
                yield _index_item(99)

    api = FakeAPI()

    related = await core._related_by_position_with_api(
        api,
        "100",
        "test",
        limit=1,
        source_page=7,
        recommend=1,
    )

    assert [row["id"] for row in related] == ["99"]
    assert api.calls == [
        (7, core.RELATED_PAGE_FETCH_SIZE, 1),
        *[
            (page, core.RELATED_PAGE_FETCH_SIZE, 1)
            for page in range(1, core.RELATED_PAGE_PROBE_STEPS + 2)
        ],
    ]


@pytest.mark.asyncio
async def test_related_falls_back_to_estimate_when_source_page_hint_misses(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"]))
            if kwargs["start_page"] == 1 and kwargs["num"] == 1:
                yield _index_item(500)
            elif kwargs["start_page"] == 3:
                yield _index_item(100)
                yield _index_item(99)

    api = FakeAPI()

    related = await core._related_by_position_with_api(
        api,
        "100",
        "test",
        limit=1,
        source_page=9,
    )

    assert [row["id"] for row in related] == ["99"]
    assert api.calls == [
        (9, core.RELATED_PAGE_FETCH_SIZE),
        (1, 1),
        (3, core.RELATED_PAGE_FETCH_SIZE),
    ]


@pytest.mark.asyncio
async def test_related_does_not_cache_empty_results(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def board(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield _index_item(100)
            elif self.calls == 2:
                yield _index_item(100)
            else:
                yield _index_item(100)
                yield _index_item(99)

    api = FakeAPI()

    first = await core._related_by_position_with_api(api, "100", "test", limit=1, source_page=1)
    core._BOARD_PAGE_CACHE.clear()
    second = await core._related_by_position_with_api(api, "100", "test", limit=1, source_page=1)

    assert first == []
    assert [row["id"] for row in second] == ["99"]
    assert api.calls == 3
