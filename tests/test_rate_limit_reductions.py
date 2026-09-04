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
    core._BOARD_REFRESH_CACHE.clear()
    core._BOARD_TIME_CACHE.clear()
    core._READ_CACHE.clear()
    core._READ_STALE_CACHE.clear()
    core._READ_INFLIGHT.clear()
    core._BOARD_INFLIGHT.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    core._CACHE_PRUNE_STATE.clear()
    yield
    core._BOARD_PAGE_CACHE.clear()
    core._BOARD_INDEX_CACHE.clear()
    core._BOARD_REFRESH_CACHE.clear()
    core._BOARD_TIME_CACHE.clear()
    core._READ_CACHE.clear()
    core._READ_STALE_CACHE.clear()
    core._READ_INFLIGHT.clear()
    core._BOARD_INFLIGHT.clear()
    core._LATEST_ID_CACHE.clear()
    core._AUTHOR_CODE_CACHE.clear()
    core._CACHE_PRUNE_STATE.clear()


def test_core_caches_use_separate_locks():
    locks = {
        core._BOARD_PAGE_CACHE_LOCK,
        core._BOARD_INDEX_CACHE_LOCK,
        core._BOARD_TIME_CACHE_LOCK,
        core._READ_CACHE_LOCK,
        core._READ_STALE_CACHE_LOCK,
        core._READ_INFLIGHT_LOCK,
        core._LATEST_ID_CACHE_LOCK,
        core._AUTHOR_CODE_CACHE_LOCK,
    }

    assert len(locks) == 8


def test_read_resilience_defaults_are_enabled():
    assert core.READ_CACHE_TTL == 30
    assert core.READ_STALE_TTL == 300
    assert core.READ_FETCH_TIMEOUT == 50
    assert core.READ_SINGLEFLIGHT_TIMEOUT >= 30


def test_read_cache_key_is_canonical_across_navigation_context():
    first = core._read_cache_key(
        "123",
        "test",
        kind="minor",
        recommend=0,
        search_type="subject_m",
        search_keyword="first",
        head_id="10",
    )
    second = core._read_cache_key(
        "123",
        "test",
        kind="minor",
        recommend=1,
        search_type="memo",
        search_keyword="second",
        head_id="20",
    )

    assert first == second == ("test", "minor", "123")


def test_author_code_cache_ttl_is_one_hour():
    assert core.AUTHOR_CODE_CACHE_TTL == 3600


def test_board_time_cache_has_dedicated_max_items_constant():
    assert core.BOARD_TIME_CACHE_MAX_ITEMS == core.BOARD_PAGE_CACHE_MAX_ITEMS


def test_related_page_estimate_matches_board_list_page_size():
    assert core.DOCS_PER_PAGE_ESTIMATE == core.dc_api.BOARD_LIST_PAGE_SIZE
    assert core.RELATED_PAGE_FETCH_SIZE == core.dc_api.BOARD_LIST_PAGE_SIZE


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

        async def board(self, board_id, headtexts_collector=None, pagination_collector=None, **kwargs):
            await asyncio.sleep(0.01 if board_id == "alpha" else 0)
            headtexts = [{"head_id": None, "label": board_id, "active": True}]
            if headtexts_collector is not None:
                headtexts_collector[:] = headtexts
            else:
                self.last_board_headtexts = headtexts
            if pagination_collector is not None:
                pagination_collector.update({"requested_page": 1, "current_page": 1, "has_next": False})
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

        async def board(self, headtexts_collector=None, pagination_collector=None, **kwargs):
            self.board_calls += 1
            if headtexts_collector is not None:
                headtexts_collector[:] = [{"head_id": None, "label": "전체", "active": True}]
            if pagination_collector is not None:
                pagination_collector.update({"requested_page": 1, "current_page": 1, "has_next": False})
            yield _index_item(123, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first_pagination = {}
    first_rows, first_categories = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        kind="minor",
        limit=1,
        max_scan_pages=1,
        pagination_collector=first_pagination,
    )
    first_rows[0]["title"] = "mutated"
    first_categories[0]["label"] = "mutated"
    first_pagination["current_page"] = 99
    second_pagination = {}
    second_rows, second_categories = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        kind="minor",
        limit=1,
        max_scan_pages=1,
        pagination_collector=second_pagination,
    )

    assert len(FakeAPI.instances) == 1
    assert FakeAPI.instances[0].board_calls == 1
    assert second_rows[0]["title"] == "title 123"
    assert second_categories[0]["label"] == "전체"
    assert second_pagination == {"requested_page": 1, "current_page": 1, "has_next": False}


