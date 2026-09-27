import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest
from flask import make_response

from app import create_app, routes
from app.services import recent
from app.services.poker_boards import BOARDS


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE", {})
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 0))
    app = create_app()
    app.config["TESTING"] = True

    @app.get("/_recent-rows")
    def rows():
        return {"rows": recent.load_recent_entries()}

    @app.get("/_recent-touch/<board_id>")
    def touch(board_id):
        response = make_response("")
        recent.touch_recent_poker_board(response, board_id)
        return response

    return app


def decode_cookie(client, name=recent.RECENT_COOKIE_NAME):
    return json.loads(base64.urlsafe_b64decode(client.get_cookie(name).value))


@pytest.mark.parametrize("board_id", BOARDS)
def test_recent_poker_helper_records_canonical_row(app, board_id):
    client = app.test_client()
    response = client.get(f"/_recent-touch/{board_id}")
    assert response.status_code == 200
    row, = decode_cookie(client)
    assert row == {
        "board": f"poker:{board_id}", "kind": "poker",
        "name": f"포커고수 {BOARDS[board_id]['label']}", "visited_at": row["visited_at"],
    }
    assert row["visited_at"] > 0
    client.get(f"/_recent-touch/{board_id}")
    assert len(decode_cookie(client)) == 1


@pytest.mark.parametrize("kind", [None, "", "  ", "poker", " POKER "])
def test_recent_poker_restores_empty_kind(app, kind):
    client = app.test_client()
    client.set_cookie(recent.RECENT_COOKIE_NAME, recent._encode_recent_rows([
        {"board": "poker:free", "kind": kind}, {"board": "free", "kind": None},
    ]))
    rows = client.get("/_recent-rows").get_json()["rows"]
    assert [(r["board"], r["kind"]) for r in rows] == [("poker:free", "poker"), ("free", None)]


@pytest.mark.parametrize("board, kind", [
    ("free", "poker"), ("poker:free", "minor"), ("poker:free", "mini"),
    ("poker:free", "person"), ("poker:free", "normal"), ("poker:free", "unknown"),
    ("poker:missing", "poker"), ("poker:missing", None), ("poker:", "poker"),
    ("poker:poker:free", "poker"),
])
def test_recent_poker_invalid_combinations_dropped_and_removal_is_noop(app, board, kind):
    client = app.test_client()
    client.set_cookie(recent.RECENT_COOKIE_NAME, recent._encode_recent_rows([
        {"board": board, "kind": kind}, {"board": "free", "name": "DC", "kind": None},
    ]))
    assert [r["board"] for r in client.get("/_recent-rows").get_json()["rows"]] == ["free"]
    assert client.post("/recent/remove", data={"board": board, "kind": kind or ""},
                       headers={"Origin": "http://localhost"}).status_code == 302
    assert client.get_cookie(recent.RECENT_TOMBSTONE_COOKIE_NAME) is None


