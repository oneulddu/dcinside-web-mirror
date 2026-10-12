import base64
import io
import json
from pathlib import Path
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import create_app, routes
from app.services import dc_write as service

ORIGIN = {"Origin": "http://localhost"}
BODY = dict(board="programming", subject="제목", body="첫 줄\n둘째 줄 <script>", nickname="ㅇㅇ", password="pw")


class FakeManager:
    def __init__(self):
        self.calls = []
        self.deadlines = []
        self.result = {"ok": True, "data": {"result": "true"}}
        self.gallery_result = {"ok": True, "data": {"heads": [{"no": 0, "name": "일반", "level": 0}], "no_write": False, "captcha": False}}
        self.vote_info_result = {"ok": True, "data": {"down": False, "captcha": True}}

    def request(self, op, deadline=None, **payload):
        self.calls.append((op, payload))
        self.deadlines.append(deadline)
        result = {"gallery_info": self.gallery_result, "vote_info": self.vote_info_result}.get(op, self.result)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def setup(monkeypatch):
    app = create_app()
    app.config.update(TESTING=True, WRITE_KEY="비밀-key", SECRET_KEY="test-secret")
    manager = FakeManager()
    monkeypatch.setattr(service, "get_manager", lambda: manager)
    monkeypatch.setattr(service, "_unlock_failures", service.deque())
    monkeypatch.setattr(service, "_last_success", {})
    monkeypatch.setattr(service, "_vote_options", {})
    contexts = []

    def render(template, **context):
        contexts.append((template, context))
        return "stand-in template"

    monkeypatch.setattr(routes, "render_template", render)

    async def metadata(*args, **kwargs):
        kwargs["pagination_collector"]["gallery_name"] = "프로그래밍"
        return [], [{"head_id": "1", "label": "일반"}]

    monkeypatch.setattr(routes, "_load_board_payload", metadata)
    return app, app.test_client(), manager, contexts


def unlock(client, **kwargs):
    return client.post("/write/unlock", data={"key": "비밀-key", "next": "/write?board=programming", **kwargs}, headers=ORIGIN)


def post(client, path="/write", **changes):
    return client.post(path, json={**BODY, **changes}, headers=ORIGIN)


@pytest.mark.parametrize("method,path", [("get", "/write"), ("post", "/write"), ("get", "/write/unlock"), ("post", "/write/unlock"), ("post", "/write/lock"), ("post", "/read/vote"), ("get", "/write/captcha")])
def test_unconfigured(setup, method, path):
    app, client, manager, _ = setup
    app.config["WRITE_KEY"] = ""
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    assert not manager.calls


def test_lock_unlock_and_key_rotation(setup):
    app, client, manager, contexts = setup
    assert client.get("/write?board=programming").status_code == 302
    assert post(client).json == {"ok": False, "code": "locked"}
    assert post(client).status_code == 403
    assert client.get("/write/captcha").status_code == 403
    assert client.post("/read/vote", json={}).status_code == 403
    response = unlock(client)
    assert response.status_code == 303
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Max-Age=15552000" in cookie
    assert "비밀-key" not in cookie
    assert client.get("/write?board=programming&head_id=0").status_code == 200
    template, context = contexts[-1]
    assert template == "write.html"
    assert set(context) == {"board", "kind", "gallery_name", "head_categories", "selected_head_id", "board_back_url", "limits", "write_blocked"}
    assert context["gallery_name"] == "프로그래밍" and context["selected_head_id"] == "0"
    app.config["WRITE_KEY"] = "changed"
    assert post(client).status_code == 403
    assert manager.calls == [("gallery_info", {"gallery": "programming"})]


def test_secure_cookie_and_lock(setup):
    _, client, _, _ = setup
    response = client.post("/write/unlock", base_url="https://localhost", headers={"Origin": "https://localhost"}, data={"key": "비밀-key"})
    assert "Secure" in response.headers["Set-Cookie"]
    response = client.post("/write/lock", data={"next": "/board?board=programming"}, headers=ORIGIN)
    assert response.status_code == 303 and "Max-Age=0" in response.headers["Set-Cookie"]
    assert post(client).status_code == 403


@pytest.mark.parametrize("next_path", ["https://evil.test/", "//evil.test/", "/\\evil.test", "/%2fevil.test", "/%0a/evil.test"])
def test_safe_next(setup, next_path):
    _, client, _, _ = setup
    assert unlock(client, next=next_path).location == "/"