@pytest.mark.asyncio
async def test_async_index_force_refresh_replaces_cached_board_rows(monkeypatch):
    class FakeAPI:
        instances = []
        board_calls = 0

        def __init__(self):
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.__class__.board_calls += 1
            yield _index_item(100 + self.__class__.board_calls, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first_rows, _ = await core.async_index_with_head_categories(1, "test", 0, limit=1)
    cached_rows, _ = await core.async_index_with_head_categories(1, "test", 0, limit=1)
    refreshed_rows, _ = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        limit=1,
        force_refresh=True,
    )
    replaced_cache_rows, _ = await core.async_index_with_head_categories(1, "test", 0, limit=1)

    assert [row["id"] for row in first_rows] == ["101"]
    assert [row["id"] for row in cached_rows] == ["101"]
    assert [row["id"] for row in refreshed_rows] == ["102"]
    assert [row["id"] for row in replaced_cache_rows] == ["102"]
    assert len(FakeAPI.instances) == 2
    assert FakeAPI.board_calls == 2


@pytest.mark.asyncio
async def test_async_index_throttles_repeated_force_refresh(monkeypatch):
    class FakeAPI:
        board_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.__class__.board_calls += 1
            yield _index_item(200 + self.__class__.board_calls, is_mobile_source=True)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    monkeypatch.setattr(core, "BOARD_FORCE_REFRESH_COOLDOWN", 5)

    initial_rows, _ = await core.async_index_with_head_categories(1, "test", 0, limit=1)
    refreshed_rows, _ = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        limit=1,
        force_refresh=True,
    )
    throttled_rows, _ = await core.async_index_with_head_categories(
        1,
        "test",
        0,
        limit=1,
        force_refresh=True,
    )

    assert [row["id"] for row in initial_rows] == ["201"]
    assert [row["id"] for row in refreshed_rows] == ["202"]
    assert [row["id"] for row in throttled_rows] == ["202"]
    assert FakeAPI.board_calls == 2


@pytest.mark.asyncio
async def test_async_index_short_caches_empty_force_refresh(monkeypatch):
    class FakeAPI:
        board_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def board(self, **kwargs):
            self.__class__.board_calls += 1
            if False:
                yield None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    monkeypatch.setattr(core, "BOARD_FORCE_REFRESH_COOLDOWN", 5)

    first_rows, first_categories = await core.async_index_with_head_categories(
        1,
        "missing",
        0,
        limit=1,
        force_refresh=True,
    )
    second_rows, second_categories = await core.async_index_with_head_categories(
        1,
        "missing",
        0,
        limit=1,
        force_refresh=True,
    )

    assert (first_rows, first_categories) == ([], [])
    assert (second_rows, second_categories) == ([], [])
    assert FakeAPI.board_calls == 1


@pytest.mark.asyncio
async def test_async_index_pagination_collector_clears_for_zero_limit_and_reads_legacy_cache():
    cleared = {"stale": True}

    assert await core.async_index_with_head_categories(
        1,
        "zero",
        0,
        limit=0,
        pagination_collector=cleared,
    ) == ([], [])
    assert cleared == {}

    cache_key = core._board_index_cache_key(1, "legacy", 0, fetch_num=1)
    core._cache_set(
        core._BOARD_INDEX_CACHE,
        core._BOARD_INDEX_CACHE_LOCK,
        cache_key,
        ([{"id": "123"}], []),
        core.BOARD_PAGE_CACHE_TTL,
        core.BOARD_INDEX_CACHE_MAX_ITEMS,
    )
    legacy_pagination = {"stale": True}

    rows, categories = await core.async_index_with_head_categories(
        1,
        "legacy",
        0,
        limit=1,
        pagination_collector=legacy_pagination,
    )

    assert rows == [{"id": "123"}]
    assert categories == []
    assert legacy_pagination == {}


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

    first_rows, first_categories = await core.async_index_with_head_categories(1, "test", 0, limit=1)
    second_rows, second_categories = await core.async_index_with_head_categories(1, "test", 0, limit=1)

    assert first_rows == second_rows == []
    assert first_categories == second_categories == []
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

    rows, categories = await core.async_index_with_head_categories(1, "test", 0)

    assert [row["id"] for row in rows] == ["123"]
    assert categories == []
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
async def test_async_read_cache_can_be_disabled(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 0)
    monkeypatch.setattr(core, "READ_STALE_TTL", 0)

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
    assert cached_data["related_posts"] == []
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

    with pytest.raises(core.dc_api.DocumentUnavailableError):
        await core.async_read("123", "test")
    with pytest.raises(core.dc_api.DocumentUnavailableError):
        await core.async_read("123", "test")

    assert FakeAPI.calls == 2