@pytest.mark.parametrize("target, target_kind, survivor", [
    ("poker:free", "poker", "free"), ("free", "", "poker:free"),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_recent_poker_removal_and_stale_worker_tombstones_are_isolated(
    app, monkeypatch, target, target_kind, survivor, legacy,
):
    now = [1800000000.0]
    monkeypatch.setattr(recent.time, "time", lambda: now[0])
    client = app.test_client()
    rows = [
        {"board": "free", "kind": None, "name": "DC 이름", "visited_at": now[0] - 10},
        {"board": "poker:free", "kind": None if legacy else "poker", "visited_at": now[0] - 9},
    ]
    cache_key = "poker-recent-worker-0001"
    client.set_cookie(recent.RECENT_COOKIE_NAME, recent._encode_recent_rows(rows))
    client.set_cookie(recent.RECENT_CACHE_KEY_COOKIE_NAME, cache_key)
    recent.replace_recent_server_cache(cache_key, rows)
    assert client.post("/recent/remove", data={"board": target, "kind": target_kind},
                       headers={"Origin": "http://localhost"}).status_code == 302
    assert [r["board"] for r in decode_cookie(client)] == [survivor]
    tombstones = recent.normalize_recent_tombstones(recent._unpack_tombstone_wire(
        decode_cookie(client, recent.RECENT_TOMBSTONE_COOKIE_NAME),
    ))
    assert tombstones["items"][0]["board_hash"] == recent._tombstone_board_digest(target)
    assert recent.filter_tombstoned_rows(rows, tombstones) == [r for r in rows if r["board"] == survivor]
    # Another worker still has the pre-deletion generation.
    recent.replace_recent_server_cache(cache_key, rows)
    assert [r["board"] for r in client.get("/_recent-rows").get_json()["rows"]] == [survivor]
    now[0] += 1
    client.get("/_recent-touch/free")
    actual = client.get("/_recent-rows").get_json()["rows"]
    assert [r["board"] for r in actual] == (["poker:free", "free"] if target_kind else ["poker:free"])


def test_recent_poker_compaction_restores_name_without_dc_lookup(app, monkeypatch):
    rows = [
        {"board": "free", "name": "DC 이름", "kind": None, "visited_at": 2},
        {"board": "poker:free", "name": "포커고수 자유 게시판", "kind": "poker", "visited_at": 1},
    ]
    compact = [rows[0], {k: v for k, v in rows[1].items() if k != "name"}]
    monkeypatch.setattr(recent, "RECENT_COOKIE_MAX_BYTES", len(recent._encode_recent_rows(compact)))
    encoded = recent._fit_recent_cookie_value(rows)
    assert json.loads(base64.urlsafe_b64decode(encoded)) == compact
    assert rows[1]["name"] == "포커고수 자유 게시판"

    def unexpected_lookup():
        pytest.fail("Poker name restoration must not query DC galleries")

    monkeypatch.setattr(routes, "get_heung_galleries", unexpected_lookup)
    with app.test_request_context(headers={"Cookie": f"{recent.RECENT_COOKIE_NAME}={encoded}"}):
        dc, poker = routes._build_recent_items(recent.load_recent_entries())
    assert poker["display_name"] == poker["gallery_name"] == "포커고수 자유 게시판"
    assert poker["href"] == "/poker/free?page=1"
    assert poker["display_id"] == "free"
    assert poker["kind_label"] == "포커고수"
    assert poker["board"] == "poker:free"
    assert dc["display_name"] == "DC 이름"
    assert dc["display_id"] == "free"
    assert parse_qs(urlparse(dc["href"]).query) == {
        "board": ["free"], "recommend": ["0"], "page": ["1"], "gallery_name": ["DC 이름"],
    }


def test_recent_poker_mixed_dc_lookup_links_and_home_limit(app, monkeypatch):
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([
        {"board_id": "free", "name": "DC 자유", "board_kind": "minor"},
    ], 0))
    monkeypatch.setattr(routes, "render_template", lambda template, **context: {"items": context["recent_items"]})
    rows = [{"board": "free", "kind": "minor", "visited_at": 100}] + [
        {"board": f"poker:{board_id}", "kind": "poker", "visited_at": 90 - i}
        for i, board_id in enumerate(BOARDS)
    ]
    client = app.test_client()
    client.set_cookie(recent.RECENT_COOKIE_NAME, recent._encode_recent_rows(rows))
    items = client.get("/recent").get_json()["items"]
    assert [i["board"] for i in items] == [r["board"] for r in rows]
    assert items[0]["display_name"] == "DC 자유"
    assert parse_qs(urlparse(items[0]["href"]).query)["kind"] == ["minor"]
    assert items[1]["display_name"] == "포커고수 자유 게시판"
    assert all(i["href"] and i["display_id"] for i in items)
    assert client.get("/").get_json()["items"] == items[:8]
    monkeypatch.setattr(routes, "RECENT_MAX_ITEMS", 3)
    assert client.get("/recent").get_json()["items"] == items[:3]


def test_recent_poker_touch_preserves_max_items_and_clear(app, monkeypatch):
    monkeypatch.setattr(recent, "RECENT_MAX_ITEMS", 3)
    client = app.test_client()
    for board in ("free", "best", "hand", "news"):
        client.get(f"/_recent-touch/{board}")
    assert [r["board"] for r in decode_cookie(client)] == ["poker:news", "poker:hand", "poker:best"]
    stale_rows = decode_cookie(client)
    key = client.get_cookie(recent.RECENT_CACHE_KEY_COOKIE_NAME).value
    assert client.post("/recent/clear", headers={"Origin": "http://localhost"}).status_code == 302
    recent.replace_recent_server_cache(key, stale_rows)
    assert client.get("/_recent-rows").get_json()["rows"] == []
