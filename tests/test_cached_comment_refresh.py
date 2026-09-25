import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from app import create_app, routes
from app.services import core
from app.services.dc.api import API
from app.services.dc.models import Comment


def comment(comment_id, contents):
    return Comment(id=str(comment_id), parent_id="0", author="익명", author_id=None,
                   contents=contents, dccon=None, voice=None, time="-")


@pytest.fixture
def cached_post(monkeypatch):
    caches = (core._READ_CACHE, core._READ_STALE_CACHE, core._READ_INFLIGHT, core._INITIAL_RELATED_CACHE)
    for cache in caches:
        cache.clear()
    clock = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    monkeypatch.setattr(core, "READ_CACHE_TTL", 30)
    monkeypatch.setattr(core, "READ_STALE_TTL", 300)
    state = {"initial": [comment(1, "기존 댓글")], "rows": [], "complete": True,
             "error": None, "wait": False, "mobile": True, "body_calls": 0, "comment_calls": 0}

    async def initial_comments():
        for row in state["initial"]:
            yield row

    class FakeAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def document(self, **kwargs):
            state["body_calls"] += 1
            return SimpleNamespace(
                title="본문 제목", author="익명", author_id=None, time="-", voteup_count=0,
                html="<p>캐시된 본문</p>", images=[], gallery_name="갤러리 이름", related_posts=[],
                is_mobile_source=state["mobile"],
                embedded_comments=state["initial"], embedded_comment_total=len(state["initial"]),
                comments=initial_comments, comment_status={"complete": True},
            )

        async def comments(self, *args, status_collector, **kwargs):
            state["comment_calls"] += 1
            await asyncio.sleep(0)
            if state["wait"]:
                await asyncio.Event().wait()
            for row in state["rows"]:
                yield row
            status_collector["complete"] = state["complete"]
            if state["error"]:
                raise state["error"]

    monkeypatch.setattr(core.dc_api, "API", FakeAPI)
    yield state, clock
    for cache in caches:
        cache.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [[], [comment(1, "기존 댓글")]])
async def test_complete_cached_post_refreshes_new_comments_after_short_ttl(cached_post, initial):
    state, clock = cached_post
    state["initial"] = initial
    first = await core.async_read("123", "test")
    state["rows"] = initial + [comment(2, "새 댓글")]
    clock[0] += 29
    assert await core.async_read("123", "test") == first
    assert state["comment_calls"] == 0

    clock[0] += 2
    updated = await core.async_read("123", "test")
    assert [row["contents"] for row in updated[1]] == [row.contents for row in state["rows"]]
    assert updated[0]["html"] == first[0]["html"]
    assert updated[0]["gallery_name"] == "갤러리 이름"
    assert state["body_calls"] == state["comment_calls"] == 1
    key = core._read_cache_key("123", "test")
    assert core._READ_STALE_CACHE[key]["expires_at"] == 1300


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [comment(1, "수정된 댓글")], [comment(2, "교체된 댓글")]])
async def test_complete_refresh_replaces_snapshot_including_edits_and_deletions(cached_post, rows):
    state, clock = cached_post
    await core.async_read("123", "test")
    state["rows"] = rows
    clock[0] += 31
    updated = await core.async_read("123", "test")
    assert [(row["id"], row["contents"]) for row in updated[1]] == [(row.id, row.contents) for row in rows]
    assert updated[0]["_comments_complete"] is True


@pytest.mark.asyncio
async def test_failed_refresh_keeps_latest_snapshot_and_retries(cached_post):
    state, clock = cached_post
    await core.async_read("123", "test")
    state["rows"] = [comment(1, "수정됨"), comment(2, "신규")]
    clock[0] += 31
    updated = await core.async_read("123", "test")
    state["rows"] = []
    state["error"] = RuntimeError("upstream failed after status update")
    clock[0] += 31
    failed = await core.async_read("123", "test")
    assert [row["contents"] for row in failed[1]] == ["수정됨", "신규"]
    assert failed[0]["_comments_complete"] is False
    assert core._READ_CACHE == {}
    state["error"] = None
    state["rows"] = [comment(2, "신규"), comment(3, "복구 후 댓글")]
    retried = await core.async_read("123", "test")
    assert [row["id"] for row in retried[1]] == ["2", "3"]
    assert state["body_calls"] == 1
    # Neither the refresh nor callers may mutate an earlier response.
    assert [row["contents"] for row in updated[1]] == ["수정됨", "신규"]