@pytest.mark.asyncio
async def test_async_read_singleflight_collapses_concurrent_requests(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 30)

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
            await asyncio.sleep(0.05)
            return FakeDocument()

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    results = await asyncio.gather(*(core.async_read("123", "test") for _ in range(12)))

    assert FakeAPI.calls == 1
    results[0][0]["html"] = "mutated"
    assert all(result[0]["html"] == "<p>body</p>" for result in results[1:])


@pytest.mark.asyncio
async def test_async_read_singleflight_failure_is_removed_for_retry(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 0)
    attempts = 0

    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>recovered</p>"
        images = []
        related_posts = []
        embedded_comments = []
        embedded_comment_total = 0

        async def comments(self):
            if False:
                yield None

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise core.dc_api.DocumentUnavailableError("temporary")
            return FakeDocument()

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    with pytest.raises(core.dc_api.DocumentUnavailableError):
        await core.async_read("123", "test")
    recovered, _comments, _images = await core.async_read("123", "test")

    assert recovered["html"] == "<p>recovered</p>"
    assert attempts == 2
    assert core._READ_INFLIGHT == {}


@pytest.mark.asyncio
async def test_async_read_waiter_cancellation_does_not_cancel_owner(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_load(*args, **kwargs):
        started.set()
        await release.wait()
        return (
            {
                "title": "title",
                "html": "<p>body</p>",
                "related_posts": [],
                "_comments_complete": True,
                "_comment_prefer_mobile": True,
            },
            [],
            [],
        )

    monkeypatch.setattr(core, "_load_read_payload", slow_load)
    owner = asyncio.create_task(core.async_read("123", "test"))
    await started.wait()
    waiter = asyncio.create_task(core.async_read("123", "test"))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    data, _comments, _images = await owner

    assert data["html"] == "<p>body</p>"
    assert core._READ_INFLIGHT == {}


@pytest.mark.asyncio
async def test_async_read_reuses_recent_body_without_refetching_document(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 0)
    monkeypatch.setattr(core, "READ_STALE_TTL", 30)
    document_calls = 0

    class FakeDocument:
        title = "title"
        author = "익명"
        author_id = None
        time = "-"
        voteup_count = 0
        html = "<p>stable</p>"
        images = []
        related_posts = []
        embedded_comments = []
        embedded_comment_total = 0

        async def comments(self):
            if False:
                yield None

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            nonlocal document_calls
            document_calls += 1
            return FakeDocument()

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    first, _comments, _images = await core.async_read("123", "test")
    cache_key = core._read_cache_key("123", "test")
    first_expiry = core._READ_STALE_CACHE[cache_key]["expires_at"]
    cached_body, _comments, _images = await core.async_read("123", "test")

    assert first["html"] == "<p>stable</p>"
    assert cached_body["html"] == "<p>stable</p>"
    assert document_calls == 1
    assert core._READ_STALE_CACHE[cache_key]["expires_at"] == first_expiry


@pytest.mark.asyncio
async def test_async_read_does_not_cache_incomplete_comments(monkeypatch):
    monkeypatch.setattr(core, "READ_CACHE_TTL", 30)

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
        embedded_comment_total = 1

        def __init__(self):
            self.comment_status = {"complete": False}

        async def comments(self):
            self.comment_status = {"complete": False}
            if False:
                yield None

    class FakeAPI:
        calls = 0
        comment_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            self.__class__.calls += 1
            return FakeDocument()

        async def comments(self, *args, status_collector=None, **kwargs):
            self.__class__.comment_calls += 1
            if status_collector is not None:
                status_collector.update({"complete": False, "source": None})
            if False:
                yield None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    await core.async_read("123", "test")
    await core.async_read("123", "test")

    assert FakeAPI.calls == 1
    assert FakeAPI.comment_calls == 1
    assert core._READ_CACHE == {}


@pytest.mark.asyncio
async def test_async_read_owner_timeout_is_bounded_and_cleans_flight(monkeypatch):
    monkeypatch.setattr(core, "READ_FETCH_TIMEOUT", 0.01)

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def document(self, **kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    with pytest.raises(core.dc_api.DocumentUnavailableError, match="timed out"):
        await core.async_read("123", "test")

    assert core._READ_INFLIGHT == {}


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

    related, has_more = await core._related_after_position_with_api(
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
                yield _index_item(160)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["index", "page", "times"])
@pytest.mark.parametrize("ttl", [0, 20])
async def test_board_lookups_coalesce_concurrent_misses_and_copy_results(monkeypatch, lookup, ttl):
    calls = 0
    started, release = asyncio.Event(), asyncio.Event()

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, headtexts_collector=None, pagination_collector=None, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            if headtexts_collector is not None:
                headtexts_collector.append({"head_id": None, "label": "전체"})
            if pagination_collector is not None:
                pagination_collector.update({"current_page": 1, "has_next": True})
            yield _index_item(123)

        async def board_precise_times(self, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"123": "2026-09-05 12:30:00"}

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    monkeypatch.setattr(core, "BOARD_PAGE_CACHE_TTL", ttl)
    monkeypatch.setattr(core, "BOARD_TIME_CACHE_TTL", ttl)
    collectors = [{} for _ in range(32)]

    async def fetch(index):
        if lookup == "index":
            return await core.async_index_with_head_categories(1, "test", 0, pagination_collector=collectors[index])
        if lookup == "page":
            return await core._fetch_board_page(FakeAPI(), 1, "test", 0)
        return await core.async_board_precise_times(1, "test", 0, target_ids=["123"])

    owner = asyncio.create_task(fetch(0))
    await asyncio.wait_for(started.wait(), 2)
    waiters = [asyncio.create_task(fetch(i)) for i in range(1, 32)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(owner, *waiters)
    assert calls == 1
    assert all(result == results[0] for result in results)
    if lookup == "index":
        results[0][0][0]["title"] = "changed"
        results[0][1][0]["label"] = "changed"
        collectors[0]["current_page"] = 999
        assert results[1][0][0]["title"] == "title 123"
        assert results[1][1][0]["label"] == "전체"
        assert collectors[1] == {"current_page": 1, "has_next": True}
    elif lookup == "page":
        results[0][0]["title"] = "changed"
        assert results[1][0]["title"] == "title 123"
    else:
        results[0]["123"] = "changed"
        assert results[1]["123"] == "2026-09-05 12:30"
    await fetch(1)
    assert calls == (2 if ttl == 0 else 1)
    assert core._BOARD_INFLIGHT == {}


@pytest.mark.asyncio
async def test_board_different_filters_run_in_parallel(monkeypatch):
    calls = []
    release = asyncio.Event()

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, **kwargs):
            calls.append((kwargs["search_keyword"], kwargs["head_id"]))
            await release.wait()
            yield _index_item(kwargs["head_id"])

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    tasks = [asyncio.create_task(core.async_index_with_head_categories(
        1, "test", 0, search_keyword=keyword, head_id=head_id,
    )) for keyword, head_id in [("a", "1"), ("a", "2"), ("b", "1")]]
    await asyncio.sleep(0)
    assert calls == [("a", "1"), ("a", "2"), ("b", "1")]
    release.set()
    results = await asyncio.gather(*tasks)
    assert [result[0][0]["id"] for result in results] == ["1", "2", "1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_board_owner_failure_releases_followers_for_retry(monkeypatch, failure):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                raise RuntimeError("upstream failed")
            yield _index_item(123)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    owner = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0))
    await asyncio.wait_for(started.wait(), 2)
    waiter = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0))
    await asyncio.sleep(0)
    if failure == "cancel":
        owner.cancel()
    else:
        release.set()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await owner
    with pytest.raises(core.dc_api.DocumentUnavailableError):
        await asyncio.wait_for(waiter, 2)
    rows, _ = await core.async_index_with_head_categories(1, "test", 0)
    assert rows[0]["id"] == "123"
    assert calls == 2
    assert core._BOARD_INFLIGHT == {}


