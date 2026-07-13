import asyncio

import pytest

from app.services import async_bridge
from app.services import core
from app.services.dc.models import Comment, DocumentIndex


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
    core._BOARD_INDEX_CACHE.clear()
    core._BOARD_TIME_CACHE.clear()
    core._READ_CACHE.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    core._CACHE_PRUNE_STATE.clear()
    yield
    core._BOARD_PAGE_CACHE.clear()
    core._BOARD_INDEX_CACHE.clear()
    core._BOARD_TIME_CACHE.clear()
    core._READ_CACHE.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    core._CACHE_PRUNE_STATE.clear()


def test_core_caches_use_separate_locks():
    locks = {
        core._BOARD_PAGE_CACHE_LOCK,
        core._BOARD_INDEX_CACHE_LOCK,
        core._BOARD_TIME_CACHE_LOCK,
        core._READ_CACHE_LOCK,
        core._LATEST_ID_CACHE_LOCK,
        core._AUTHOR_CODE_CACHE_LOCK,
    }

    assert len(locks) == 6


def test_author_code_cache_ttl_is_one_hour():
    assert core.AUTHOR_CODE_CACHE_TTL == 3600


def test_board_time_cache_has_dedicated_max_items_constant():
    assert core.BOARD_TIME_CACHE_MAX_ITEMS == core.BOARD_PAGE_CACHE_MAX_ITEMS


def test_normalize_author_preserves_existing_name_and_code_rules():
    assert core._normalize_author("닉네임(abc123)") == ("닉네임", "abc123")
    assert core._normalize_author("닉네임(abc123") == ("닉네임", "abc123")
    assert core._normalize_author("ㅇㅇ(1.2)") == ("익명", "1.2")
    assert core._normalize_author("테스트갤러") == ("익명", None)
    assert core._normalize_author("닉\u00ad네임", " (ipcode) ") == ("닉네임", "ipcode")


def test_shared_api_head_categories_are_request_scoped(monkeypatch):
    async_bridge.shutdown_async_bridge()

    class FakeSession:
        closed = False

    class FakeAPI:
        instances = []

        def __init__(self):
            self.session = FakeSession()
            self.close_calls = 0
            self.last_board_headtexts = []
            self.__class__.instances.append(self)

        async def close(self):
            self.close_calls += 1
            self.session.closed = True

        async def board(self, board_id, headtexts_collector=None, **kwargs):
            await asyncio.sleep(0.01 if board_id == "alpha" else 0)
            headtexts = [{"head_id": None, "label": board_id, "active": True}]
            if headtexts_collector is not None:
                headtexts_collector[:] = headtexts
            else:
                self.last_board_headtexts = headtexts
            yield _index_item(101 if board_id == "alpha" else 202)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    async def load_both():
        return await asyncio.gather(
            core.async_index_with_head_categories(1, "alpha", 0, limit=1),
            core.async_index_with_head_categories(1, "beta", 0, limit=1),
        )

    try:
        results = async_bridge.run_async(load_both())
    finally:
        async_bridge.shutdown_async_bridge()

    assert len(FakeAPI.instances) == 1
    assert [results[0][1][0]["label"], results[1][1][0]["label"]] == ["alpha", "beta"]
    assert FakeAPI.instances[0].close_calls == 1


@pytest.mark.asyncio
async def test_async_index_with_head_categories_reuses_short_cache(monkeypatch):
    class FakeAPI:
        instances = []

        def __init__(self):
            self.board_calls = 0
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, headtexts_collector=None, **kwargs):
            self.board_calls += 1
            if headtexts_collector is not None:
                headtexts_collector[:] = [{"head_id": None, "label": "전체", "active": True}]
            yield _index_item(123, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first_rows, first_categories, first_search_nav = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        kind="minor",
        limit=1,
        max_scan_pages=1,
    )
    first_rows[0]["title"] = "mutated"
    first_categories[0]["label"] = "mutated"
    second_rows, second_categories, second_search_nav = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        kind="minor",
        limit=1,
        max_scan_pages=1,
    )

    assert len(FakeAPI.instances) == 1
    assert FakeAPI.instances[0].board_calls == 1
    assert second_rows[0]["title"] == "title 123"
    assert second_categories[0]["label"] == "전체"
    assert first_search_nav is None
    assert second_search_nav is None