@pytest.mark.asyncio
async def test_partial_refresh_preserves_existing_comments_without_duplicates(cached_post):
    state, clock = cached_post
    state["initial"] = [comment(1, "첫 댓글"), comment(2, "이전 댓글")]
    await core.async_read("123", "test")
    state["rows"] = [comment(1, "첫 댓글 수정"), comment(3, "새 댓글"), comment(3, "새 댓글")]
    state["complete"] = False
    clock[0] += 31
    updated = await core.async_read("123", "test")
    assert [(row["id"], row["contents"]) for row in updated[1]] == [
        ("1", "첫 댓글 수정"), ("2", "이전 댓글"), ("3", "새 댓글"),
    ]
    assert updated[0]["_comments_complete"] is False
    assert core._READ_CACHE == {}


@pytest.mark.asyncio
async def test_concurrent_expired_comment_refresh_is_shared_and_body_expires_on_time(cached_post):
    state, clock = cached_post
    await core.async_read("123", "test")
    clock[0] += 31
    state["rows"] = [comment(1, "기존 댓글"), comment(2, "새 댓글")]
    results = await asyncio.gather(*(core.async_read("123", "test") for _ in range(12)))
    assert all(len(result[1]) == 2 for result in results)
    assert state["comment_calls"] == 1
    results[0][1][0]["contents"] = "호출자 변경"
    assert results[1][1][0]["contents"] == "기존 댓글"
    clock[0] = 1301
    await core.async_read("123", "test")
    assert state["body_calls"] == 2


@pytest.mark.asyncio
async def test_refresh_timeout_serves_latest_comments_without_extending_body_ttl(cached_post, monkeypatch):
    state, clock = cached_post
    await core.async_read("123", "test")
    state["rows"] = [comment(2, "최근에 확인한 댓글")]
    clock[0] += 31
    await core.async_read("123", "test")
    state["wait"] = True
    clock[0] += 31
    monkeypatch.setattr(core, "READ_FETCH_TIMEOUT", 0.01)
    fallback = await core.async_read("123", "test")
    assert [row["contents"] for row in fallback[1]] == ["최근에 확인한 댓글"]
    assert fallback[0]["_served_stale"] is True
    assert core._READ_INFLIGHT == {}
    assert core._READ_STALE_CACHE[core._read_cache_key("123", "test")]["expires_at"] == 1300


def test_read_page_renders_new_comments_while_reusing_body(cached_post, monkeypatch):
    state, clock = cached_post
    monkeypatch.setattr(routes, "run_async", asyncio.run)
    client = create_app().test_client()
    first = client.get("/read?board=test&pid=123")
    assert first.status_code == 200
    state["rows"] = [comment(1, "기존 댓글"), comment(2, "새로 달린 댓글")]
    clock[0] += 31
    response = client.get("/read?board=test&pid=123")
    soup = BeautifulSoup(response.data, "html.parser")
    assert response.status_code == 200
    assert soup.select_one(".comment-shell h2").get_text(" ", strip=True) == "댓글 2"
    assert [node.get_text(strip=True) for node in soup.select(".comment-main p")] == ["기존 댓글", "새로 달린 댓글"]
    assert state["body_calls"] == state["comment_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prefer_mobile", [True, False])
@pytest.mark.parametrize("payload, complete", [
    ({}, False),
    ({"error": "blocked"}, False),
    ({"comments": None}, False),
    ({"comments": [], "total_cnt": 2}, False),
    ({"comments": [{"missing_id": True}]}, False),
    ({"comments": [], "total_cnt": 0}, True),
    ({"comments": None, "total_cnt": 0}, True),
])
async def test_pc_fallback_distinguishes_confirmed_empty_comments_from_failure(cached_post, monkeypatch, payload, complete, prefer_mobile):
    state, clock = cached_post
    state["mobile"] = prefer_mobile
    await core.async_read("123", "test")
    clock[0] += 31
    api = API.__new__(API)

    async def unavailable_mobile(*args, **kwargs):
        raise RuntimeError("mobile unavailable")
        yield

    async def pc_context(*args, **kwargs):
        return {"referer": "https://gall.dcinside.com/board/view/?id=test&no=123",
                "e_s_n_o": "token", "board_type": "", "_GALLTYPE_": "G", "secret_article_key": ""}

    async def request_text(*args, **kwargs):
        return 200, {}, json.dumps(payload)

    api._API__comments_from_mobile = unavailable_mobile
    api._API__get_pc_comment_context = pc_context
    api._API__request_text = request_text

    @asynccontextmanager
    async def context():
        yield api

    monkeypatch.setattr(core, "dc_api_context", context)
    result = await core.async_read("123", "test")
    assert result[0]["_comments_complete"] is complete
    assert [row["contents"] for row in result[1]] == ([] if complete else ["기존 댓글"])
    assert bool(core._READ_CACHE) is complete