@pytest.mark.asyncio
async def test_cold_force_refresh_coalesces_and_preserves_pagination(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, pagination_collector=None, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            pagination_collector.update({"current_page": 1, "has_next": False})
            yield _index_item(123)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    owner = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0, force_refresh=True))
    await asyncio.wait_for(started.wait(), 2)
    collector = {}
    waiter = asyncio.create_task(core.async_index_with_head_categories(
        1, "test", 0, force_refresh=True, pagination_collector=collector,
    ))
    await asyncio.sleep(0)
    release.set()
    assert await owner == await waiter
    assert calls == 1
    assert collector == {"current_page": 1, "has_next": False}


@pytest.mark.asyncio
async def test_read_waiter_timeout_does_not_cancel_owner_or_other_waiters(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def load(*args, **kwargs):
        started.set()
        await release.wait()
        return {"html": "body", "related_posts": [{"id": "99"}], "_comments_complete": True}, [], []

    monkeypatch.setattr(core, "_load_read_payload", load)
    monkeypatch.setattr(core, "READ_SINGLEFLIGHT_TIMEOUT", 0.01)
    owner = asyncio.create_task(core.async_read("123", "test"))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(core.dc_api.DocumentUnavailableError, match="wait timed out"):
        await core.async_read("123", "test")
    monkeypatch.setattr(core, "READ_SINGLEFLIGHT_TIMEOUT", 2)
    waiter = asyncio.create_task(core.async_read("123", "test"))
    await asyncio.sleep(0)
    release.set()
    owner_result, waiter_result = await asyncio.gather(owner, waiter)
    assert owner_result[0]["related_posts"] == [{"id": "99"}]
    assert waiter_result[0]["related_posts"] == []
    assert waiter_result[0]["html"] == "body"
    assert core._READ_INFLIGHT == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["index", "page"])