def test_unlock_failure_rate(setup, monkeypatch):
    _, client, _, contexts = setup
    now = [100.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    for _ in range(5):
        assert unlock(client, key="wrong").status_code == 400
        assert contexts[-1][1]["error"] == "wrong"
    assert unlock(client).status_code == 429
    assert contexts[-1] == ("write_unlock.html", {"next": "/write?board=programming", "error": "limited", "unlocked": False})
    now[0] += 60
    assert unlock(client).status_code == 303


@pytest.mark.parametrize("path", ["/write", "/read/vote", "/write/unlock", "/write/lock"])
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.test"}, {"Referer": "https://evil.test/form"}])
def test_cross_origin(setup, path, headers):
    _, client, manager, _ = setup
    unlock(client)
    response = client.post(path, json=BODY, headers=headers)
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert not manager.calls


@pytest.mark.parametrize("field,value", [("board", "../x"), ("board", ""), ("board", None), ("kind", []), ("kind", "bad"), ("subject", " "), ("subject", "x" * 41), ("subject", 3), ("body", " "), ("body", "x" * 10001), ("nickname", "x" * 21), ("nickname", None), ("password", "x"), ("password", "x" * 21), ("captcha_key", "oops"), ("captcha_code", "answer"), ("head_id", "bad")])
def test_validation(setup, field, value):
    _, client, manager, _ = setup
    unlock(client)
    response = post(client, **{field: value})
    expected = "board" if field in {"kind", "head_id"} else "captcha" if field.startswith("captcha_") else field
    assert response.status_code == 400
    assert response.json["code"] == "invalid" and response.json["field"] == expected
    assert any("가" <= c <= "힣" for c in response.json["message"])
    assert not manager.calls


def test_json_required_and_pid_validation(setup):
    _, client, manager, _ = setup
    unlock(client)
    for path in ("/write", "/read/vote"):
        assert client.post(path, data=BODY, headers=ORIGIN).status_code == 400
        assert client.post(path, json=[], headers=ORIGIN).status_code == 400
        assert client.post(path, data="{", content_type="application/json", headers=ORIGIN).status_code == 400
    for pid in (None, True, -1, 1.5, "abc", "1" * 16):
        assert post(client, "/read/vote", pid=pid).json["field"] == "board"
    assert not manager.calls


def test_success_payload_and_interval(setup, monkeypatch):
    _, client, manager, _ = setup
    unlock(client)
    now = [100.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    manager.result["data"]["no"] = 123
    response = post(client, kind="minor", subject=" 제목 ", head_id="1", head_name="일반")
    assert response.json == {"ok": True, "board_url": "/board?board=programming&kind=minor&refresh=1", "read_url": "/read?board=programming&pid=123&kind=minor"}
    op, payload = manager.calls[0]
    assert op == "write" and payload["session"] == {"type": "anonymous", "nickname": "ㅇㅇ", "password": "pw"}
    assert payload["body"] == BODY["body"] and payload["subject"] == "제목"
    assert payload["head"] == {"no": 1, "name": "일반"}
    assert len(manager.calls) == 1  # POST passes the head through without a metadata request.
    response = post(client)
    assert response.status_code == 429 and response.json["retry_after"] == 20
    assert response.headers["Retry-After"] == "20" and len(manager.calls) == 1
    now[0] += 20
    assert post(client).status_code == 200 and len(manager.calls) == 2


def test_vote_interval_independent(setup, monkeypatch):
    _, client, manager, _ = setup
    unlock(client)
    now = [100.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    assert post(client).json["ok"]
    assert post(client, "/read/vote", pid=123).json == {"ok": True, "message": "추천했어요."}
    response = post(client, "/read/vote", pid=124)
    assert response.status_code == 429 and response.json["retry_after"] == 3
    now[0] += 3
    assert post(client, "/read/vote", pid=124).json["ok"]


def test_downvote_uses_its_own_op_and_message(setup):
    _, client, manager, _ = setup
    unlock(client)
    assert post(client, "/read/vote", pid=123, direction="down").json == {"ok": True, "message": "비추천했어요."}
    assert manager.calls[-1] == ("downvote", {"gallery": "programming", "no": 123, "captcha": None})
    assert post(client, "/read/vote", pid=124, direction="sideways").json["field"] == "direction"
    assert len(manager.calls) == 1
    service._last_success.clear()
    manager.result = service.WorkerUnavailable(sent=True)
    response = post(client, "/read/vote", pid=125, direction="down")
    assert response.status_code == 503 and response.json["code"] == "uncertain"
    assert response.json["message"].startswith("비추천 결과를")


@pytest.mark.parametrize("result,code,status", [
    ({"ok": False, "error": {"name": "ApiError", "cause": "권한 오류"}}, "rejected", 200),
    ({"ok": False, "error": {"name": "ApiError", "cause": ""}}, "rejected", 200),
    ({"ok": True, "data": {"result": "false", "cause": "이미 추천함"}}, "rejected", 200),
    ({"ok": False, "error": {"name": "ApiError", "cause": "잠시후 다시 이용해주세요."}}, "retry", 429),
    # 워커가 디시와 통신한 뒤의 알 수 없는 실패는 이미 반영됐을 수 있으므로 결과 불명이다.
    ({"ok": False, "error": {"name": "HTTPError", "status": 503}}, "uncertain", 503),
    ({"ok": True, "data": {}}, "uncertain", 503),
    (service.WorkerUnavailable(), "unavailable", 503),
    (service.WorkerUnavailable(sent=True), "uncertain", 503),
])
@pytest.mark.parametrize("path", ["/write", "/read/vote"])
def test_worker_mapping(setup, result, code, status, path):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = result
    response = post(client, path, pid=123)
    assert response.status_code == status and response.json["code"] == code
    if code == "uncertain":
        assert response.json["message"] == (
            "디시에 보냈지만 결과를 확인하지 못했어요. 다시 보내기 전에 목록을 확인해 주세요."
            if path == "/write" else "추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.")
    assert len(manager.calls) == 1
    if code == "rejected":
        cause = result.get("error", result.get("data", {})).get("cause")
        if path == "/write":
            assert response.json["message"] == (cause or "디시에서 글을 받지 않았어요.")
        else:
            assert response.json["message"] == "디시가 추천을 받지 않았어요." + (f" 디시 안내: {cause}" if cause else "")
        assert "cause" not in response.json
    assert response.headers["Cache-Control"] == "no-store"
    assert service._last_success == {}


@pytest.mark.parametrize("result", [
    {"ok": False, "error": {"name": "ApiError", "cause": "추천할 수 없습니다.", "vpn_ban": True}},
    {"ok": True, "data": {"result": False, "cause": "추천할 수 없습니다.", "carrier_vpn_ban": True}},
])
def test_vote_rejected_for_server_ip_is_explained(setup, result):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = result
    response = post(client, "/read/vote", pid=123)
    assert response.json == {"ok": False, "code": "rejected", "reason": "ip_blocked",
                             "message": "디시가 이 미러 서버의 접속 위치(IP)를 막아서 받지 않았어요."}


def test_vote_outcome_is_logged_without_identity(setup, caplog):
    app, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "ApiError", "cause": "추천할 수 없습니다."}}
    with caplog.at_level("INFO", logger=app.logger.name):
        post(client, "/read/vote", pid=123, direction="down")
    assert "dc downvote board=programming no=123 code=rejected reason=- cause=추천할 수 없습니다." in caplog.text


def test_vote_options_are_read_once_per_gallery(setup):
    _, client, manager, _ = setup
    assert client.get("/read/vote/options?board=programming&pid=1").json == {"ok": False, "code": "locked"}
    unlock(client)
    response = client.get("/read/vote/options?board=programming&pid=12")
    assert response.json == {"ok": True, "down": False, "captcha": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/read/vote/options?board=programming&pid=34").json["down"] is False
    assert manager.calls == [("vote_info", {"gallery": "programming", "no": 12})]
    assert client.get("/read/vote/options?board=programming&pid=0").status_code == 400
    assert client.get("/read/vote/options?board=../x&pid=1").status_code == 400
    # 비추천을 끈 갤러리로 확인됐으면 디시에 보내지 않는다.
    response = post(client, "/read/vote", pid=12, direction="down")
    assert response.json["reason"] == "down_disabled" and len(manager.calls) == 1
    assert post(client, "/read/vote", pid=12, direction="up").json["ok"] is True


@pytest.mark.parametrize("result", [
    {"ok": False, "error": {"name": "ApiError", "cause": "삭제된 글"}},
    {"ok": True, "data": {"down": "false", "captcha": True}},
    service.WorkerUnavailable(),
])
def test_vote_options_failure_is_not_cached(setup, result):
    _, client, manager, _ = setup
    unlock(client)
    manager.vote_info_result = result
    assert client.get("/read/vote/options?board=programming&pid=1").status_code == 503
    manager.vote_info_result = {"ok": True, "data": {"down": True, "captcha": False}}
    assert client.get("/read/vote/options?board=programming&pid=1").json == {"ok": True, "down": True, "captcha": False}


@pytest.mark.parametrize("path,kind", [("/write", "article"), ("/read/vote", "recommend")])
def test_captcha_new_key_and_retry_payload(setup, path, kind):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "CaptchaRequiredError", "cause": "보안 코드 입력"}}
    response = post(client, path, pid=123, captcha_key="a" * 16, captcha_code="answer")
    assert response.status_code == 200 and response.json["code"] == "captcha_required"
    captcha = response.json["captcha"]
    assert service.CAPTCHA_KEY.fullmatch(captcha["key"]) and captcha["key"] != "a" * 16
    assert captcha["image_url"] == f'/write/captcha?kind={kind}&board=programming&key={captcha["key"]}'
    assert manager.calls[0][1]["captcha"] == {"key": "a" * 16, "code": "answer"}


