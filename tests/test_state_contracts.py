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


def _same_origin_headers(cookie=None):
    headers = {"Origin": "http://localhost"}
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _recent_response_cookie_header(response, cache_key):
    rows = _cookie_morsel(response, recent.RECENT_COOKIE_NAME).value
    tombstones = _cookie_morsel(response, recent.RECENT_TOMBSTONE_COOKIE_NAME).value
    return (
        f"{recent.RECENT_COOKIE_NAME}={rows}; "
        f"{recent.RECENT_CACHE_KEY_COOKIE_NAME}={cache_key}; "
        f"{recent.RECENT_TOMBSTONE_COOKIE_NAME}={tombstones}"
    )


def _seed_stale_recent_cache(cache_key, rows):
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE[cache_key] = recent.make_recent_server_cache_entry(
            rows,
            FIXED_NOW,
            recent.RECENT_SERVER_CACHE_TTL,
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
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
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
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
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
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/recent")
    assert _decode_recent_rows(_cookie_morsel(response, recent.RECENT_COOKIE_NAME).value) == []
    assert recent.get_recent_server_cache(cache_key) == []


def test_recent_remove_tombstone_filters_stale_cache_from_another_worker():
    app = create_app()
    cache_key = "remove-stale-worker-key"
    rows = [
        {
            "board": "stale_remove_target",
            "name": "다른 worker에 남은 갤러리",
            "kind": "minor",
            "recommend": 0,
            "visited_at": FIXED_NOW - 10,
        },
        {
            "board": "stale_keep_target",
            "name": "남길 갤러리",
            "kind": None,
            "recommend": 0,
            "visited_at": FIXED_NOW - 20,
        },
    ]
    response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": "stale_remove_target", "kind": "minor", "recommend": "0"},
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
    )
    _seed_stale_recent_cache(cache_key, rows)

    with app.test_request_context(
        "/recent",
        headers={"Cookie": _recent_response_cookie_header(response, cache_key)},
    ):
        loaded = recent.load_recent_entries()

    assert [row["board"] for row in loaded] == ["stale_keep_target"]


def test_recent_clear_tombstone_filters_all_stale_cache_from_another_worker():
    app = create_app()
    cache_key = "clear-stale-worker-key-0"
    rows = [
        {
            "board": "stale_clear_a",
            "name": "오래된 갤러리 A",
            "kind": "mini",
            "recommend": 0,
            "visited_at": FIXED_NOW - 10,
        },
        {
            "board": "stale_clear_b",
            "name": "오래된 갤러리 B",
            "kind": None,
            "recommend": 1,
            "visited_at": FIXED_NOW - 20,
        },
    ]
    response = app.test_client(use_cookies=False).post(
        "/recent/clear",
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
    )
    _seed_stale_recent_cache(cache_key, rows)

    with app.test_request_context(
        "/recent",
        headers={"Cookie": _recent_response_cookie_header(response, cache_key)},
    ):
        loaded = recent.load_recent_entries()

    assert loaded == []


def test_touch_with_tombstone_does_not_revive_deleted_gallery(monkeypatch):
    async def board_payload(*args, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", board_payload)
    app = create_app()
    cache_key = "touch-tombstone-key-000"
    deleted_row = {
        "board": "touch_deleted_target",
        "name": "삭제한 갤러리",
        "kind": "minor",
        "recommend": 0,
        "visited_at": FIXED_NOW - 10,
    }
    delete_response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": deleted_row["board"], "kind": "minor", "recommend": "0"},
        headers=_same_origin_headers(_recent_cookie_header([deleted_row], cache_key)),
    )
    _seed_stale_recent_cache(cache_key, [deleted_row])

    visit_response = app.test_client(use_cookies=False).get(
        "/board",
        query_string={"board": "new_visit", "kind": "mini", "gallery_name": "새 방문"},
        headers={"Cookie": _recent_response_cookie_header(delete_response, cache_key)},
    )

    cookie_rows = _decode_recent_rows(_cookie_morsel(visit_response, recent.RECENT_COOKIE_NAME).value)
    assert [row["board"] for row in cookie_rows] == ["new_visit"]
    assert [row["board"] for row in recent.get_recent_server_cache(cache_key)] == ["new_visit"]