async def test_concurrent_empty_board_result_is_shared_but_not_cached(monkeypatch, lookup):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            if False:
                yield None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)

    def fetch():
        if lookup == "index":
            return core.async_index_with_head_categories(1, "test", 0)
        return core._fetch_board_page(FakeAPI(), 1, "test", 0)

    owner = asyncio.create_task(fetch())
    await asyncio.wait_for(started.wait(), 2)
    waiter = asyncio.create_task(fetch())
    await asyncio.sleep(0)
    release.set()
    assert await owner == await waiter
    assert calls == 1
    await fetch()
    assert calls == 2
    assert core._BOARD_INFLIGHT == {}


@pytest.mark.asyncio
async def test_warm_board_cache_remains_available_during_force_refresh(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set()
                await release.wait()
            yield _index_item(100 + calls)

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    await core.async_index_with_head_categories(1, "test", 0)
    refresh = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0, force_refresh=True))
    await asyncio.wait_for(started.wait(), 2)
    cached, _ = await asyncio.wait_for(core.async_index_with_head_categories(1, "test", 0), 2)
    throttled, _ = await asyncio.wait_for(core.async_index_with_head_categories(1, "test", 0, force_refresh=True), 2)
    assert cached[0]["id"] == throttled[0]["id"] == "101"
    release.set()
    fresh, _ = await refresh
    assert fresh[0]["id"] == "102"
    assert calls == 2


@pytest.mark.asyncio
async def test_force_refresh_during_ordinary_fetch_keeps_empty_result_cache(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def board(self, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            if False:
                yield None

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    ordinary = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0))
    await asyncio.wait_for(started.wait(), 2)
    refresh = asyncio.create_task(core.async_index_with_head_categories(1, "test", 0, force_refresh=True))
    await asyncio.sleep(0)
    release.set()
    assert await ordinary == await refresh == ([], [])
    assert calls == 2
    key = core._board_index_cache_key(1, "test", 0)
    assert core._cache_get(core._BOARD_INDEX_CACHE, core._BOARD_INDEX_CACHE_LOCK, key) == ([], [], {})
    await core.async_index_with_head_categories(1, "test", 0, force_refresh=True)
    assert calls == 2
    assert core._BOARD_INFLIGHT == {}