@pytest.mark.parametrize("content_type,size,status", [("image/png", 10, 200), ("image/jpeg", 204800, 200), ("image/png", 204801, 502), ("text/html", 10, 502), ("image/png\r\nX-Injected: true", 10, 502), ("image/png", 0, 502)])
def test_captcha_proxy(setup, content_type, size, status):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {"content_type": content_type, "base64": base64.b64encode(b"x" * size).decode()}}
    response = client.get("/write/captcha?kind=article&board=programming&key=" + "a" * 16)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Content-Type-Options"] == "nosniff"
    if status == 200:
        assert response.data == b"x" * size and response.content_type == content_type


@pytest.mark.parametrize("sent", [False, True])
def test_captcha_invalid_and_failure(setup, sent):
    _, client, manager, _ = setup
    unlock(client)
    assert client.get("/write/captcha?kind=login&board=x&key=" + "a" * 16).status_code == 400
    assert client.get("/write/captcha?kind=article&board=../x&key=" + "a" * 16).status_code == 400
    assert not manager.calls
    manager.result = service.WorkerUnavailable(sent=sent)
    assert client.get("/write/captcha?kind=article&board=x&key=" + "a" * 16).status_code == 502
    manager.result = {"ok": True, "data": {"content_type": "image/png", "base64": "!!!"}}
    assert client.get("/write/captcha?kind=article&board=x&key=" + "a" * 16).status_code == 502