def test_recent_remove_without_kind_preserves_kind_specific_entry():
    app = create_app()
    cache_key = "asymmetric-remove-key-00"
    rows = [
        {
            "board": "shared_board",
            "name": "미니 갤러리",
            "kind": "mini",
            "recommend": 0,
            "visited_at": FIXED_NOW - 10,
        },
    ]
    recent.set_recent_server_cache(cache_key, rows)

    response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": "shared_board", "kind": "", "recommend": "0"},
        headers=_same_origin_headers(_recent_cookie_header(rows, cache_key)),
    )

    remaining = _decode_recent_rows(_cookie_morsel(response, recent.RECENT_COOKIE_NAME).value)
    assert [row["kind"] for row in remaining] == ["mini"]
    assert [row["kind"] for row in recent.get_recent_server_cache(cache_key)] == ["mini"]


def test_recent_gallery_reappears_when_revisited_after_deletion(monkeypatch):
    async def board_payload(*args, **kwargs):
        return [], []

    monkeypatch.setattr(routes, "_load_board_payload", board_payload)
    app = create_app()
    cache_key = "revisit-after-delete-key"
    row = {
        "board": "revisited_target",
        "name": "다시 방문할 갤러리",
        "kind": "minor",
        "recommend": 0,
        "visited_at": FIXED_NOW - 10,
    }
    delete_response = app.test_client(use_cookies=False).post(
        "/recent/remove",
        data={"board": row["board"], "kind": "minor", "recommend": "0"},
        headers=_same_origin_headers(_recent_cookie_header([row], cache_key)),
    )
    monkeypatch.setattr(recent.time, "time", lambda: FIXED_NOW + 10)

    visit_response = app.test_client(use_cookies=False).get(
        "/board",
        query_string={
            "board": row["board"],
            "kind": "minor",
            "gallery_name": row["name"],
        },
        headers={"Cookie": _recent_response_cookie_header(delete_response, cache_key)},
    )

    cookie_rows = _decode_recent_rows(_cookie_morsel(visit_response, recent.RECENT_COOKIE_NAME).value)
    assert [(item["board"], item["visited_at"]) for item in cookie_rows] == [
        (row["board"], FIXED_NOW + 10),
    ]
    assert [item["board"] for item in recent.get_recent_server_cache(cache_key)] == [row["board"]]


def _decode_tombstone_cookie(encoded):
    return recent.normalize_recent_tombstones(
        recent._unpack_tombstone_wire(_decode_recent_rows(encoded))
    )


def test_all_recent_items_keep_tombstones_within_cookie_budget():
    app = create_app()
    rows = [
        {
            "board": f"deleted_board_{index:02d}",
            "name": f"삭제 대상 {index}",
            "kind": "minor",
            "recommend": 0,
            "visited_at": FIXED_NOW - 10,
        }
        for index in range(recent.RECENT_MAX_ITEMS)
    ]
    tombstones = {"cleared_at": 0.0, "items": []}
    for row in rows:
        tombstones["items"].insert(0, {
            "board_hash": recent._tombstone_board_digest(row["board"]),
            "kind": row["kind"],
            "recommend": row["recommend"],
            # 실제 time.time()처럼 소수부가 긴 타임스탬프로 크기를 검증한다.
            "deleted_at": FIXED_NOW + 0.4992871,
        })
        tombstones = recent.normalize_recent_tombstones(tombstones)

    with app.test_request_context("/recent", base_url="http://localhost"):
        response = app.make_response("")
        recent.save_recent_tombstone_cookie(response, tombstones)

    encoded = _cookie_morsel(response, recent.RECENT_TOMBSTONE_COOKIE_NAME).value
    saved = _decode_tombstone_cookie(encoded)
    assert recent.RECENT_TOMBSTONE_MAX_ITEMS == recent.RECENT_MAX_ITEMS
    assert len(saved["items"]) == recent.RECENT_MAX_ITEMS
    assert len(encoded.encode("ascii")) <= recent.RECENT_COOKIE_MAX_BYTES
    assert recent.filter_tombstoned_rows(rows, saved) == []


