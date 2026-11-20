import json
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import render_template

from app import create_app
from app.services import dc_write, read_sync

URL = "/read-state/sync"
ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def browser(tmp_path):
    app = create_app()
    path = tmp_path / "private" / "read.json"
    app.config.update(TESTING=True, SECRET_KEY="test", WRITE_KEY="key", VISITOR_CHECK=False,
                      PUBLIC_BASE_URL=None, READ_SYNC_STATE=path)
    client = app.test_client()
    assert client.post("/write/unlock", data={"key": "key"}, headers=ORIGIN).status_code == 303
    return SimpleNamespace(app=app, client=client, path=path,
                           sync=lambda since, items, **cursor: client.post(
                               URL, json={"since": since, "items": items, **cursor}, headers=ORIGIN))


def test_merge_delta_and_noop(browser):
    initial = browser.sync(0, {}).json
    state_id = initial["id"]
    assert initial == {"ok": True, "id": state_id, "rev": 0, "reset": False, "items": {}}
    assert json.loads(browser.path.read_text())["id"] == state_id
    assert browser.sync(0, {"A_b|1": 4, "poker:99": 0}).json["rev"] == 2
    assert stat.S_IMODE(browser.path.stat().st_mode) == 0o600
    assert browser.sync(2, {"A_b|1": 3, "poker:99": 5}, id=state_id).json == {
        "ok": True, "id": state_id, "rev": 3, "reset": False, "items": {"poker:99": 5}}
    before = browser.path.stat()
    assert browser.sync(3, {"A_b|1": 4}, id=state_id).json["items"] == {}
    assert browser.path.stat().st_ino == before.st_ino
    assert browser.sync(0, {}).json["items"] == {"A_b|1": 4, "poker:99": 5}


def test_ahead_revision_resets_before_incoming_merge(browser):
    state_id = browser.sync(0, {}).json["id"]
    assert browser.sync(1, {"board|2": 2}, id=state_id).json == {
        "ok": True, "id": state_id, "rev": 1, "reset": True, "items": {"board|2": 2}}


def test_restored_file_at_same_revision_requests_local_history(browser):
    safari = {f"board|{n}": n for n in range(1, 21)}
    home = {f"poker:{n}": n for n in range(1, 21)}
    old_id = browser.sync(0, safari).json["id"]
    browser.path.unlink()
    restored = browser.sync(0, home).json
    assert restored["rev"] == 20 and restored["id"] != old_id
    for cursor in ({"id": old_id}, {}):  # stale generation and pre-ID client
        assert browser.sync(20, {}, **cursor).json == {
            "ok": True, "id": restored["id"], "rev": 20, "reset": True, "items": home}
    assert browser.sync(20, safari, id=restored["id"]).json["reset"] is False
    assert browser.sync(0, {}).json["items"] == {**safari, **home}


def test_keep_newest_1500_and_clamp_far_future(browser, monkeypatch):
    monkeypatch.setattr(read_sync, "time", SimpleNamespace(time_ns=lambda: 1_800_000_000_000_000_000))
    board = "a" * 80
    marks = {f"{board}|{n}": n for n in range(1, 1501)}
    state_id = browser.sync(0, marks).json["id"]
    assert browser.sync(1500, {"poker:1": 1_800_000_900_000}, id=state_id).json["items"] == {
        "poker:1": 1_800_000_000_000}
    retained = browser.sync(0, {}).json["items"]
    assert len(retained) == 1500 and f"{board}|1" not in retained and f"{board}|2" in retained


@pytest.mark.parametrize("body", [
    None, [], {}, {"since": True, "items": {}}, {"since": -1, "items": {}},
    {"since": 0, "items": []}, {"since": 0, "items": {"a|1": -1}},
    {"since": 0, "items": {"a|1": True}}, {"since": 0, "items": {"a|1": 1.5}},
    *({"since": 0, "items": {key: 1}} for key in [
        "a|0", "a|+1", "a|١", "a|1|2", "a-b|1", "a" * 81 + "|1", "poker:1234567890123", "poker:0"]),
    {"since": 0, "items": {f"a|{n+1}": n for n in range(1501)}},
])
def test_invalid_body_never_writes(browser, body):
    response = browser.client.post(URL, data=json.dumps(body), content_type="application/json", headers=ORIGIN)
    assert (response.status_code, response.json) == (400, {"ok": False, "code": "invalid"})
    assert not browser.path.exists()


@pytest.mark.parametrize("data", [b"{", b" " * (192 * 1024) + b'{"since":0,"items":{}}'], ids=["syntax", "oversize"])
def test_json_reader_rejects_invalid(browser, data):
    response = browser.client.post(URL, data=data, content_type="application/json", headers=ORIGIN)
    assert (response.status_code, response.json) == (400, {"ok": False, "code": "invalid"})


def test_route_gates_and_cache_headers(browser):
    fresh = browser.app.test_client()
    locked = fresh.post(URL)
    assert (locked.status_code, locked.json) == (403, {"ok": False, "code": "locked"})
    assert locked.headers["Cache-Control"] == "no-store" and locked.headers["X-Content-Type-Options"] == "nosniff"
    assert browser.client.post(URL, headers={"Origin": "https://other.example"}).status_code == 403
    browser.app.config["VISITOR_CHECK"] = True
    assert fresh.post(URL).json["code"] == "visitor_check_required"
    browser.app.config.update(VISITOR_CHECK=False, WRITE_KEY="")
    assert fresh.post(URL).status_code == 404
    assert not browser.path.exists()


def test_disabled_sync_hidden_before_visitor_check(browser):
    browser.app.config.update(VISITOR_CHECK=True, WRITE_KEY="")
    assert browser.app.test_client().post(URL).status_code == 404
    assert not browser.path.exists()


def test_only_unlocked_dc_pages_expose_url(browser):
    attr = b'data-read-sync="/read-state/sync"'
    assert attr in browser.client.get("/recent").data
    assert attr not in browser.app.test_client().get("/recent").data
    cookie = browser.client.get_cookie(dc_write.COOKIE)
    with browser.app.test_request_context("/poker", headers={"Cookie": f"{cookie.key}={cookie.value}"}):
        assert attr.decode() not in render_template("base.html")


@pytest.mark.parametrize("contents", [b"{", b"{}", b'{"rev":0,"items":{}}', b'{"id":42,"rev":0,"items":{}}',
                                      b'{"id":"abcdef0123456789abcdef0123456789","rev":1,"items":{"secret|1":[2,0]}}'])
def test_corruption_not_disclosed_or_replaced(browser, contents, caplog):
    browser.path.parent.mkdir()
    browser.path.write_bytes(contents)
    response = browser.sync(0, {"secret|1": 1})
    assert (response.status_code, response.json) == (503, {"ok": False, "code": "unavailable"})
    assert browser.path.read_bytes() == contents
    assert "type=" in caplog.text and "secret" not in caplog.text


def test_symlink_never_used_even_if_target_is_valid(browser, tmp_path):
    target = tmp_path / "target.json"
    browser.path.parent.mkdir()
    browser.path.symlink_to(target)
    assert browser.sync(0, {"a|1": 1}).status_code == 503
    assert not target.exists()
    initial = b'{"id":"abcdef0123456789abcdef0123456789","rev":0,"items":{}}'
    target.write_bytes(initial)
    assert browser.sync(0, {"a|1": 1}).status_code == 503
    assert browser.path.is_symlink() and target.read_bytes() == initial


def test_browser_sync_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    subprocess.run([node, "--test", str(Path(__file__).parent / "javascript/read_sync.test.cjs")],
                   check=True, timeout=30)