def test_metadata_failure_and_no_recent_cookie(setup, monkeypatch):
    _, client, _, contexts = setup
    unlock(client)

    async def fail(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr(routes, "_load_board_payload", fail)
    response = client.get("/write?board=programming")
    assert response.status_code == 200
    assert contexts[-1][1]["head_categories"] == [{"head_id": "0", "label": "일반"}]
    assert contexts[-1][1]["gallery_name"] == "programming"
    assert "Set-Cookie" not in response.headers


def test_context_globals(setup):
    app, client, _, _ = setup
    with app.test_request_context("/write"):
        assert routes.dc_write_context() == {"write_configured": True, "write_enabled": False}
    unlock(client)
    cookie = client.get_cookie(service.COOKIE).value
    with app.test_request_context("/write", headers={"Cookie": f"mirror_write={cookie}"}):
        assert routes.dc_write_context() == {"write_configured": True, "write_enabled": True}
    with app.test_request_context("/poker"):
        assert routes.dc_write_context() == {"write_configured": False, "write_enabled": False}


@pytest.fixture
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node unavailable; transport tests need a local fake worker")
    return executable


@pytest.mark.parametrize("failure", ["process.exit(1)", "console.log('bad-json')", "console.log('x'.repeat(400001))", "console.log(JSON.stringify({id: 'wrong', ok: true, data: {}}))", "await new Promise(r => setTimeout(r, 1000))"])
def test_manager_restarts_without_replay(tmp_path, node, failure):
    worker = tmp_path / "worker.mjs"
    worker.write_text("import {createInterface} from 'node:readline';\nfor await (const line of createInterface({input:process.stdin})) {" + failure + ";}\n")
    manager = service.WorkerManager(node, tmp_path, timeout=0.1)
    try:
        with pytest.raises(service.WorkerUnavailable) as error:
            manager.request("ping")
        assert error.value.sent is True
        assert manager._process is None
        worker.write_text("import {createInterface} from 'node:readline';\nfor await (const line of createInterface({input:process.stdin})) {const r=JSON.parse(line);console.log(JSON.stringify({id:r.id,ok:true,data:{ready:true}}));}\n")
        manager.timeout = 3
        assert manager.request("ping")["data"]["ready"] is True
    finally:
        manager.close()


def test_manager_serializes(tmp_path, node):
    (tmp_path / "worker.mjs").write_text("import {createInterface} from 'node:readline';\nfor await (const line of createInterface({input:process.stdin})) {const r=JSON.parse(line);await new Promise(r=>setTimeout(r,5));console.log(JSON.stringify({id:r.id,ok:true,data:{value:r.value}}));}\n")
    manager = service.WorkerManager(node, tmp_path, timeout=3)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda value: manager.request("ping", value=value)["data"]["value"], range(8)))
        assert results == list(range(8))
    finally:
        manager.close()


def test_success_interval_atomic(setup):
    app, _, manager, _ = setup
    manager.request = lambda *a, **kw: (time.sleep(0.02) or {"ok": True, "data": {"result": "true"}})

    def send(_):
        with app.app_context():
            return service.perform("write")[1]

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(send, range(3)))
    assert sorted(statuses) == [200, 429, 429]


def test_worker_protocol_with_fake_library(node):
    from pathlib import Path
    import subprocess

    subprocess.run([node, "--test", str(Path(__file__).parent / "javascript/dc_app_worker.test.cjs")],
                   check=True, capture_output=True, text=True, timeout=20)


