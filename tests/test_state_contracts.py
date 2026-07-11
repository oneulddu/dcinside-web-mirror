import base64
import json
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from threading import Barrier
from types import SimpleNamespace

import pytest

from app import create_app, routes
from app.services import core, recent


FIXED_NOW = 1_725_000_000.25


@pytest.fixture(autouse=True)
def isolate_state(monkeypatch):
    monkeypatch.setattr(recent.time, "time", lambda: FIXED_NOW)
    with core._BOARD_PAGE_CACHE_LOCK:
        core._BOARD_PAGE_CACHE.clear()
    with core._LATEST_ID_CACHE_LOCK:
        core._LATEST_ID_CACHE.clear()
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    yield
    with core._BOARD_PAGE_CACHE_LOCK:
        core._BOARD_PAGE_CACHE.clear()
    with core._LATEST_ID_CACHE_LOCK:
        core._LATEST_ID_CACHE.clear()
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()


def _index_item(doc_id):
    return SimpleNamespace(
        id=str(doc_id),
        subject=None,
        title=f"title {doc_id}",
        has_image=False,
        has_video=False,
        author="익명",
        author_id=None,
        author_role=None,
        time="-",
        comment_count=0,
        voteup_count=0,
        view_count=0,
        isimage=False,
        isvideo=False,
        isrecommend=False,
        isdcbest=False,
        ishit=False,
        is_mobile_source=False,
    )


class FakeBoardAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def board(self, **kwargs):
        self.calls.append(kwargs)
        for doc_id in self.pages.get(kwargs["start_page"], []):
            yield _index_item(doc_id)


@pytest.mark.asyncio
async def test_related_cursor_keeps_order_across_overlap_and_internal_duplicates():
    api = FakeBoardAPI(
        {
            1: [110, 109, 108, 107, 107, 106],
            2: [108, 107, 106, 106, 105, 104, 104, 103],
        }
    )

    rows, has_more = await core._related_after_position_with_api(
        api,
        api_id="110",
        after_id="108",
        board="state-contract-more",
        limit=4,
        source_page=1,
        recommend=1,
        tail_pages=1,
    )

    assert [row["id"] for row in rows] == ["107", "106", "105", "104"]
    assert has_more is True
    assert [(call["start_page"], call["num"]) for call in api.calls] == [
        (1, core.RELATED_PAGE_FETCH_SIZE),
        (2, core.RELATED_PAGE_FETCH_SIZE),
    ]


@pytest.mark.asyncio
async def test_related_cursor_reports_end_after_overlap_only_pages_are_exhausted():
    api = FakeBoardAPI(
        {
            1: [210, 209, 208, 207, 207],
            2: [208, 207, 206, 206],
            3: [],
        }
    )

    rows, has_more = await core._related_after_position_with_api(
        api,
        api_id="210",
        after_id="208",
        board="state-contract-end",
        limit=4,
        source_page=1,
        recommend=1,
        tail_pages=2,
    )

    assert [row["id"] for row in rows] == ["207", "206"]
    assert has_more is False
    assert [(call["start_page"], call["num"]) for call in api.calls] == [
        (1, core.RELATED_PAGE_FETCH_SIZE),
        (2, core.RELATED_PAGE_FETCH_SIZE),
        (3, core.RELATED_PAGE_FETCH_SIZE),
    ]


def _cookie_morsel(response, name):
    header = next(
        value
        for value in response.headers.getlist("Set-Cookie")
        if value.startswith(f"{name}=")
    )
    cookies = SimpleCookie()
    cookies.load(header)
    return cookies[name]


def _decode_recent_rows(value):
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


@pytest.mark.parametrize(
    ("path", "board"),
    (("/board", "board_cookie_contract"), ("/read", "read_cookie_contract")),
)
@pytest.mark.parametrize("secure", (False, True), ids=("http", "https"))
def test_board_and_read_recent_cookie_payload_and_attributes(monkeypatch, path, board, secure):
    async def board_payload(*args, **kwargs):
        return [], []

    async def read_payload(*args, **kwargs):
        return (
            {
                "title": "cookie contract",
                "author": "익명",
                "author_code": None,
                "author_role": None,
                "time": "-",
                "voteup_count": 0,
                "contents": "body",
                "html": "<p>body</p>",
                "related_posts": [],
            },
            [],
            [],
        )

    monkeypatch.setattr(routes, "_load_board_payload", board_payload)
    monkeypatch.setattr(routes, "async_read", read_payload)
    monkeypatch.setattr(recent.secrets, "token_urlsafe", lambda size: "contract-visitor-key")
    app = create_app()
    query = {
        "board": board,
        "kind": "minor",
        "recommend": "1",
        "gallery_name": "계약 갤러리",
    }
    if path == "/read":
        query["pid"] = "123"

    response = app.test_client().get(
        path,
        query_string=query,
        base_url=("https://localhost" if secure else "http://localhost"),
    )

    assert response.status_code == 200
    rows_cookie = _cookie_morsel(response, recent.RECENT_COOKIE_NAME)
    key_cookie = _cookie_morsel(response, recent.RECENT_CACHE_KEY_COOKIE_NAME)
    assert _decode_recent_rows(rows_cookie.value) == [
        {
            "board": board,
            "name": "계약 갤러리",
            "kind": "minor",
            "recommend": 1,
            "visited_at": FIXED_NOW,
        }
    ]
    assert key_cookie.value == "contract-visitor-key"

    for morsel in (rows_cookie, key_cookie):
        assert morsel["path"] == "/"
        assert morsel["samesite"] == "Lax"
        assert morsel["max-age"] == str(recent.RECENT_COOKIE_TTL)
        assert bool(morsel["secure"]) is secure
    assert not rows_cookie["httponly"]
    assert key_cookie["httponly"] is True