def test_large_tombstones_fit_cookie_budget_and_keep_newest(monkeypatch):
    app = create_app()
    rows = [
        {
            "board": f"{index:02d}" + "x" * 78,
            "kind": "minor",
            "recommend": index % 2,
            "visited_at": FIXED_NOW - 100,
        }
        for index in range(recent.RECENT_MAX_ITEMS)
    ]
    items = [
        {
            "board_hash": recent._tombstone_board_digest(row["board"]),
            "kind": row["kind"],
            "recommend": row["recommend"],
            "deleted_at": FIXED_NOW - index + 0.4992871,
        }
        for index, row in enumerate(rows)
    ]

    with app.test_request_context("/recent", base_url="http://localhost"):
        response = app.make_response("")
        recent.save_recent_tombstone_cookie(
            response,
            {"cleared_at": 0.0, "items": items},
        )

    encoded = _cookie_morsel(response, recent.RECENT_TOMBSTONE_COOKIE_NAME).value
    saved = _decode_tombstone_cookie(encoded)
    assert len(saved["items"]) == recent.RECENT_MAX_ITEMS
    assert len(encoded.encode("ascii")) <= recent.RECENT_COOKIE_MAX_BYTES
    assert recent.filter_tombstoned_rows(rows, saved) == []

    monkeypatch.setattr(recent, "RECENT_COOKIE_MAX_BYTES", 500)
    with app.test_request_context("/recent", base_url="http://localhost"):
        compact_response = app.make_response("")
        recent.save_recent_tombstone_cookie(
            compact_response,
            {"cleared_at": 0.0, "items": items},
        )

    compact_encoded = _cookie_morsel(
        compact_response,
        recent.RECENT_TOMBSTONE_COOKIE_NAME,
    ).value
    compact_saved = _decode_tombstone_cookie(compact_encoded)
    assert 0 < len(compact_saved["items"]) < len(items)
    assert len(compact_encoded.encode("ascii")) <= recent.RECENT_COOKIE_MAX_BYTES
    assert [item["board_hash"] for item in compact_saved["items"]] == [
        item["board_hash"] for item in items[:len(compact_saved["items"])]
    ]


def test_tombstone_digest_matching_preserves_asymmetric_kind_rule():
    board = "digest_kind_target"
    kindless_row = {
        "board": board,
        "kind": None,
        "recommend": 0,
        "visited_at": FIXED_NOW - 10,
    }
    kindful_row = {**kindless_row, "kind": "mini"}
    tombstone = {
        "board_hash": recent._tombstone_board_digest(board),
        "kind": "minor",
        "recommend": 0,
        "deleted_at": FIXED_NOW,
    }

    assert recent.filter_tombstoned_rows([kindless_row], {
        "cleared_at": 0.0,
        "items": [tombstone],
    }) == []
    assert recent.filter_tombstoned_rows([kindful_row], {
        "cleared_at": 0.0,
        "items": [{**tombstone, "kind": None}],
    }) == [kindful_row]


def test_revisit_at_same_time_as_tombstone_survives():
    rows = [
        {
            "board": "same_time_removed",
            "kind": "minor",
            "recommend": 0,
            "visited_at": FIXED_NOW,
        },
        {
            "board": "same_time_cleared",
            "kind": "mini",
            "recommend": 0,
            "visited_at": FIXED_NOW,
        },
    ]
    tombstones = {
        "cleared_at": FIXED_NOW,
        "items": [
            {
                "board_hash": recent._tombstone_board_digest("same_time_removed"),
                "kind": "minor",
                "recommend": 0,
                "deleted_at": FIXED_NOW,
            }
        ],
    }

    assert recent.filter_tombstoned_rows(rows, tombstones) == rows


def test_recent_remove_csrf_origin_contract():
    app = create_app()
    client = app.test_client(use_cookies=False)

    rejected = client.post(
        "/recent/remove",
        data={"board": "csrf_target"},
        headers={"Origin": "https://evil.example"},
    )
    accepted = client.post(
        "/recent/remove",
        data={"board": "csrf_target"},
        headers={"Origin": "http://localhost"},
    )
    missing_headers = client.post(
        "/recent/remove",
        data={"board": "csrf_target"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 302
    assert missing_headers.status_code == 403