def test_write_settings_come_from_server_environment(tmp_path):
    # 운영 서버는 .env/PM2 환경변수로만 키와 Node 경로를 준다.
    import os
    import subprocess
    import sys

    code = (
        "from app import create_app; a = create_app(); "
        "print(a.config['WRITE_KEY'], a.config['DC_APP_NODE'], a.config['DC_APP_STATE'])"
    )
    env = dict(os.environ, MIRROR_ENV="development", MIRROR_WRITE_KEY=" 서버키 ",
               MIRROR_DC_APP_NODE="/opt/node", MIRROR_DC_APP_STATE=str(tmp_path / "c.json"))
    output = subprocess.run([sys.executable, "-c", code], env=env, cwd=os.getcwd(),
                            capture_output=True, text=True, check=True).stdout.split()
    assert output == ["서버키", "/opt/node", str(tmp_path / "c.json")]


@pytest.mark.parametrize("selected,expected", [("0", "0"), ("2", "2"), ("10", None), ("1", None), ("99", None)])
@pytest.mark.parametrize("blocked", [False, True])
def test_gallery_info_heads_and_write_blocked(setup, monkeypatch, selected, expected, blocked):
    _, client, manager, contexts = setup
    metadata_calls = []

    async def metadata(*args, **kwargs):
        metadata_calls.append((args, kwargs))
        kwargs["pagination_collector"]["gallery_name"] = "프로그래밍"
        return [], [{"head_id": "1", "label": "목록 전용"}]

    monkeypatch.setattr(routes, "_load_board_payload", metadata)
    manager.gallery_result = {"ok": True, "data": {"heads": [
        {"no": 0, "name": "일반", "level": 0},
        {"no": 10, "name": "기록", "level": 9},
        {"no": 2, "name": "질문", "level": -1},
    ], "no_write": blocked, "captcha": True}}
    unlock(client)
    response = client.get(f"/write?board=programming&head_id={selected}")
    assert response.status_code == 200
    context = contexts[-1][1]
    assert context["head_categories"] == [{"head_id": "0", "label": "일반"}, {"head_id": "2", "label": "질문"}]
    assert context["write_blocked"] is blocked
    assert context["selected_head_id"] == expected
    assert context["gallery_name"] == "프로그래밍"
    assert manager.calls == [("gallery_info", {"gallery": "programming"})]
    assert len(metadata_calls) == 1
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("result", [
    service.WorkerUnavailable(),
    service.WorkerUnavailable(sent=True),
    {"ok": False, "error": {"name": "ApiError", "cause": "잠시후 다시 이용해주세요."}},
    {"ok": True, "data": {"heads": [], "no_write": False}},
])
def test_gallery_info_failure_or_no_heads(setup, result):
    _, client, manager, contexts = setup
    manager.gallery_result = result
    unlock(client)
    response = client.get("/write?board=programming&head_id=1")
    assert response.status_code == 200
    context = contexts[-1][1]
    assert context["head_categories"] == []
    assert context["write_blocked"] is False
    assert context["selected_head_id"] is None
    assert context["gallery_name"] == "프로그래밍"


@pytest.mark.parametrize("cause", ["잠시후 다시 이용해주세요.", "잠시 후 다시 시도해주세요."])
@pytest.mark.parametrize("path", ["/write", "/read/vote"])
@pytest.mark.parametrize("is_error", [True, False])
def test_upstream_throttle_retries_after_30_seconds(setup, cause, path, is_error):
    _, client, manager, _ = setup
    manager.result = ({"ok": False, "error": {"name": "ApiError", "cause": cause}} if is_error
                      else {"ok": True, "data": {"result": "false", "cause": cause}})
    unlock(client)
    response = post(client, path, pid=123)
    assert response.status_code == 429
    assert response.json == {"ok": False, "code": "retry", "message": cause, "retry_after": 30}
    assert response.headers["Retry-After"] == "30"
    assert service._last_success == {}


@pytest.mark.parametrize("op", ["write", "upvote", "downvote"])
def test_action_lock_deadline_does_not_send(setup, monkeypatch, op):
    app, _, manager, _ = setup

    class HeldLock:
        def acquire(self, *, timeout):
            assert timeout == 40
            return False

        def release(self):
            pytest.fail("Must not release another request's lock")

    monkeypatch.setattr(service, "_action_lock", HeldLock())
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    with app.app_context():
        body, status = service.perform(op)
    assert status == 503 and body["code"] == "unavailable"
    assert not manager.calls


def test_action_lock_wait_uses_original_deadline(setup, monkeypatch):
    app, _, manager, _ = setup
    now = [100.0]
    released = []

    class DelayedLock:
        def acquire(self, *, timeout):
            assert timeout == 40
            now[0] += 15
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(service, "_action_lock", DelayedLock())
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    with app.app_context():
        assert service.perform("write")[1] == 200
    assert manager.deadlines == [140.0]
    assert released == [True]