def test_concurrent_visits_with_same_cache_key_do_not_drop_either_gallery(monkeypatch):
    async def board_payload(*args, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", board_payload)
    app = create_app()
    cache_key = "concurrent-visitor-key"
    rendezvous = Barrier(2)
    load_recent_entries = recent.load_recent_entries

    def synchronized_load():
        rows = load_recent_entries()
        rendezvous.wait(timeout=5)
        return rows

    monkeypatch.setattr(recent, "load_recent_entries", synchronized_load)

    def visit(board):
        client = app.test_client(use_cookies=False)
        return client.get(
            "/board",
            query_string={"board": board, "kind": "minor", "gallery_name": board.upper()},
            headers={"Cookie": f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(visit, board) for board in ("concurrent_a", "concurrent_b")]
        responses = [future.result(timeout=10) for future in futures]

    assert [response.status_code for response in responses] == [200, 200]
    rows = recent.get_recent_server_cache(cache_key)
    assert len(rows) == 2
    assert {row["board"] for row in rows} == {"concurrent_a", "concurrent_b"}
    assert {row["name"] for row in rows} == {"CONCURRENT_A", "CONCURRENT_B"}
    assert {row["visited_at"] for row in rows} == {FIXED_NOW}


def _recent_cookie_header(rows, cache_key):
    encoded = recent._encode_recent_rows(rows)
    return (
        f"{recent.RECENT_COOKIE_NAME}={encoded}; "
        f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}"
    )


def test_recent_remove_deletes_gallery_from_cookie_and_server_cache():
    app = create_app()
    cache_key = "remove-visitor-key-000000"
    rows = [
        {
            "board": "remove_target",
            "name": "지울 갤러리",
            "kind": "minor",
            "recommend": 1,
            "visited_at": FIXED_NOW,
        },
        {
            "board": "keep_target",
            "name": "남길 갤러리",
            "kind": None,
            "recommend": 0,
            "visited_at": FIXED_NOW - 10,
        },
    ]
    recent.set_recent_server_cache(cache_key, rows)

    response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": "remove_target", "kind": "minor", "recommend": "1"},
        headers={"Cookie": _recent_cookie_header(rows, cache_key)},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/recent")
    remaining = _decode_recent_rows(_cookie_morsel(response, recent.RECENT_COOKIE_NAME).value)
    assert [row["board"] for row in remaining] == ["keep_target"]
    assert [row["board"] for row in recent.get_recent_server_cache(cache_key)] == ["keep_target"]


def test_recent_remove_matches_stored_entry_without_kind():
    app = create_app()
    cache_key = "kindless-visitor-key-0000"
    rows = [
        {
            "board": "kindless_target",
            "name": None,
            "kind": None,
            "recommend": 0,
            "visited_at": FIXED_NOW,
        },
    ]
    recent.set_recent_server_cache(cache_key, rows)

    response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": "kindless_target", "kind": "minor", "recommend": "0"},
        headers={"Cookie": _recent_cookie_header(rows, cache_key)},
    )

    assert response.status_code == 302
    assert _decode_recent_rows(_cookie_morsel(response, recent.RECENT_COOKIE_NAME).value) == []
    assert recent.get_recent_server_cache(cache_key) == []


def test_recent_clear_empties_cookie_and_server_cache():
    app = create_app()
    cache_key = "clear-visitor-key-000000"
    rows = [
        {
            "board": "clear_target",
            "name": "비울 갤러리",
            "kind": "mini",
            "recommend": 0,
            "visited_at": FIXED_NOW,
        },
    ]
    recent.set_recent_server_cache(cache_key, rows)

    response = app.test_client(use_cookies=False).post(
        "/recent/clear",
        headers={"Cookie": _recent_cookie_header(rows, cache_key)},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/recent")
    assert _decode_recent_rows(_cookie_morsel(response, recent.RECENT_COOKIE_NAME).value) == []
    assert recent.get_recent_server_cache(cache_key) == []