@pytest.mark.asyncio
async def test_async_index_cache_key_includes_limit_and_scan_bounds(monkeypatch):
    class FakeAPI:
        instances = []

        def __init__(self):
            self.calls = []
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.calls.append(
                (
                    kwargs["num"],
                    kwargs["max_scan_pages"],
                    kwargs["document_id_upper_limit"],
                    kwargs["document_id_lower_limit"],
                )
            )
            for offset in range(kwargs["num"]):
                yield _index_item(100 + offset, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    await core.async_index_with_head_categories(1, "test", 0, limit=1, max_scan_pages=1)
    await core.async_index_with_head_categories(1, "test", 0, limit=2, max_scan_pages=1)
    await core.async_index_with_head_categories(
        1,
        "test",
        0,
        limit=1,
        max_scan_pages=2,
        document_id_upper_limit=200,
        document_id_lower_limit=10,
    )

    assert len(FakeAPI.instances) == 3
    assert [instance.calls[0] for instance in FakeAPI.instances] == [
        (1, 1, None, None),
        (2, 1, None, None),
        (1, 2, 200, 10),
    ]


@pytest.mark.asyncio
async def test_async_index_cache_distinguishes_source_pattern(monkeypatch):
    class FakeAPI:
        instances = []

        def __init__(self):
            self.patterns = []
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.patterns.append(kwargs.get("list_pattern"))
            yield _index_item(123, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    await core.async_index_with_head_categories(
        1, "index-source-pattern", 0, search_keyword="hello", list_pattern="mobile"
    )
    await core.async_index_with_head_categories(
        1, "index-source-pattern", 0, search_keyword="hello", list_pattern="normal"
    )

    assert [instance.patterns for instance in FakeAPI.instances] == [["mobile"], ["normal"]]


@pytest.mark.asyncio
async def test_async_index_with_head_categories_does_not_cache_empty_results(monkeypatch):
    class FakeAPI:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.__class__.calls += 1
            if False:
                yield None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first_rows, first_categories, first_search_nav = await core.async_index_with_head_categories(1, "test", 0, limit=1)
    second_rows, second_categories, second_search_nav = await core.async_index_with_head_categories(1, "test", 0, limit=1)

    assert first_rows == second_rows == []
    assert first_categories == second_categories == []
    assert first_search_nav is second_search_nav is None
    assert FakeAPI.calls == 2


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
async def test_fetch_board_page_cache_distinguishes_search_pos():
    class FakeAPI:
        def __init__(self):
            self.search_positions = []

        async def board(self, **kwargs):
            self.search_positions.append(kwargs.get("search_pos"))
            yield _index_item(123, is_mobile_source=True)

    api = FakeAPI()

    await core._fetch_board_page(api, 1, "search-pos-test", 0, search_pos=-10)
    await core._fetch_board_page(api, 1, "search-pos-test", 0, search_pos=-20)

    assert api.search_positions == [-10, -20]


@pytest.mark.asyncio
async def test_fetch_board_page_cache_distinguishes_source_pattern():
    class FakeAPI:
        def __init__(self):
            self.patterns = []

        async def board(self, **kwargs):
            self.patterns.append(kwargs.get("list_pattern"))
            yield _index_item(123, is_mobile_source=True)

    api = FakeAPI()

    await core._fetch_board_page(api, 1, "source-pattern-test", 0, list_pattern="mobile")
    await core._fetch_board_page(api, 1, "source-pattern-test", 0, list_pattern="normal")

    assert api.patterns == ["mobile", "normal"]


@pytest.mark.asyncio
async def test_fetch_board_page_cache_restores_search_navigation():
    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def board(self, **kwargs):
            self.calls += 1
            kwargs["search_nav_collector"].update({"next_pos": -20})
            yield _index_item(123, is_mobile_source=True)

    api = FakeAPI()
    second_nav = {}

    await core._fetch_board_page(api, 1, "test", 0, search_keyword="hello", search_pos=-10)
    await core._fetch_board_page(
        api,
        1,
        "test",
        0,
        search_keyword="hello",
        search_pos=-10,
        search_nav_collector=second_nav,
    )

    assert api.calls == 1
    assert second_nav == {"next_pos": -20}


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

    rows, categories, search_nav = await core.async_index_with_head_categories(1, "test", 0)

    assert [row["id"] for row in rows] == ["123"]
    assert categories == []
    assert search_nav is None
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
async def test_read_document_passes_search_pos_to_document_fetch():
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
    await core._read_document_with_api(api, "123", "test", search_pos="-10")

    assert api.kwargs["search_pos"] == "-10"


def test_read_cache_key_distinguishes_normalized_search_pos():
    first = core._read_cache_key("123", "test", search_pos="-10")
    same = core._read_cache_key("123", "test", search_pos=-10)
    second = core._read_cache_key("123", "test", search_pos=-20)

    assert first == same
    assert first != second


@pytest.mark.asyncio
async def test_async_read_cache_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 0)

    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = []
        related_posts = []
        embedded_comments = []
        embedded_comment_total = 0

        async def comments(self):
            if False:
                yield None

    class FakeAPI:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            self.__class__.calls += 1
            return FakeDocument()

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    await core.async_read("123", "test")
    await core.async_read("123", "test")

    assert FakeAPI.calls == 2


@pytest.mark.asyncio
async def test_async_read_cache_returns_mutation_safe_copies(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 30)

    class FakeImage:
        src = "https://img.dcinside.com/original.jpg"

    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>body</p>"
        images = [FakeImage()]
        related_posts = [_index_item(456)]
        embedded_comments = [
            Comment(
                id="1",
                parent_id="1",
                author="댓글작성자",
                author_id=None,
                contents="embedded comment",
                dccon="https://dccon.dcinside.com/original.png",
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
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            self.__class__.calls += 1
            return FakeDocument()

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    data, comments, images = await core.async_read("123", "test")
    data["related_posts"][0]["title"] = "mutated"
    data.pop("related_posts")
    data["html"] = "<p>mutated</p>"
    comments[0]["dccon"] = "/media?src=mutated"
    images.append("mutated")

    cached_data, cached_comments, cached_images = await core.async_read("123", "test")

    assert FakeAPI.calls == 1
    assert cached_data["html"] == "<p>body</p>"
    assert [row["id"] for row in cached_data["related_posts"]] == ["456"]
    assert cached_data["related_posts"][0]["title"] == "title 456"
    assert cached_comments[0]["dccon"] == "https://dccon.dcinside.com/original.png"
    assert cached_images == ["https://img.dcinside.com/original.jpg"]


@pytest.mark.asyncio
async def test_async_read_cache_skips_missing_document_payload(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 30)

    class FakeAPI:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            self.__class__.calls += 1
            return None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first_data, _first_comments, _first_images = await core.async_read("123", "test")
    second_data, _second_comments, _second_images = await core.async_read("123", "test")

    assert first_data["html"] == "게시글 데이터를 가져오는 데 실패했습니다."
    assert second_data["html"] == "게시글 데이터를 가져오는 데 실패했습니다."
    assert FakeAPI.calls == 2


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
async def test_related_after_position_uses_source_page_before_latest_lookup_without_author_backfill(monkeypatch):
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
            elif kwargs["start_page"] == 2:
                yield _index_item(100)
                yield _index_item(99)
                yield _index_item(98)

    api = FakeAPI()

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=1,
        source_page=2,
    )

    assert [row["id"] for row in related] == ["99"]
    assert "search_pos" not in related[0]
    assert has_more is True
    assert next_search_pos is None
    assert api.calls == [(2, core.RELATED_PAGE_FETCH_SIZE)]


@pytest.mark.asyncio
async def test_related_after_position_skips_shifted_page_overlap_before_cursor():
    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            self.calls.append((kwargs["start_page"], kwargs["num"]))
            if kwargs["start_page"] == 1:
                yield _index_item(102)
                yield _index_item(101)
                yield _index_item(100)
            elif kwargs["start_page"] == 2:
                # New posts can shift the old page prefix onto the next page
                # between requests. Those rows are at or before the cursor.
                yield _index_item(101)
                yield _index_item(100)
                yield _index_item(99)
                yield _index_item(98)
                yield _index_item(97)

    api = FakeAPI()

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        api,
        "200",
        "100",
        "test",
        limit=2,
        source_page=1,
        recommend=1,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["99", "98"]
    assert has_more is True
    assert next_search_pos is None
    assert api.calls == [
        (1, core.RELATED_PAGE_FETCH_SIZE),
        (2, core.RELATED_PAGE_FETCH_SIZE),
    ]


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

    related, has_more, next_search_pos = await core._related_after_position_with_api(
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
    assert next_search_pos is None
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

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=1,
        source_page=9,
    )

    assert [row["id"] for row in related] == ["99"]
    assert has_more is True
    assert next_search_pos is None
    assert api.calls == [
        (9, core.RELATED_PAGE_FETCH_SIZE),
        (1, 1),
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

    related, has_more, next_search_pos = await core._related_after_position_with_api(
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
    assert next_search_pos is None
    assert api.calls == [(1, core.RELATED_PAGE_FETCH_SIZE, 1)]


@pytest.mark.asyncio
async def test_related_after_position_crosses_search_block_boundary():
    current_pos = -20816199
    next_pos = -20806199

    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_pos = kwargs.get("search_pos")
            self.calls.append((page, search_pos))
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None:
                search_nav.update({"next_pos": next_pos})
            if search_pos == current_pos and page == 1:
                yield _index_item(100)
                yield _index_item(99)
            elif search_pos == next_pos and page == 1:
                yield _index_item(98)
                yield _index_item(97)

    api = FakeAPI()

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        api,
        "100",
        "100",
        "test",
        limit=2,
        source_page=1,
        search_keyword="hello",
        search_pos=current_pos,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["99", "98"]
    assert [row["search_pos"] for row in related] == [current_pos, next_pos]
    assert has_more is True
    assert next_search_pos == next_pos
    assert api.calls == [(1, current_pos), (2, current_pos), (1, next_pos)]


@pytest.mark.asyncio
async def test_related_after_position_keeps_search_cursor_in_original_block():
    current_pos = -20816199

    class FakeAPI:
        async def board(self, **kwargs):
            if kwargs["start_page"] == 1:
                yield _index_item(100)
                yield _index_item(99)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "test",
        limit=1,
        source_page=1,
        search_keyword="hello",
        search_pos=current_pos,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["99"]
    assert has_more is False
    assert next_search_pos == current_pos


@pytest.mark.asyncio
async def test_related_after_position_does_not_claim_more_without_advancing_cursor():
    current_pos = -20816199
    next_pos = -20806199

    class FakeAPI:
        async def board(self, **kwargs):
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None:
                search_nav.update({"next_pos": next_pos})
            if kwargs["start_page"] == 1:
                yield _index_item(100)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "test",
        limit=1,
        source_page=1,
        search_keyword="hello",
        search_pos=current_pos,
        tail_pages=0,
    )

    assert related == []
    assert has_more is False
    assert next_search_pos is None


@pytest.mark.asyncio
async def test_related_after_position_skips_duplicate_only_search_block():
    first_pos = -300
    duplicate_pos = -200
    final_pos = -100

    class FakeAPI:
        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_pos = kwargs.get("search_pos")
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None:
                if search_pos == first_pos:
                    search_nav.update({"next_pos": duplicate_pos})
                elif search_pos == duplicate_pos:
                    search_nav.update({"next_pos": final_pos})
            if search_pos == first_pos and page == 1:
                yield _index_item(100)
                yield _index_item(99)
            elif search_pos == duplicate_pos and page == 1:
                yield _index_item(99)
            elif search_pos == final_pos and page == 1:
                yield _index_item(98)
                yield _index_item(97)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "duplicate-search-block",
        limit=2,
        source_page=1,
        search_keyword="hello",
        search_pos=first_pos,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["99", "98"]
    assert [row["search_pos"] for row in related] == [first_pos, final_pos]
    assert has_more is True
    assert next_search_pos == final_pos


@pytest.mark.asyncio
async def test_related_after_position_uses_empty_search_block_navigation():
    first_pos = -300
    empty_pos = -200
    final_pos = -100

    class FakeAPI:
        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_pos = kwargs.get("search_pos")
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None:
                if search_pos == first_pos:
                    search_nav.update({"next_pos": empty_pos})
                elif search_pos == empty_pos:
                    search_nav.update({"next_pos": final_pos})
            if search_pos == first_pos and page == 1:
                yield _index_item(100)
            elif search_pos == final_pos and page == 1:
                yield _index_item(98)
                yield _index_item(97)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "empty-search-block",
        limit=1,
        source_page=1,
        search_keyword="hello",
        search_pos=first_pos,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["98"]
    assert related[0]["search_pos"] == final_pos
    assert has_more is True
    assert next_search_pos == final_pos


@pytest.mark.asyncio
async def test_related_after_position_crosses_after_repeated_last_search_page():
    first_pos = -300
    next_pos = -200
    calls = []

    class FakeAPI:
        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_pos = kwargs.get("search_pos")
            calls.append((page, search_pos))
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None and search_pos == first_pos:
                search_nav.update({"next_pos": next_pos})
            if search_pos == first_pos:
                # PC redirects every out-of-range page back to the same last page.
                yield _index_item(100)
                yield _index_item(99)
            elif search_pos == next_pos and page == 1:
                yield _index_item(98)
                yield _index_item(97)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "repeated-last-search-page",
        limit=2,
        source_page=1,
        search_keyword="hello",
        search_pos=first_pos,
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["99", "98"]
    assert has_more is True
    assert next_search_pos == next_pos
    assert calls == [(1, first_pos), (2, first_pos), (1, next_pos)]


@pytest.mark.asyncio
async def test_related_after_position_reports_more_for_remaining_search_page():
    class FakeAPI:
        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None:
                search_nav.update({"next_page": page + 1, "source_pattern": "mobile"})
            if page == 1:
                yield _index_item(100)
                yield _index_item(99)
            elif page == 2:
                for doc_id in range(98, 69, -1):
                    yield _index_item(doc_id)

    related, has_more, next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "remaining-search-page",
        limit=30,
        source_page=1,
        search_keyword="hello",
        tail_pages=1,
        list_pattern="mobile",
    )

    assert len(related) == 30
    assert related[-1]["id"] == "70"
    assert has_more is True
    assert next_search_pos is None


@pytest.mark.asyncio
async def test_related_after_position_uses_supplied_source_pattern_first():
    patterns = []

    class FakeAPI:
        async def board(self, **kwargs):
            patterns.append(kwargs.get("list_pattern"))
            yield _index_item(100)
            yield _index_item(99)

    related, _has_more, _next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "100",
        "100",
        "initial-source-pattern",
        limit=1,
        source_page=15,
        search_keyword="hello",
        list_pattern="normal",
        tail_pages=0,
    )

    assert [row["id"] for row in related] == ["99"]
    assert patterns == ["normal"]


@pytest.mark.asyncio
async def test_related_after_position_rejects_cyclic_newer_search_rows():
    first_pos = -100
    cyclic_pos = -200

    class FakeAPI:
        async def board(self, **kwargs):
            page = kwargs["start_page"]
            search_pos = kwargs.get("search_pos")
            search_nav = kwargs.get("search_nav_collector")
            if search_nav is not None and search_pos == first_pos:
                search_nav.update({"next_pos": cyclic_pos, "source_pattern": "mobile"})
            if search_pos == first_pos and page == 1:
                yield _index_item(296)
                yield _index_item(295)
            elif search_pos == cyclic_pos and page == 1:
                yield _index_item(300)

    related, has_more, _next_search_pos = await core._related_after_position_with_api(
        FakeAPI(),
        "296",
        "296",
        "cyclic-search-order",
        limit=2,
        source_page=1,
        search_keyword="hello",
        search_pos=first_pos,
        list_pattern="mobile",
        tail_pages=1,
    )

    assert [row["id"] for row in related] == ["295"]
    assert has_more is False


@pytest.mark.asyncio
async def test_related_latest_id_cache_isolated_by_source_pattern():
    with core._LATEST_ID_CACHE_LOCK:
        core._LATEST_ID_CACHE.clear()
    latest_patterns = []

    class FakeAPI:
        async def board(self, **kwargs):
            pattern = kwargs.get("list_pattern")
            page = kwargs["start_page"]
            if kwargs["num"] == 1:
                latest_patterns.append(pattern)
                yield _index_item(1000 if pattern == "normal" else 500)
                return
            expected_page = 5 if pattern == "normal" else 3
            if page == expected_page:
                yield _index_item(100)
                yield _index_item(99)

    for pattern in ("normal", "mobile"):
        related, _has_more, _next_pos = await core._related_after_position_with_api(
            FakeAPI(),
            "100",
            "100",
            "latest-source-pattern",
            limit=1,
            search_keyword="hello",
            list_pattern=pattern,
            tail_pages=0,
        )
        assert [row["id"] for row in related] == ["99"]

    assert latest_patterns == ["normal", "mobile"]