@pytest.mark.parametrize("explicit_deadline", [False, True])
def test_worker_lock_deadline_does_not_send(monkeypatch, explicit_deadline):
    manager = service.WorkerManager(node="unused", timeout=0.01)
    process = object()
    manager._process = process
    manager._lock.acquire()
    started = time.monotonic()
    try:
        kwargs = {"deadline": started + 0.01} if explicit_deadline else {}
        with pytest.raises(service.WorkerUnavailable) as error:
            manager.request("write", **kwargs)
        assert error.value.sent is False
        assert manager._process is process  # The in-flight request is untouched.
        assert time.monotonic() - started < 1
    finally:
        manager._process = None
        manager._lock.release()
        manager.close()


def test_worker_expired_deadline_never_starts(monkeypatch):
    manager = service.WorkerManager(node="unused")
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **kw: pytest.fail("Must not start worker"))
    try:
        with pytest.raises(service.WorkerUnavailable) as error:
            manager.request("write", deadline=time.monotonic() - 1)
        assert error.value.sent is False
    finally:
        manager.close()


def test_worker_start_failure_is_not_sent(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("cannot start")

    manager = service.WorkerManager(node="unused")
    monkeypatch.setattr(service.subprocess, "Popen", fail)
    try:
        with pytest.raises(service.WorkerUnavailable) as error:
            manager.request("write")
        assert error.value.sent is False
        assert manager._process is None
    finally:
        manager.close()


def test_worker_partial_send_failure_is_uncertain(monkeypatch):
    from types import SimpleNamespace

    manager = service.WorkerManager(node="unused")
    manager._process = SimpleNamespace(poll=lambda: None, stdin=SimpleNamespace(fileno=lambda: 123))
    writes = []

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def register(self, *args):
            pass

        def select(self, timeout):
            return [True]

    def write(fd, data):
        writes.append(bytes(data))
        if len(writes) == 1:
            return 1
        raise BrokenPipeError()

    monkeypatch.setattr(service.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(service.os, "write", write)
    monkeypatch.setattr(manager, "_discard", lambda: setattr(manager, "_process", None))
    try:
        with pytest.raises(service.WorkerUnavailable) as error:
            manager.request("write")
        assert error.value.sent is True
        assert len(writes) == 2 and writes[1] == writes[0][1:]
        assert manager._process is None
    finally:
        manager.close()


def test_readonly_worker_calls_have_short_deadlines(setup, monkeypatch):
    _, client, manager, _ = setup
    unlock(client)
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    assert client.get("/write?board=programming").status_code == 200
    manager.result = {"ok": True, "data": {"content_type": "image/png", "base64": base64.b64encode(b"image").decode()}}
    assert client.get("/write/captcha?kind=article&board=programming&key=" + "a" * 16).status_code == 200
    assert [op for op, _ in manager.calls] == ["gallery_info", "captcha"]
    assert manager.deadlines == [110.0, 110.0]


PNG = b"\x89PNG\r\n\x1a\nfirst"
JPEG = b"\xff\xd8\xffsecond"


def multipart(client, files=(), *, headers=None, **changes):
    return client.post("/write", data={
        "payload": json.dumps({**BODY, **changes}),
        "images": [(io.BytesIO(data), "private-original.png", "text/plain") for data in files],
    }, content_type="multipart/form-data", headers=ORIGIN if headers is None else headers)


@pytest.mark.parametrize("position", ["top", "bottom"])
def test_multipart_image_order_permissions_and_cleanup(setup, tmp_path, monkeypatch, position):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    observed = []

    def inspect(op, deadline=None, **payload):
        assert op == "write" and payload["image_position"] == position
        assert deadline == 155.0
        assert payload["body"] == BODY["body"]
        for index, (image, data, extension, content_type) in enumerate(zip(
                payload["images"], [PNG, JPEG, b"GIF87a", b"GIF89a", b"RIFF1234WEBP"],
                ["png", "jpg", "gif", "gif", "webp"],
                ["image/png", "image/jpeg", "image/gif", "image/gif", "image/webp"]), 1):
            path = Path(image["path"])
            assert path.is_absolute() and path.parent.parent == tmp_path
            assert path.suffix == f".{extension}" and "private-original" not in path.name
            assert path.read_bytes() == data
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            assert image["type"] == content_type and image["name"] == f"image{index}.{extension}"
            observed.append(path)
        return manager.result

    monkeypatch.setattr(manager, "request", inspect)
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    response = multipart(client, [PNG, JPEG, b"GIF87a", b"GIF89a", b"RIFF1234WEBP"], image_position=position)
    assert response.status_code == 200 and response.json["ok"]
    assert len(observed) == 5
    assert all(not path.parent.exists() for path in observed)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("files,limit,message", [
    ([PNG, b"text"], None, "2번째 파일은 JPG, PNG, GIF, WEBP 사진만 올릴 수 있어요."),
    ([b""], None, "1번째 파일은 JPG, PNG, GIF, WEBP 사진만 올릴 수 있어요."),
    ([b"RIFFshort", b"GIFwrong"], None, "1번째 파일은 JPG, PNG, GIF, WEBP 사진만 올릴 수 있어요."),
    ([PNG] * 11, None, "사진은 10장까지 올릴 수 있어요."),
    ([JPEG, PNG], ("image_max_bytes", len(PNG) - 1), "2번째 사진은 10MB까지 올릴 수 있어요."),
    ([PNG, JPEG], ("image_total_bytes", len(PNG) + len(JPEG) - 1), service.IMAGE_TOTAL_MESSAGE),
])
def test_invalid_uploads_cleanup_before_worker(setup, tmp_path, monkeypatch, files, limit, message):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    if limit:
        monkeypatch.setitem(service.LIMITS, *limit)
    response = multipart(client, files)
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "images", "message": message}
    assert not manager.calls and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("body", ["", " \n\t "])
@pytest.mark.parametrize("images", [False, True])
def test_image_only_body(setup, body, images):
    _, client, manager, _ = setup
    unlock(client)
    response = multipart(client, [PNG] if images else [], body=body)
    assert response.status_code == (200 if images else 400)
    if images:
        assert manager.calls[0][1]["body"] == body
        assert manager.calls[0][1]["image_position"] == "top"
    else:
        assert response.json["field"] == "body" and not manager.calls


def test_photo_markers_place_images_between_paragraphs(setup):
    _, client, manager, _ = setup
    unlock(client)
    body = "첫 문단\r\n\n[사진 2]\n둘째 문단\n  [사진1]  \n"
    response = multipart(client, [PNG, JPEG, PNG], body=body, image_position="bottom")
    assert response.status_code == 200
    payload = manager.calls[0][1]
    assert payload["body"] == body
    assert payload["blocks"] == [{"text": "첫 문단"}, {"image": 1}, {"text": "둘째 문단"}, {"image": 0}, {"image": 2}]


@pytest.mark.parametrize("body,message", [
    ("[사진 3]", "본문의 [사진 3]에 맞는 사진이 없어요. 그 줄을 지우거나 사진을 넣어 주세요."),
    ("[사진 1]\n가\n[사진 1]", "본문에 [사진 1] 줄이 두 번 있어요. 하나만 남겨 주세요."),
])
def test_bad_photo_markers_are_rejected_before_upload(setup, tmp_path, body, message):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    response = multipart(client, [PNG, PNG], body=body)
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "body", "message": message}
    assert not manager.calls and list(tmp_path.iterdir()) == []


