import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import lxml.html
import pytest

from app import create_app, routes
from app.services import core, heung, link_preview
from app.services.dc.api import API


@pytest.fixture
def precise_origin(monkeypatch):
    for name in ("_BOARD_TIME_CACHE", "_BOARD_INFLIGHT", "_CACHE_PRUNE_STATE"):
        monkeypatch.setattr(core, name, {})
    state = {"calls": 0, "failed_pages": set(), "empty": False}
    api = API.__new__(API)

    async def fetch(urls, **kwargs):
        from urllib.parse import parse_qs, urlparse
        page = int(parse_qs(urlparse(urls[0]).query)["page"][0])
        state["calls"] += 1
        if page in state["failed_pages"]:
            return None, "", None
        rows = "" if state["empty"] else f'''
            <tr class="ub-content us-post" data-no="{page}">
            <td class="gall_tit"><a href="/board/view/?id=test&no={page}">글</a></td>
            <td class="gall_writer" data-nick="익명"></td>
            <td class="gall_date" title="2026.09.23 12:00:00"></td></tr>'''
        return lxml.html.fromstring(f"<table>{rows}</table>"), "", urls[0]

    api._API__fetch_parsed_from_urls = fetch

    @asynccontextmanager
    async def context():
        yield api

    monkeypatch.setattr(core, "dc_api_context", context)
    monkeypatch.setattr(routes, "run_async", asyncio.run)
    return state


def test_precise_first_page_failure_returns_502_without_cache(precise_origin):
    precise_origin["failed_pages"] = {1}
    response = create_app().test_client().get("/board/times?board=test")
    assert response.status_code == 502
    assert response.json["ok"] is False
    assert core._BOARD_TIME_CACHE == {}
    precise_origin["failed_pages"].clear()
    assert create_app().test_client().get("/board/times?board=test").status_code == 200


@pytest.mark.asyncio
async def test_partial_precise_times_retry_without_cache(precise_origin):
    precise_origin["failed_pages"] = {2, 3, 4}
    result = await core.async_board_precise_times(1, "test", 0, target_ids=["1", "2"])
    assert set(result) == {"1"}
    assert core._BOARD_TIME_CACHE == {}
    precise_origin["failed_pages"].clear()
    result = await core.async_board_precise_times(1, "test", 0, target_ids=["1", "2"])
    assert set(result) == {"1", "2"}
    calls = precise_origin["calls"]
    assert await core.async_board_precise_times(1, "test", 0, target_ids=["1", "2"]) == result
    assert precise_origin["calls"] == calls


@pytest.mark.asyncio
async def test_successful_empty_precise_times_are_cached(precise_origin):
    precise_origin["empty"] = True
    assert await core.async_board_precise_times(1, "test", 0) == {}
    assert await core.async_board_precise_times(1, "test", 0) == {}
    assert precise_origin["calls"] == 1


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("retry_seconds", [0, 30])
def test_heung_failure_backoff_and_recovery(monkeypatch, stale, retry_seconds):
    old = [{"rank": 1, "name": "기존", "board_id": "old"}]
    fresh = [{"rank": 1, "name": "새 목록", "board_id": "new"}]
    now = [1000.0]
    fetch = Mock(side_effect=RuntimeError("upstream failed"))
    threads = []

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target
            threads.append(self)

        def start(self):
            self.target()

    monkeypatch.setattr(heung, "HEUNG_CACHE", {"updated_at": 1, "items": old if stale else []})
    monkeypatch.setattr(heung, "HEUNG_CACHE_TTL", 1)
    monkeypatch.setattr(heung, "HEUNG_CACHE_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "HEUNG_REFRESH_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "HEUNG_NEXT_RETRY_AT", 0.0, raising=False)
    monkeypatch.setattr(heung, "HEUNG_REFRESH_RETRY_SECONDS", retry_seconds, raising=False)
    monkeypatch.setattr(heung.time, "time", lambda: now[0])
    monkeypatch.setattr(heung.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(heung, "_read_heung_cache_file", lambda: None)
    monkeypatch.setattr(heung, "_write_heung_cache_file", Mock())
    monkeypatch.setattr(heung, "_fetch_heung_galleries", fetch)

    for _ in range(2):
        if stale:
            assert heung.get_heung_galleries() == (old, 1.0)
        else:
            with pytest.raises(RuntimeError):
                heung.get_heung_galleries()
    expected_calls = 1 if retry_seconds else 2
    assert fetch.call_count == expected_calls
    assert len(threads) == (expected_calls if stale else 0)
    now[0] += retry_seconds
    fetch.side_effect = None
    fetch.return_value = fresh
    heung.get_heung_galleries()
    assert heung.get_heung_galleries() == (fresh, now[0])
    assert heung.HEUNG_NEXT_RETRY_AT == 0
    assert fetch.call_count == expected_calls + 1


@pytest.mark.parametrize("request_name", ["_request_preview_target", "_request_preview_image_target"])
@pytest.mark.parametrize("url, expected", [
    ("https://example.com/한 글%20;x?검색=값 값&x=%2F+/:?@!$'()*,;=[]#fragment",
     "/%ED%95%9C%20%EA%B8%80%20;x?%EA%B2%80%EC%83%89=%EA%B0%92%20%EA%B0%92&x=%2F+/:?@!$'()*,;=[]"),
    ("https://example.com?q=한 글", "/?q=%ED%95%9C%20%EA%B8%80"),
])
def test_preview_request_target_encoding(monkeypatch, request_name, url, expected):
    connection = Mock()
    connection.getresponse.return_value = SimpleNamespace(status=302, headers={}, close=lambda: None)
    monkeypatch.setattr(link_preview, "_connect_pinned_target", lambda *args: Mock())
    monkeypatch.setattr(link_preview, "_check_deadline", lambda *args: 5)
    monkeypatch.setattr(link_preview.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    target = SimpleNamespace(hostname="example.com", port=443, host_header="example.com")
    getattr(link_preview, request_name)(url, target, 0, Mock())
    assert connection.request.call_args.args == ("GET", expected)
    connection.request.call_args.args[1].encode("ascii")


@pytest.mark.parametrize("token", ["한" * 64, "é", "g" * 64, "a" * 63, "a" * 65])
def test_invalid_preview_image_signature_returns_403(token):
    url = "https://example.com/image.png"
    assert link_preview.has_valid_preview_image_signature(url, token, "secret") is False
    response = create_app().test_client().get("/embed/link-preview-image", query_string={"url": url, "token": token})
    assert response.status_code == 403
