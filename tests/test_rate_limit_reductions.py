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
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    yield
    core._BOARD_PAGE_CACHE.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()


def test_core_caches_use_separate_locks():
    locks = {
        core._BOARD_PAGE_CACHE_LOCK,
        core._LATEST_ID_CACHE_LOCK,
        core._AUTHOR_CODE_CACHE_LOCK,
    }

    assert len(locks) == 3


def test_author_code_cache_ttl_is_one_hour():
    assert core.AUTHOR_CODE_CACHE_TTL == 3600


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
async def test_fill_missing_author_codes_disabled_skips_document_fetch(monkeypatch):
    monkeypatch.setattr(core, "BOARD_FILL_AUTHOR_CODES", False)

    class FailingAPI:
        def __init__(self):
            self.document_calls = 0

        async def document(self, *args, **kwargs):
            self.document_calls += 1
            raise AssertionError("disabled board author backfill must not fetch documents")

    api = FailingAPI()
    rows = [
        {
            "id": "123",
            "author": "익명",
            "author_code": None,
            "is_mobile_source": False,
        }
    ]

    result = await core._fill_missing_author_codes(api, "test", None, rows)

    assert result is rows
    assert rows[0]["author_code"] is None
    assert api.document_calls == 0


@pytest.mark.asyncio
async def test_fill_missing_author_codes_enabled_uses_cache_only(monkeypatch):
    monkeypatch.setattr(core, "BOARD_FILL_AUTHOR_CODES", True)
    core._cache_author_code("test", None, "123", "익명", "1.2")

    class FailingAPI:
        def __init__(self):
            self.document_calls = 0

        async def document(self, *args, **kwargs):
            self.document_calls += 1
            raise AssertionError("board author backfill should use only cache hits")

    api = FailingAPI()
    rows = [
        {
            "id": "123",
            "author": "익명",
            "author_code": None,
            "is_mobile_source": False,
        },
        {
            "id": "456",
            "author": "익명",
            "author_code": None,
            "is_mobile_source": False,
        },
    ]

    await core._fill_missing_author_codes(api, "test", None, rows)

    assert rows[0]["author_code"] == "1.2"
    assert rows[1]["author_code"] is None
    assert api.document_calls == 0


@pytest.mark.asyncio
async def test_async_index_does_not_fetch_documents_for_missing_author_codes_by_default(monkeypatch):
    monkeypatch.setattr(core, "BOARD_FILL_AUTHOR_CODES", False)

    class FakeAPI:
        instances = []

        def __init__(self):
            self.board_calls = 0
            self.document_calls = 0
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.board_calls += 1
            yield _index_item(123, is_mobile_source=False)

        async def document(self, **kwargs):
            self.document_calls += 1
            raise AssertionError("board rendering must not fetch documents for author codes by default")

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    rows = await core.async_index(1, "test", 0)

    assert [row["id"] for row in rows] == ["123"]
    assert rows[0]["author_code"] is None
    assert FakeAPI.instances[0].board_calls == 1
    assert FakeAPI.instances[0].document_calls == 0


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
async def test_read_document_passes_head_id_to_document_fetch():
    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []
        related_posts = []

        async def comments(self):
            if False:
                yield None

    class FakeAPI:
        def __init__(self):
            self.kwargs = None

        async def document(self, **kwargs):
            self.kwargs = kwargs
            return FakeDocument()

    api = FakeAPI()

    await core._read_document_with_api(
        api,
        "123",
        "test",
        kind="minor",
        head_id="10",
    )

    assert api.kwargs["head_id"] == "10"


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
async def test_related_after_position_uses_source_page_without_author_backfill(monkeypatch):
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
                yield _index_item(98)

    api = FakeAPI()

    related, has_more = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=1,
        source_page=2,
    )

    assert [row["id"] for row in related] == ["99"]
    assert has_more is True
    assert api.calls == [(1, 1), (2, core.RELATED_PAGE_FETCH_SIZE)]


@pytest.mark.asyncio
async def test_related_after_position_recommend_keeps_following_higher_ids(monkeypatch):
    async def fail_author_backfill(*args, **kwargs):
        raise AssertionError("related posts should not fetch documents for author code backfill")

    monkeypatch.setattr(core, "_fill_missing_author_codes", fail_author_backfill)

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"], kwargs["recommend"]))
            yield _index_item(100)
            yield _index_item(105)
            yield _index_item(99)

    api = FakeAPI()

    related, has_more = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=2,
        source_page=1,
        recommend=1,
        tail_pages=0,
    )

    assert [row["id"] for row in related] == ["105", "99"]
    assert has_more is False
    assert api.calls == [(1, core.RELATED_PAGE_FETCH_SIZE, 1)]


@pytest.mark.asyncio
async def test_related_after_position_falls_back_to_estimate_when_source_page_hint_misses(monkeypatch):
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
                yield _index_item(98)

    api = FakeAPI()

    related, has_more = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=1,
        source_page=9,
    )

    assert [row["id"] for row in related] == ["99"]
    assert has_more is True
    assert api.calls == [
        (1, 1),
        (9, core.RELATED_PAGE_FETCH_SIZE),
        (3, core.RELATED_PAGE_FETCH_SIZE),
    ]


@pytest.mark.asyncio
async def test_related_after_position_respects_zero_tail_pages():
    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"], kwargs["recommend"]))
            if kwargs["start_page"] == 1:
                yield _index_item(100)
                yield _index_item(99)
            elif kwargs["start_page"] == 2:
                yield _index_item(98)

    api = FakeAPI()

    related, has_more = await core._related_after_position_with_api(
        api,
        "100",
        "99",
        "test",
        limit=1,
        source_page=1,
        recommend=1,
        tail_pages=0,
    )

    assert related == []
    assert has_more is False
    assert api.calls == [(1, core.RELATED_PAGE_FETCH_SIZE, 1)]