def test_marker_text_without_images_stays_text(setup):
    _, client, manager, _ = setup
    unlock(client)
    assert post(client, body="[사진 1]").json["ok"]
    assert "blocks" not in manager.calls[0][1]


@pytest.mark.parametrize("body", [None, 3, "x" * 10001])
def test_images_do_not_relax_body_type_or_maximum(setup, body):
    _, client, manager, _ = setup
    unlock(client)
    response = multipart(client, [PNG], body=body)
    assert response.status_code == 400 and response.json["field"] == "body"
    assert not manager.calls


@pytest.mark.parametrize("position", [None, "middle", [], {}, 1])
@pytest.mark.parametrize("encoding", ["json", "multipart"])
def test_invalid_image_position(setup, position, encoding):
    _, client, manager, _ = setup
    unlock(client)
    response = (post if encoding == "json" else multipart)(client, image_position=position)
    assert response.status_code == 400 and response.json["field"] == "images"
    assert not manager.calls


@pytest.mark.parametrize("payload", [None, "[]", "null", "1", '"text"', "{"])
def test_multipart_requires_object_payload(setup, payload):
    _, client, manager, _ = setup
    unlock(client)
    data = {"images": (io.BytesIO(PNG), "photo.png")}
    if payload is not None:
        data["payload"] = payload
    response = client.post("/write", data=data, content_type="multipart/form-data", headers=ORIGIN)
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "board", "message": "JSON 형식으로 요청해 주세요."}
    assert not manager.calls


def test_multipart_rejects_unknown_file_field(setup):
    _, client, manager, _ = setup
    unlock(client)
    response = client.post("/write", data={"payload": json.dumps(BODY), "other": (io.BytesIO(PNG), "x.png")}, headers=ORIGIN)
    assert response.status_code == 400 and response.json["field"] == "images"
    assert not manager.calls


@pytest.mark.parametrize("gate", ["unconfigured", "locked", "missing_origin", "foreign_origin"])
def test_multipart_write_gates_run_before_body_parsing(setup, gate):
    app, client, manager, _ = setup
    headers = ORIGIN
    if gate == "unconfigured":
        app.config["WRITE_KEY"] = ""
    elif gate != "locked":
        unlock(client)
        headers = {} if gate == "missing_origin" else {"Origin": "https://evil.test"}
    # Parsing this body would raise 413; the access gate must reject it first.
    app.config["MAX_CONTENT_LENGTH"] = 1
    response = multipart(client, [PNG], headers=headers)
    assert response.status_code == (404 if gate == "unconfigured" else 403)
    assert not manager.calls


@pytest.mark.parametrize("encoding", ["json", "multipart"])
def test_write_413_is_json_and_other_endpoints_keep_default(setup, encoding):
    app, client, manager, _ = setup
    assert app.config["MAX_CONTENT_LENGTH"] == 32 * 1024 * 1024
    unlock(client)
    app.config["MAX_CONTENT_LENGTH"] = 10
    response = post(client) if encoding == "json" else multipart(client, [PNG])
    assert response.status_code == 413
    assert response.json == {"ok": False, "code": "invalid", "field": "images", "message": service.IMAGE_TOTAL_MESSAGE}
    assert response.headers["Cache-Control"] == "no-store"
    other = post(client, "/read/vote", pid=123)
    assert other.status_code == 413 and other.mimetype == "text/html"
    assert not manager.calls


@pytest.mark.parametrize("result,code", [
    (service.WorkerUnavailable(sent=True), "uncertain"),
    (service.WorkerUnavailable(), "unavailable"),
    ({"ok": False, "error": {"name": "WorkerError", "sent": False}}, "unavailable"),
    ({"ok": False, "error": {"name": "ApiError", "cause": "권한 오류"}}, "rejected"),
    (RuntimeError("fake worker failure"), None),
])
def test_upload_cleanup_on_failed_or_exceptional_perform(setup, tmp_path, monkeypatch, result, code):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    manager.result = result
    original = manager.request
    observed = []

    def inspect(op, **payload):
        observed.extend(Path(image["path"]) for image in payload["images"])
        assert all(path.is_file() for path in observed)
        return original(op, **payload)

    monkeypatch.setattr(manager, "request", inspect)
    if code is None:
        with pytest.raises(RuntimeError, match="fake worker failure"):
            multipart(client, [PNG])
    else:
        assert multipart(client, [PNG]).json["code"] == code
    assert observed and all(not path.parent.exists() for path in observed)
    assert list(tmp_path.iterdir()) == []


def test_upload_cleanup_when_rate_limited(setup, tmp_path):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    assert post(client).json["ok"]
    assert multipart(client, [PNG]).status_code == 429
    assert len(manager.calls) == 1 and list(tmp_path.iterdir()) == []


def test_image_deadline_includes_action_lock_wait(setup, monkeypatch):
    app, _, manager, _ = setup
    now, released = [100.0], []

    class DelayedLock:
        def acquire(self, *, timeout):
            assert timeout == 55
            now[0] += 15
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(service, "_action_lock", DelayedLock())
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    with app.app_context():
        assert service.perform("write", images=[{"path": "fake"}])[1] == 200
    assert manager.deadlines == [155.0] and released == [True]


def test_image_limits_in_write_context(setup):
    _, client, _, contexts = setup
    unlock(client)
    assert client.get("/write?board=programming").status_code == 200
    limits = contexts[-1][1]["limits"]
    assert limits["image_max_count"] == 10
    assert limits["image_max_bytes"] == 10 * 1024 * 1024
    assert limits["image_total_bytes"] == 30 * 1024 * 1024

def test_worker_env_routes_only_worker_through_relay(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("https_proxy", "http://other:1")
    env = service.worker_env("/state.json", "http://relay:secret@100.64.0.2:8899")
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://relay:secret@100.64.0.2:8899"
    assert "NO_PROXY" not in env and "https_proxy" not in env
    plain = service.worker_env("/state.json")
    assert not set(plain) & set(service._PROXY_ENV) and plain["MIRROR_DC_APP_STATE"] == "/state.json"


@pytest.mark.parametrize("path", ["/write", "/read/vote"])
def test_offline_relay_is_reported_as_not_sent(setup, monkeypatch, path):
    app, client, manager, _ = setup
    app.config["DC_APP_PROXY"] = "http://relay:secret@100.64.0.2:8899"
    monkeypatch.setattr(service, "relay_reachable", lambda proxy: False)
    unlock(client)
    response = post(client, path, pid=123)
    assert response.status_code == 503
    assert response.json["code"] == "unavailable" and response.json["message"] == service.RELAY_DOWN
    assert manager.calls == []
    monkeypatch.setattr(service, "relay_reachable", lambda proxy: True)
    assert post(client, path, pid=124).json["ok"] is True


def test_relay_reachable_uses_proxy_host_and_port(monkeypatch):
    seen = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def connect(address, timeout):
        seen.append((address, timeout))
        if address[1] == 1:
            raise OSError()
        return Connection()

    monkeypatch.setattr(service.socket, "create_connection", connect)
    assert service.relay_reachable("http://relay:secret@100.64.0.2:8899") is True
    assert service.relay_reachable("http://relay:secret@100.64.0.2:1") is False
    assert service.relay_reachable("not a url") is False
    assert seen[0] == (("100.64.0.2", 8899), 2)
