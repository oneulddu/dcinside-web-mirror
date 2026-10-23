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
    monkeypatch.delenv("MIRROR_DC_LOGIN_ID", raising=False)
    monkeypatch.delenv("MIRROR_DC_LOGIN_PASSWORD", raising=False)
    monkeypatch.delenv("MIRROR_DC_LOGIN_ID_2", raising=False)
    monkeypatch.delenv("MIRROR_DC_LOGIN_PASSWORD_2", raising=False)
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
    assert set(context) == {"board", "kind", "gallery_name", "head_categories", "selected_head_id", "board_back_url", "limits", "write_blocked", "write_notice"}
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
    response = post(client, path, **({"pid": 123} if path == "/read/vote" else {}))
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


def test_vote_options_are_cached_per_post(setup):
    _, client, manager, _ = setup
    assert client.get("/read/vote/options?board=programming&pid=1").json == {"ok": False, "code": "locked"}
    unlock(client)
    response = client.get("/read/vote/options?board=programming&pid=12")
    assert response.json == {"ok": True, "down": False, "captcha": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/read/vote/options?board=programming&pid=12").json["down"] is False
    assert manager.calls == [("vote_info", {"gallery": "programming", "no": 12})]
    # 비추천은 말머리마다 꺼질 수 있어 같은 갤러리의 다른 글은 따로 읽는다.
    manager.vote_info_result = {"ok": True, "data": {"down": True, "captcha": False}}
    assert client.get("/read/vote/options?board=programming&pid=34").json["down"] is True
    assert len(manager.calls) == 2
    assert client.get("/read/vote/options?board=programming&pid=0").status_code == 400
    assert client.get("/read/vote/options?board=../x&pid=1").status_code == 400
    # 비추천을 끈 글로 확인됐으면 디시에 보내지 않는다. 다른 글의 비추천은 그대로 보낸다.
    response = post(client, "/read/vote", pid=12, direction="down")
    assert response.json["reason"] == "down_disabled" and len(manager.calls) == 2
    assert post(client, "/read/vote", pid=34, direction="down").json["ok"] is True
    service._last_success.clear()
    assert post(client, "/read/vote", pid=12, direction="up").json["ok"] is True


def test_vote_options_cache_is_bounded(setup, monkeypatch):
    _, client, manager, _ = setup
    monkeypatch.setattr(service, "VOTE_OPTIONS_MAX", 3)
    unlock(client)
    for pid in range(1, 6):
        client.get(f"/read/vote/options?board=programming&pid={pid}")
    assert list(service._vote_options) == [("programming", 3), ("programming", 4), ("programming", 5)]


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
    # 인물 갤러리 추천은 앱 보안 문자를 그대로 쓴다.
    extra = {"kind": "person", "pid": 123} if path == "/read/vote" else {}
    response = post(client, path, captcha_key="a" * 16, captcha_code="answer", **extra)
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
    assert client.get("/write/captcha?kind=unknown&board=x&key=" + "a" * 16).status_code == 400
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
        assert routes.dc_write_context() == {"write_configured": True, "write_enabled": False, "account_configured": False, "account_slots": [], "write_limits": service.LIMITS}
    unlock(client)
    cookie = client.get_cookie(service.COOKIE).value
    with app.test_request_context("/write", headers={"Cookie": f"mirror_write={cookie}"}):
        assert routes.dc_write_context() == {"write_configured": True, "write_enabled": True, "account_configured": False, "account_slots": [], "write_limits": service.LIMITS}
    with app.test_request_context("/poker"):
        assert routes.dc_write_context() == {"write_configured": False, "write_enabled": False, "account_configured": False, "account_slots": [], "write_limits": service.LIMITS}


ACCOUNT_STATUS = {"configured": True, "logged_in": True, "user_id": "public-user",
                  "name": "고정닉", "blocked": False, "reason": None}
ACCOUNT_ROUTES = [("get", "/write/account"), ("get", "/write/account/status"),
                  ("post", "/write/account/login")]


@pytest.mark.parametrize("method,path", ACCOUNT_ROUTES)
@pytest.mark.parametrize("gate", ["disabled", "locked", "origin"])
def test_account_route_gates(setup, method, path, gate):
    app, client, manager, _ = setup
    if gate == "disabled":
        app.config["WRITE_KEY"] = ""
    elif gate == "origin":
        unlock(client)
    response = getattr(client, method)(path, json={}, headers={
        "Origin": "https://elsewhere.test", "Sec-Fetch-Site": "cross-site",
    })
    assert response.status_code == (404 if gate == "disabled" else 403)
    assert response.headers["Cache-Control"] == "no-store"
    assert not manager.calls


def test_account_page_opens_from_address_bar(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": ACCOUNT_STATUS}
    response = client.get("/write/account", headers={"Sec-Fetch-Site": "none"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path,site", [(path, site) for path in ("/write/account", "/write/account/status")
                                       for site in ("cross-site", "same-site", "")]
                         + [("/write/account/status", "none")])
def test_account_get_rejects_non_same_origin_fetch_metadata(setup, path, site):
    _, client, manager, _ = setup
    unlock(client)
    response = client.get(path, headers={"Sec-Fetch-Site": site})
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert not manager.calls


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://elsewhere.test"}, {"Referer": "https://elsewhere.test/form"}])
def test_account_login_requires_same_origin(setup, headers):
    _, client, manager, _ = setup
    unlock(client)
    assert client.post("/write/account/login", json={}, headers=headers).status_code == 403
    assert not manager.calls


def test_account_status_page_and_context_are_allowlisted(setup, monkeypatch):
    app, client, manager, contexts = setup
    monkeypatch.setenv("MIRROR_DC_LOGIN_ID", "secret-login-id")
    assert not service.account_configured()
    monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD", "secret-login-password")
    assert service.account_configured()
    with app.test_request_context("/write"):
        assert routes.dc_write_context()["account_configured"] is True
    unlock(client)
    manager.result = {"ok": True, "data": {**ACCOUNT_STATUS, "id": "secret-login-id",
                                          "password": "secret-login-password", "otp_token": "secret-token",
                                          "accounts": {"1": {**ACCOUNT_STATUS, "password": "secret"},
                                                       "2": {**ACCOUNT_STATUS, "id": "secret"},
                                                       "3": ACCOUNT_STATUS}}}
    expected = {"ok": True, **ACCOUNT_STATUS, "accounts": {"1": ACCOUNT_STATUS, "2": ACCOUNT_STATUS}}
    response = client.get("/write/account/status", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.json == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/write/account").status_code == 200
    assert contexts[-1] == ("write_account.html", {"account": expected})
    assert manager.calls == [("account_status", {}), ("account_status", {})]


@pytest.mark.parametrize("reason", ["otp", "captcha", "failed", "expired"])
def test_account_login_failure_contract_and_worker_cooldown(setup, reason):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "LoginNeeded", "reason": reason, "sent": False,
                                           "cause": "secret-login-id secret-login-password", "captcha_key": "a" * 16}}
    response = client.post("/write/account/login", json={}, headers=ORIGIN)
    expected = {"ok": False, "code": "login_needed", "reason": reason, "account": "1",
                "account_url": "/write/account#account-1", "message": service.LOGIN_MESSAGES[reason]}
    if reason == "captcha":
        expected["captcha_key"] = "a" * 16
    assert response.json == expected
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert manager.calls == [("account_login", {"account": "1", "otp": None, "captcha": None})]
    manager.result = {"ok": False, "error": {"name": "LoginCooldown", "retry_after": 10, "sent": False}}
    limited = client.post("/write/account/login", json={}, headers=ORIGIN)
    assert limited.status_code == 429
    assert limited.json == {"ok": False, "code": "retry", "message": "잠시 후 다시 시도해 주세요.", "retry_after": 10}
    assert limited.headers["Retry-After"] == "10"
    assert len(manager.calls) == 2
    manager.result = {"ok": True, "data": {"result": True, **ACCOUNT_STATUS, "password": "secret-login-password"}}
    response = client.post("/write/account/login", json={"otp": "123456", "captcha": {"key": "b" * 16, "code": " ab "},
                                                       "id": "do-not-forward", "password": "do-not-forward"}, headers=ORIGIN)
    assert response.json == {"ok": True, "account": "1", **ACCOUNT_STATUS}
    assert manager.calls[-1] == ("account_login", {"account": "1", "otp": "123456", "captcha": {"key": "b" * 16, "code": "ab"}})
    # Even an immediate second success must reach the worker: Python owns no login clock.
    assert client.post("/write/account/login", json={}, headers=ORIGIN).json["ok"]
    assert len(manager.calls) == 4
    assert "account_login" not in service._last_success


@pytest.mark.parametrize("data,field", [
    ({"otp": "１２３４５６"}, "otp"), ({"otp": 123456}, "otp"), ({"otp": "12345"}, "otp"),
    ({"otp": ""}, "otp"), ({"captcha": {}}, "captcha"), ({"captcha": "x"}, "captcha"),
    ({"captcha": {"key": "bad", "code": "a"}}, "captcha"),
    ({"captcha": {"key": "a" * 16, "code": ""}}, "captcha"),
])
def test_account_login_validation_never_calls_worker(setup, data, field):
    _, client, manager, _ = setup
    unlock(client)
    response = client.post("/write/account/login", json=data, headers=ORIGIN)
    assert response.status_code == 400 and response.json["field"] == field
    assert not manager.calls


def test_login_captcha_image_without_gallery(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {"content_type": "image/png", "base64": "AQID"}}
    response = client.get("/write/captcha?kind=login&key=" + "a" * 16)
    assert response.status_code == 200 and response.data == b"\x01\x02\x03"
    assert response.headers["Cache-Control"] == "no-store"
    assert manager.calls == [("captcha", {"kind": "login", "key": "a" * 16, "gallery": ""})]


ACCOUNT_ACTIONS = [
    ("/write", "write", {"subject": "제목", "body": "본문"}),
    ("/read/vote", "upvote", {"pid": 123}),
    ("/read/vote", "downvote", {"pid": 123, "direction": "down"}),
    ("/read/comment", "comment", {"pid": 123, "content": "댓글"}),
    ("/read/delete", "delete", {"pid": 123}),
    ("/read/comment/delete", "comment_delete", {"pid": 123, "comment_id": 456}),
    ("/write/modify/load", "modify_info", {"pid": 123}),
    ("/write", "modify", {"pid": 123, "subject": "제목", "body": "수정", "fingerprint": "a" * 64}),
]


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS)
def test_account_actions_use_only_session_marker(setup, path, op, fields):
    _, client, manager, _ = setup
    unlock(client)
    data = {"result": True, "data": "456", "author": {"name": "고정닉", "user_id": "public-user", "password": "secret"}}
    if op == "modify_info":
        data = MODIFY_INFO
    manager.result = {"ok": True, "data": data}
    response = client.post(path, json={"board": "programming", "mode": "account", **fields}, headers=ORIGIN)
    assert response.status_code == 200 and response.json["ok"]
    assert manager.calls[0][0] == op
    assert manager.calls[0][1]["session"] == {"type": "login", "account": "1"}
    assert "password" not in response.get_data(as_text=True)
    if op == "comment":
        assert response.json["author"] == {"name": "고정닉", "user_id": "public-user"}
        assert response.json["comment_id"] == 456
    if op == "modify_info":
        assert response.json["mode"] == "account"
        assert response.json["account"] == "1"


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS + [("/write/account/login", "account_login", {})])
@pytest.mark.parametrize("seconds", [1, 7, 10])
def test_account_login_cooldown_maps_to_retry_for_every_route(setup, path, op, fields, seconds):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "LoginCooldown", "retry_after": seconds, "sent": False}}
    response = client.post(path, json={"board": "programming", "mode": "account", **fields}, headers=ORIGIN)
    assert response.status_code == 429
    assert response.json == {"ok": False, "code": "retry", "message": "잠시 후 다시 시도해 주세요.",
                             "retry_after": seconds}
    assert response.headers["Retry-After"] == str(seconds)
    assert response.headers["Cache-Control"] == "no-store"
    assert manager.calls[0][0] == op and len(manager.calls) == 1
    assert service._last_success == {}


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS)
@pytest.mark.parametrize("reason", ["otp", "captcha", "failed", "expired"])
def test_account_action_login_needed_is_not_uncertain(setup, path, op, fields, reason, caplog):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "LoginNeeded", "reason": reason, "sent": False,
                                           "cause": "secret-login-id secret-login-password", "captcha_key": "a" * 16}}
    response = client.post(path, json={"board": "programming", "mode": "account", **fields}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json == {"ok": False, "code": "login_needed", "reason": reason,
                             "message": service.LOGIN_MESSAGES[reason], "account": "1",
                             "account_url": "/write/account#account-1"}
    assert len(manager.calls) == 1
    assert "secret-login" not in response.get_data(as_text=True) + caplog.text


@pytest.mark.parametrize("failure", [service.WorkerUnavailable(sent=True),
                                   {"ok": False, "error": {"name": "WorkerError", "cause": "", "status": None}}])
def test_account_mutation_timeout_stays_uncertain(setup, failure):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = failure
    response = post(client, mode="account", nickname=None, password=None)
    assert response.status_code == 503 and response.json["code"] == "uncertain"
    assert len(manager.calls) == 1


def test_account_rejection_does_not_leak_upstream_identity(setup, caplog):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "ApiError", "cause": "secret-login-id secret-login-password"}}
    response = post(client, mode="account")
    assert response.json["code"] == "rejected"
    assert "secret-login" not in response.get_data(as_text=True) + caplog.text


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS + [("/write/account/login", "account_login", {})])
@pytest.mark.parametrize("mode", ["account", "anonymous"])
@pytest.mark.parametrize("returned", [False, True])
def test_update_required_fixed_response_before_account_sanitization(setup, caplog, path, op, fields, mode, returned):
    _, client, manager, _ = setup
    unlock(client)
    failure = {"reason": "update_required", "cause": "secret-login-id secret-login-password 최신 버전으로 업데이트"}
    manager.result = ({"ok": True, "data": {"result": False, **failure}} if returned else
                      {"ok": False, "error": {"name": "WorkerError", "sent": False, **failure}})
    response = post(client, path, mode=mode, **fields)
    assert response.status_code == 200
    assert response.json == {"ok": False, "code": "update_required",
                             "message": "디시가 앱 업데이트를 요구해요. 미러 서버의 앱 버전을 올려야 해요."}
    assert len(manager.calls) == 1 and manager.calls[0][0] == op
    assert service._last_success == {}
    assert "secret-login" not in response.get_data(as_text=True) + caplog.text
    assert "cause" not in response.json and "diag_cause" not in response.json


@pytest.mark.parametrize("returned", [False, True])
def test_update_required_reads_are_visible_and_not_cached(setup, returned):
    app, client, manager, contexts = setup
    unlock(client)
    result = ({"ok": True, "data": {"result": False, "reason": "update_required"}} if returned else
              {"ok": False, "error": {"name": "WorkerError", "reason": "update_required"}})
    manager.vote_info_result = manager.gallery_result = result
    response = client.get("/read/vote/options?board=programming&pid=123")
    assert response.status_code == 200
    assert response.json == {"ok": False, "code": "update_required", "message": service.UPDATE_REQUIRED}
    assert service._vote_options == {}
    response = client.get("/write?board=programming")
    assert response.status_code == 200
    context = contexts[-1][1]
    assert context["write_notice"] == service.UPDATE_REQUIRED
    # Render the real template as well as checking the route's context.
    with app.test_request_context("/write?board=programming"):
        app.update_template_context(context)
        html = app.jinja_env.get_template("write.html").render(**context)
    assert service.UPDATE_REQUIRED in html


@pytest.mark.parametrize("path,op,fields", [action for action in ACCOUNT_ACTIONS if action[1] in service.SENDS])
@pytest.mark.parametrize("returned", [False, True])
def test_account_diag_is_warning_only_not_browser_data_or_message(setup, caplog, path, op, fields, returned):
    _, client, manager, _ = setup
    unlock(client)
    # The worker masks before this boundary; generic cause remains independently sanitized.
    failure = {"cause": "secret-login-id secret-login-password",
               "diag_cause": "DC diagnostic *** *** " + "x" * 300}
    manager.result = ({"ok": True, "data": {"result": False, **failure}} if returned else
                      {"ok": False, "error": {"name": "ApiError", **failure}})
    response = post(client, path, mode="account", **fields)
    assert response.status_code == 200 and response.json["code"] == "rejected"
    text = response.get_data(as_text=True)
    assert "diag_cause" not in text and "DC diagnostic" not in text
    assert "secret-login" not in text + caplog.text
    records = [record for record in caplog.records if "diag=" in record.getMessage()]
    assert len(records) == 1 and records[0].levelname == "WARNING"
    assert records[0].getMessage() == f"dc {op} board=programming no={fields.get('pid', '-')} diag={failure['diag_cause'][:200]!r}"
    assert len(manager.calls) == 1


@pytest.mark.parametrize("op,session", [("account_login", {}), ("modify_info", {"type": "login"}),
                                      ("write", {"type": "anonymous"}), ("write", {"type": "login"})])
def test_diag_is_stripped_even_from_success_and_logged_only_for_account_mutations(setup, caplog, op, session):
    app, _, manager, _ = setup
    manager.result = {"ok": True, "data": {"result": True, "diag_cause": "masked diagnostic"}}
    with app.app_context():
        result, status = service.perform(op, session=session)
    assert status == 200 and result == {"ok": True, "data": {"result": True}}
    assert ("masked diagnostic" in caplog.text) == (op == "write" and session.get("type") == "login")


@pytest.mark.parametrize("reason", ["UPDATE_REQUIRED", "future_category", ["update_required"]])
def test_unrecognized_worker_category_is_not_an_update_rejection(setup, reason):
    app, _, manager, _ = setup
    manager.result = {"ok": False, "error": {"name": "ApiError", "reason": reason, "cause": "private rejection"}}
    with app.app_context():
        result, status = service.perform("upvote", session={"type": "login"})
    assert status == 200 and result["code"] == "rejected"
    assert result["message"] == "디시에서 요청을 받지 않았어요."


ACCOUNT_SELECTOR_ROUTES = [
    *[(path, op, fields, False) for path, op, fields in ACCOUNT_ACTIONS],
    ("/write/account/login", "account_login", {}, False),
    *[(path, op, fields, True) for path, op, fields in ACCOUNT_ACTIONS if path == "/write"],
]


@pytest.mark.parametrize("path,op,fields,as_form", ACCOUNT_SELECTOR_ROUTES)
@pytest.mark.parametrize("selector,expected", [
    ({}, "1"), ({"account": "1"}, "1"), ({"account": "2"}, "2"),
    ({"account": None}, None), ({"account": ""}, None), ({"account": 2}, None),
    ({"account": True}, None), ({"account": "3"}, None), ({"account": {}}, None),
    ({"account": []}, None), ({"account": " 2"}, None),
])
def test_account_selector_contract(setup, monkeypatch, path, op, fields, as_form, selector, expected):
    _, client, manager, _ = setup
    for suffix in ("", "_2"):
        monkeypatch.setenv("MIRROR_DC_LOGIN_ID" + suffix, "private-id" + suffix)
        monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD" + suffix, "private-password" + suffix)
    unlock(client)
    manager.result = {"ok": True, "data": MODIFY_INFO if op == "modify_info" else
                      {"result": True, **ACCOUNT_STATUS}}
    payload = {"board": "programming", "mode": "account", **fields, **selector}
    if as_form:
        # Real form selectors are strings; nonstrings exercise multipart's JSON payload.
        form = {}
        if "account" in selector and isinstance(selector["account"], str):
            form["account"] = payload.pop("account")
        response = client.post(path, data={"payload": json.dumps(payload), **form},
                               content_type="multipart/form-data", headers=ORIGIN)
    else:
        response = client.post(path, json=payload, headers=ORIGIN)
    if expected is None:
        assert response.status_code == 400
        assert response.json == {"ok": False, "code": "invalid", "field": "mode",
                                 "message": "계정을 다시 선택해 주세요."}
        assert manager.calls == []
    else:
        assert response.status_code == 200 and response.json["ok"]
        assert len(manager.calls) == 1 and manager.calls[0][0] == op
        sent = manager.calls[0][1]
        if op == "account_login":
            assert sent["account"] == expected
            assert response.json == {"ok": True, "account": expected, **ACCOUNT_STATUS}
        else:
            assert sent["session"] == {"type": "login", "account": expected}
        if op == "modify_info":
            assert response.json["mode"] == "account" and response.json["account"] == expected


@pytest.mark.parametrize("path,op,fields,as_form", ACCOUNT_SELECTOR_ROUTES)
@pytest.mark.parametrize("slot", ["1", "2"])
def test_explicit_unconfigured_slot_never_calls_worker(setup, monkeypatch, path, op, fields, as_form, slot):
    _, client, manager, _ = setup
    suffix = "" if slot == "1" else "_2"
    # One credential alone is not a configured account.
    monkeypatch.setenv("MIRROR_DC_LOGIN_ID" + suffix, "private-id")
    unlock(client)
    payload = {"board": "programming", "mode": "account", **fields}
    response = (client.post(path, data={"payload": json.dumps(payload), "account": slot},
                            content_type="multipart/form-data", headers=ORIGIN)
                if as_form else client.post(path, json={**payload, "account": slot}, headers=ORIGIN))
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "mode",
                             "message": "계정을 다시 선택해 주세요."}
    assert manager.calls == []


@pytest.mark.parametrize("path,op,fields,as_form", [row for row in ACCOUNT_SELECTOR_ROUTES if row[1] != "account_login"])
@pytest.mark.parametrize("selector", [None, "", 2, True, "3", "2"])
def test_anonymous_ignores_account_selector(setup, path, op, fields, as_form, selector):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": MODIFY_INFO if op == "modify_info" else {"result": True}}
    payload = {**BODY, **fields, "mode": "anonymous", "account": selector}
    response = (client.post(path, data={"payload": json.dumps(payload)}, content_type="multipart/form-data", headers=ORIGIN)
                if as_form else client.post(path, json=payload, headers=ORIGIN))
    assert response.status_code == 200 and response.json["ok"]
    if op in ("upvote", "downvote"):
        assert "session" not in manager.calls[0][1]
    else:
        assert manager.calls[0][1]["session"]["type"] == "anonymous"
        assert "account" not in manager.calls[0][1]["session"]
    assert "account" not in response.json


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS + [("/write/account/login", "account_login", {})])
def test_worker_invalid_account_mapping(setup, path, op, fields):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "AccountInvalid", "sent": False}}
    response = client.post(path, json={"board": "programming", "mode": "account", **fields}, headers=ORIGIN)
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "mode",
                             "message": "계정을 다시 선택해 주세요."}


@pytest.mark.parametrize("path,op,fields", ACCOUNT_ACTIONS + [("/write/account/login", "account_login", {})])
@pytest.mark.parametrize("worker_account,expected", [("2", "2"), ("1", "1"), (2, "1"), ("3", "1"), (None, "1")])
def test_login_needed_uses_validated_worker_account(setup, path, op, fields, worker_account, expected):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "LoginNeeded", "reason": "expired",
                                           "account": worker_account, "sent": False}}
    response = client.post(path, json={"board": "programming", "mode": "account", **fields}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json == {"ok": False, "code": "login_needed", "reason": "expired",
                             "message": service.LOGIN_MESSAGES["expired"], "account": expected,
                             "account_url": "/write/account#account-" + expected}


def test_post_interval_is_shared_between_slots(setup, monkeypatch):
    _, client, manager, _ = setup
    for suffix in ("", "_2"):
        monkeypatch.setenv("MIRROR_DC_LOGIN_ID" + suffix, "private-id")
        monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD" + suffix, "private-password")
    unlock(client)
    assert post(client, mode="account", account="1").json["ok"]
    response = post(client, mode="account", account="2")
    assert response.status_code == 429 and response.json["code"] == "retry"
    assert len(manager.calls) == 1


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
        # 느린 CI에서는 첫 Node 실행만으로 몇 초가 걸린다.
        manager.timeout = 10
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
    response = post(client, path, **({"pid": 123} if path == "/read/vote" else {}))
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
    response = post(client, path, **({"pid": 123} if path == "/read/vote" else {}))
    assert response.status_code == 503
    assert response.json["code"] == "unavailable" and response.json["message"] == service.RELAY_DOWN
    assert manager.calls == []
    monkeypatch.setattr(service, "relay_reachable", lambda proxy: True)
    assert post(client, path, **({"pid": 124} if path == "/read/vote" else {})).json["ok"] is True


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

@pytest.mark.parametrize("kind,galltype,query_kind", [(None, "G", ""), ("minor", "M", "minor"), ("mini", "MI", "mini")])
def test_vote_captcha_uses_pc_session(setup, kind, galltype, query_kind):
    _, client, manager, _ = setup
    unlock(client)
    extra = {"kind": kind} if kind else {}
    manager.result = {"ok": False, "error": {"name": "CaptchaRequiredError", "cause": ""}}
    response = post(client, "/read/vote", pid=123, **extra)
    captcha = response.json["captcha"]
    assert captcha["image_url"] == (f'/write/captcha?kind=vote&board=programming&key={captcha["key"]}'
                                    f'&pid=123&gallery_kind={query_kind}')
    service._last_success.clear()
    manager.result = {"ok": True, "data": {"result": True, "count": 4}}
    response = post(client, "/read/vote", pid=123, direction="down", captcha_key="b" * 16, captcha_code=" ab12 ", **extra)
    assert response.json == {"ok": True, "message": "비추천했어요."}
    assert manager.calls[-1] == ("downvote", {"gallery": "programming", "no": 123, "captcha": None,
                                              "pc": {"key": "b" * 16, "code": "ab12", "galltype": galltype}})


@pytest.mark.parametrize("mode", [None, "", "login", "ACCOUNT", 1, True, {}, []])
def test_vote_invalid_mode_never_calls_worker(setup, mode):
    _, client, manager, _ = setup
    unlock(client)
    response = post(client, "/read/vote", pid=123, mode=mode)
    assert response.status_code == 400
    assert response.json == {"ok": False, "code": "invalid", "field": "mode",
                             "message": "작성 모드를 다시 선택해 주세요."}
    assert manager.calls == []


@pytest.mark.parametrize("kind", [None, "minor", "mini", "person"])
@pytest.mark.parametrize("direction,op,message", [
    ("up", "upvote", "추천했어요."), ("down", "downvote", "비추천했어요."),
])
def test_account_vote_captcha_always_uses_app(setup, monkeypatch, kind, direction, op, message):
    _, client, manager, _ = setup
    monkeypatch.setenv("MIRROR_DC_LOGIN_ID_2", "private-id")
    monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD_2", "private-password")
    unlock(client)
    payload = {"pid": 123, "kind": kind, "direction": direction, "mode": "account", "account": "2"}
    manager.result = {"ok": False, "error": {"name": "CaptchaRequiredError", "cause": ""}}
    response = post(client, "/read/vote", **payload)
    assert response.status_code == 200 and response.json["code"] == "captcha_required"
    captcha = response.json["captcha"]
    assert captcha["image_url"] == f'/write/captcha?kind=recommend&board=programming&key={captcha["key"]}'
    assert service._last_success == {}
    manager.result = {"ok": True, "data": {"result": True}}
    response = post(client, "/read/vote", **payload, captcha_key=captcha["key"], captcha_code=" ab12 ")
    assert response.json == {"ok": True, "message": message}
    assert manager.calls == [
        (op, {"gallery": "programming", "no": 123, "captcha": None,
              "session": {"type": "login", "account": "2"}}),
        (op, {"gallery": "programming", "no": 123, "captcha": {"key": captcha["key"], "code": "ab12"},
              "session": {"type": "login", "account": "2"}}),
    ]


@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("first,second", [
    ("anonymous", "1"), ("1", "2"), ("2", "anonymous"),
])
def test_vote_interval_is_shared_across_identities(setup, monkeypatch, direction, first, second):
    _, client, manager, _ = setup
    for suffix in ("", "_2"):
        monkeypatch.setenv("MIRROR_DC_LOGIN_ID" + suffix, "private-id")
        monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD" + suffix, "private-password")
    unlock(client)
    for index, identity in enumerate((first, second)):
        mode = "anonymous" if identity == "anonymous" else "account"
        response = post(client, "/read/vote", pid=123, direction=direction, mode=mode, account=identity)
        assert response.status_code == (200 if index == 0 else 429)
    assert response.json["code"] == "retry"
    assert len(manager.calls) == 1
    assert set(service._last_success) == {"upvote" if direction == "up" else "downvote"}


@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("failure,code", [
    ({"ok": False, "error": {"name": "CaptchaRequiredError"}}, "captcha_required"),
    ({"ok": False, "error": {"name": "LoginNeeded", "reason": "expired", "account": "2", "sent": False}}, "login_needed"),
    ({"ok": False, "error": {"name": "ApiError", "cause": "rejected"}}, "rejected"),
    ({"ok": False, "error": {"name": "WorkerError", "sent": False}}, "unavailable"),
    (service.WorkerUnavailable(sent=True), "uncertain"),
])
def test_account_vote_failure_does_not_consume_interval(setup, monkeypatch, direction, failure, code):
    _, client, manager, _ = setup
    monkeypatch.setenv("MIRROR_DC_LOGIN_ID_2", "private-id")
    monkeypatch.setenv("MIRROR_DC_LOGIN_PASSWORD_2", "private-password")
    unlock(client)
    manager.result = failure
    response = post(client, "/read/vote", pid=123, direction=direction, mode="account", account="2")
    assert response.json["code"] == code
    if code == "login_needed":
        assert response.json["account"] == "2"
        assert response.json["account_url"] == "/write/account#account-2"
    assert service._last_success == {}
    manager.result = {"ok": True, "data": {"result": True}}
    assert post(client, "/read/vote", pid=123, direction=direction).json["ok"]
    assert len(manager.calls) == 2


def test_pc_vote_captcha_image_route(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {"content_type": "image/png", "base64": base64.b64encode(b"png").decode()}}
    response = client.get("/write/captcha?kind=vote&board=ai_utilize&key=" + "c" * 16 + "&pid=126474&gallery_kind=minor")
    assert response.status_code == 200 and response.data == b"png"
    assert response.headers["Cache-Control"] == "no-store"
    assert manager.calls == [("pc_vote_captcha", {"gallery": "ai_utilize", "no": 126474, "galltype": "M", "key": "c" * 16})]
    for query in ["pid=0&gallery_kind=minor", "pid=1&gallery_kind=person", "gallery_kind=minor", "pid=1&gallery_kind=bad"]:
        assert client.get("/write/captcha?kind=vote&board=ai_utilize&key=" + "c" * 16 + "&" + query).status_code == 400
    assert len(manager.calls) == 1

# ----- 댓글 쓰기 -----

COMMENT = dict(board="programming", pid=123, content="첫 줄\n둘째 줄 <b>", nickname=" ㅇㅇ ", password="pw")


def comment(client, **changes):
    return client.post("/read/comment", json={**COMMENT, **changes}, headers=ORIGIN)


def test_comment_requires_unlock_and_same_origin(setup):
    _, client, manager, _ = setup
    assert comment(client).status_code == 403
    unlock(client)
    assert client.post("/read/comment", json=COMMENT, headers={"Origin": "https://evil.test"}).status_code == 403
    assert not manager.calls


def test_comment_success_payload_and_interval(setup, monkeypatch):
    _, client, manager, _ = setup
    unlock(client)
    now = [100.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    response = comment(client, kind="minor")
    assert response.json == {"ok": True, "message": "댓글을 달았어요."}
    assert response.headers["Cache-Control"] == "no-store"
    assert manager.calls == [("comment", {"gallery": "programming", "no": 123, "content": COMMENT["content"], "parent": None,
                                          "captcha": None, "session": {"type": "anonymous", "nickname": "ㅇㅇ", "password": "pw"}})]
    response = comment(client)
    assert response.status_code == 429 and response.json["retry_after"] == 10
    now[0] += 10
    assert comment(client).json["ok"] and len(manager.calls) == 2


@pytest.mark.parametrize("code,user_id", [("abc_12", "abc_12"), ("118.235", ""), (None, ""), ("", "")])
def test_comment_reply_parent(setup, code, user_id):
    _, client, manager, _ = setup
    unlock(client)
    assert comment(client, parent_id="456", parent_author_code=code).json["ok"]
    assert manager.calls[0][1]["parent"] == {"comment_no": 456, "user_id": user_id}


@pytest.mark.parametrize("field,value,expected", [
    ("pid", None, "board"), ("pid", "0", "board"), ("board", "../x", "board"),
    ("content", " ", "content"), ("content", "x" * 401, "content"), ("content", 3, "content"),
    ("nickname", "", "nickname"), ("nickname", "x" * 21, "nickname"),
    ("password", "x", "password"), ("password", "  ", "password"), ("password", "x" * 21, "password"),
    ("parent_id", "abc", "parent"), ("parent_id", True, "parent"), ("parent_author_code", 5, "parent"),
    ("captcha_key", "oops", "captcha"),
])
def test_comment_validation(setup, field, value, expected):
    _, client, manager, _ = setup
    unlock(client)
    extra = {"parent_id": "1"} if field == "parent_author_code" else {}
    response = comment(client, **{field: value}, **extra)
    assert response.status_code == 400 and response.json["field"] == expected
    assert not manager.calls


def test_comment_outcomes(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "CaptchaRequiredError", "cause": "보안 코드 입력"}}
    response = comment(client, kind="minor", captcha_key="a" * 16, captcha_code=" ab ")
    captcha = response.json["captcha"]
    assert response.json["code"] == "captcha_required"
    assert captcha["image_url"] == f'/write/captcha?kind=comment&board=programming&key={captcha["key"]}'
    assert manager.calls[-1][1]["captcha"] == {"key": "a" * 16, "code": "ab"}
    # 코드를 설정한 갤러리는 보안 코드 없이 보내면 이 문구로 거절한다. 입력창을 띄워야 한다.
    manager.result = {"ok": False, "error": {"name": "ApiError", "cause": "코드가 설정된 갤러리 입니다. 코드를 확인해 주십시오."}}
    response = comment(client)
    assert response.json["code"] == "captcha_required"
    assert response.json["captcha"]["image_url"] == f'/write/captcha?kind=comment&board=programming&key={response.json["captcha"]["key"]}'
    manager.result = {"ok": False, "error": {"name": "ApiError", "cause": "댓글 작성이 제한되었습니다."}}
    assert comment(client).json == {"ok": False, "code": "rejected",
                                    "message": "디시가 댓글을 받지 않았어요. 디시 안내: 댓글 작성이 제한되었습니다."}
    manager.result = service.WorkerUnavailable(sent=True)
    response = comment(client)
    assert response.status_code == 503 and response.json["code"] == "uncertain"
    assert response.json["message"].startswith("댓글을 보냈지만")


def test_comment_captcha_image_kind(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {"content_type": "image/png", "base64": base64.b64encode(b"x").decode()}}
    assert client.get("/write/captcha?kind=comment&board=programming&key=" + "a" * 16).status_code == 200
    assert manager.calls[-1] == ("captcha", {"kind": "comment", "gallery": "programming", "key": "a" * 16})


MUTATION_PATHS = [
    ("/read/delete", "delete"),
    ("/read/comment/delete", "comment_delete"),
    ("/write/modify/load", "modify_info"),
    ("/write", "modify"),
]
MODIFY_INFO = {"result": True, "subject": "제목", "head_id": None, "head_name": None,
               "blocks": [{"kind": "text", "text": "본문"}], "fingerprint": "a" * 64,
               "editable": True, "reason": None}


def mutation_post(client, path, **changes):
    return client.post(path, json={**BODY, "pid": 123, "comment_id": 456,
                                  "fingerprint": "a" * 64, "photos": [], "originals": [], **changes},
                       headers=ORIGIN)


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
def test_mutation_success_and_process_interval(setup, monkeypatch, path, op):
    app, client, manager, _ = setup
    unlock(client)
    now = [100.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    if op == "modify_info":
        manager.result = {"ok": True, "data": MODIFY_INFO}
    response = mutation_post(client, path, kind="minor")
    assert response.status_code == 200 and response.json["ok"]
    assert response.headers["Cache-Control"] == "no-store"
    expected = {
        "delete": {"ok": True, "message": "글을 삭제했어요.", "board_url": "/board?board=programming&kind=minor&refresh=1"},
        "comment_delete": {"ok": True, "message": "댓글을 삭제했어요."},
        "modify_info": {"ok": True, "mode": "anonymous", **{key: MODIFY_INFO[key] for key in ("subject", "head_id", "head_name", "blocks", "fingerprint")}},
        "modify": {"ok": True, "board_url": "/board?board=programming&kind=minor&refresh=1",
                   "read_url": "/read?board=programming&pid=123&kind=minor"},
    }
    assert response.json == expected[op]
    assert manager.calls[0][0] == op
    payload = manager.calls[0][1]
    assert payload["gallery"] == "programming" and payload["no"] == 123
    assert payload["session"] == {"type": "anonymous", "nickname": "ㅇㅇ", "password": "pw"}
    if op == "comment_delete":
        assert payload["comment_no"] == 456
    # A second client in the same process shares the interval.
    second = app.test_client()
    unlock(second)
    response = mutation_post(second, path)
    assert response.status_code == 429
    assert response.json["retry_after"] == service.INTERVALS[op]
    assert response.headers["Retry-After"] == str(service.INTERVALS[op])
    now[0] += service.INTERVALS[op]
    assert mutation_post(client, path).json["ok"]
    assert len(manager.calls) == 2


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
@pytest.mark.parametrize("gate", ["disabled", "locked", "origin", "missing_origin"])
def test_mutation_gates_before_worker(setup, path, op, gate):
    app, client, manager, _ = setup
    if gate == "disabled":
        app.config["WRITE_KEY"] = ""
    elif gate in ("origin", "missing_origin"):
        unlock(client)
    headers = {} if gate == "missing_origin" else {"Origin": "https://evil.test"} if gate == "origin" else ORIGIN
    response = client.post(path, json={}, headers=headers)
    assert response.status_code == (404 if gate == "disabled" else 403)
    assert response.headers["Cache-Control"] == "no-store"
    assert not manager.calls


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
@pytest.mark.parametrize("field,value", [("pid", None), ("pid", True), ("pid", ""), ("pid", 0),
                                        ("pid", "1x"), ("pid", "1" * 16), ("board", "../x"),
                                        ("kind", []), ("password", "x"), ("password", " " * 2),
                                        ("password", None), ("password", "x" * 21)])
def test_mutation_invalid_input_never_creates_or_sends(setup, path, op, field, value):
    _, client, manager, _ = setup
    unlock(client)
    response = mutation_post(client, path, **{field: value})
    assert response.status_code == 400
    assert response.json["field"] == ("password" if field == "password" else "board")
    assert not manager.calls


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
def test_mutation_requires_json(setup, path, op):
    _, client, manager, _ = setup
    unlock(client)
    assert client.post(path, data={"pid": 123}, headers=ORIGIN).status_code == 400
    assert not manager.calls


@pytest.mark.parametrize("value", [None, True, 0, "0", "x", "1" * 16])
def test_comment_delete_invalid_id(setup, value):
    _, client, manager, _ = setup
    unlock(client)
    response = mutation_post(client, "/read/comment/delete", comment_id=value)
    assert response.status_code == 400 and response.json["field"] == "comment_id"
    assert not manager.calls


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
def test_mutation_wrong_password_and_private_logging(setup, caplog, path, op):
    _, client, manager, _ = setup
    unlock(client)
    cause = "비밀번호가 틀립니다." if op == "comment_delete" else "비밀번호 오류"
    manager.result = {"ok": False, "error": {"name": "ApiError", "cause": cause}}
    response = mutation_post(client, path, nickname="private-name", password="private-password",
                             captcha_key="a" * 16, captcha_code="secret")
    assert response.status_code == 200 and response.json["code"] == "rejected"
    assert cause in response.json["message"]
    assert f"dc {op}" in caplog.text
    assert all(secret not in caplog.text for secret in ("private-name", "private-password", "secret"))


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
def test_mutation_captcha_response(setup, path, op):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "CaptchaRequiredError", "cause": "보안 코드 입력"}}
    response = mutation_post(client, path)
    assert response.json["code"] == "captcha_required"
    kind = "comment" if op == "comment_delete" else "article"
    captcha = response.json["captcha"]
    assert captcha["image_url"] == f'/write/captcha?kind={kind}&board=programming&key={captcha["key"]}'


@pytest.mark.parametrize("path,op", MUTATION_PATHS)
@pytest.mark.parametrize("failure", ["sent", "not_sent", "worker"])
def test_mutation_transport_never_replays(setup, path, op, failure):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = ({"ok": False, "error": {"name": "WorkerError"}} if failure == "worker"
                      else service.WorkerUnavailable(sent=failure == "sent"))
    response = mutation_post(client, path)
    assert response.status_code == 503
    assert response.json["code"] == ("unavailable" if op == "modify_info" or failure == "not_sent" else "uncertain")
    assert len(manager.calls) == 1


@pytest.mark.parametrize("reason", ["secret", "poll", "movie", "ai_image"])
def test_modify_load_not_editable(setup, reason):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {**MODIFY_INFO, "editable": False, "reason": reason}}
    response = mutation_post(client, "/write/modify/load")
    assert response.json["ok"] is False
    assert response.json["code"] == "rejected" and response.json["reason"] == "not_editable"
    assert "수정할 수 없어요" in response.json["message"]
    assert "fingerprint" not in response.json and "password" not in response.json


def test_modify_load_proxies_only_allowed_photo_urls(setup):
    _, client, manager, _ = setup
    unlock(client)
    urls = ["https://dcimg1.dcinside.com/viewimage.php?id=1&no=2", "https://evil.test/img.jpg",
            "javascript:alert(1)", None, "https://user:pass@dcimg1.dcinside.com/x"]
    manager.result = {"ok": True, "data": {**MODIFY_INFO, "blocks": [
        {"kind": "photo", "index": index, "url": url} for index, url in enumerate(urls)]}}
    response = mutation_post(client, "/write/modify/load", kind="minor")
    from urllib.parse import parse_qs, urlsplit
    url = urlsplit(response.json["blocks"][0]["url"])
    assert url.path == "/media"
    assert parse_qs(url.query) == {"src": [urls[0]], "board": ["programming"], "pid": ["123"], "kind": ["minor"]}
    assert all(block["url"] is None for block in response.json["blocks"][1:])


def test_modify_get_context_and_invalid_pid(setup):
    _, client, manager, contexts = setup
    unlock(client)
    assert client.get("/write?board=programming&pid=123").status_code == 200
    assert contexts[-1][0] == "write.html" and contexts[-1][1]["modify_pid"] == 123
    manager.calls.clear()
    for pid in ("", "0", "-1", "x", "1" * 16):
        assert client.get(f"/write?board=programming&pid={pid}").status_code == 400
    assert not manager.calls


def test_modify_multipart_interleaves_kept_and_new_blocks_and_cleans_files(setup, tmp_path):
    app, client, manager, _ = setup
    app.config["DC_APP_UPLOADS"] = str(tmp_path)
    unlock(client)
    response = multipart(client, [PNG, JPEG], pid=123, fingerprint="A" * 64,
                         body="앞\n[사진 2]\n[원본 2]\n뒤", photos=[{"keep": 2}, {"new": 1}, {"new": 0}],
                         originals=[1, 3], captcha_key="a" * 16, captcha_code=" ab ")
    assert response.json["ok"]
    op, payload = manager.calls[0]
    assert op == "modify" and payload["no"] == 123 and payload["fingerprint"] == "a" * 64
    assert payload["blocks"] == [{"text": "앞"}, {"image": 1}, {"keep": 3}, {"text": "뒤"},
                                 {"keep": 2}, {"image": 0}, {"keep": 1}]
    assert payload["captcha"] == {"key": "a" * 16, "code": "ab"}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("changes", [
    {"fingerprint": "bad"}, {"fingerprint": None}, {"body": "", "photos": [], "originals": []},
    {"body": "[사진 1]"}, {"body": "[원본 1]"}, {"body": "[사진 10]"},
    {"body": "[사진 1]\n[사진 1]", "photos": [{"keep": 1}]},
    {"body": "[원본 1]\n[원본 1]", "originals": [1]},
    {"photos": None}, {"photos": {}}, {"originals": None}, {"originals": {}},
    {"photos": [{"keep": -1}]}, {"photos": [{"keep": True}]}, {"photos": [{"new": 0}]},
    {"photos": [{"keep": 1, "html": "<b>bad</b>"}]},
    {"photos": [{"keep": 1}, {"keep": 1}]}, {"photos": [{"keep": 1}], "originals": [1]},
    {"originals": [1, 1]}, {"originals": ["1"]}, {"originals": [-1]},
])
def test_modify_invalid_block_input(setup, changes):
    _, client, manager, _ = setup
    unlock(client)
    response = mutation_post(client, "/write", **changes)
    assert response.status_code == 400 and response.json["field"] == "body"
    assert not manager.calls


@pytest.mark.parametrize("photos", [[], [{"new": 1}], [{"new": 0}, {"new": 0}]])
def test_modify_requires_each_upload_exactly_once(setup, photos):
    _, client, manager, _ = setup
    unlock(client)
    response = multipart(client, [PNG], pid=123, fingerprint="a" * 64, photos=photos)
    assert response.status_code == 400 and response.json["field"] == "body"
    assert not manager.calls


@pytest.mark.parametrize("changes,blocks", [
    ({"photos": [{"keep": 2}]}, [{"keep": 2}]),
    ({"originals": [1]}, [{"keep": 1}]),
])
def test_modify_empty_body_with_retained_blocks(setup, changes, blocks):
    _, client, manager, _ = setup
    unlock(client)
    assert mutation_post(client, "/write", body="", **changes).json["ok"]
    assert manager.calls[0][1]["blocks"] == blocks


def test_modify_three_digit_markers_stay_text(setup):
    _, client, manager, _ = setup
    unlock(client)
    response = mutation_post(client, "/write", body="[사진 001]\n[원본 001]",
                             photos=[{"keep": 2}], originals=[1])
    assert response.json["ok"]
    assert manager.calls[0][1]["blocks"] == [
        {"text": "[사진 001]\n[원본 001]"}, {"keep": 2}, {"keep": 1}]


def test_modify_stale_is_rejected_not_uncertain(setup):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": False, "error": {"name": "WorkerError", "cause": "stale", "sent": False}}
    response = mutation_post(client, "/write")
    assert response.json == {"ok": False, "code": "rejected", "reason": "stale",
                             "message": "글이 그사이 바뀌었어요. 수정 화면을 다시 열어 주세요."}
    assert len(manager.calls) == 1


def test_modify_image_deadline(setup, monkeypatch):
    _, client, manager, _ = setup
    unlock(client)
    monkeypatch.setattr(service.time, "monotonic", lambda: 100)
    assert multipart(client, [PNG], pid=123, fingerprint="a" * 64, photos=[{"new": 0}]).json["ok"]
    assert manager.deadlines == [155]


@pytest.mark.parametrize("number,expected", [(1054, 1054), ("1054", 1054), (None, None), (True, None), ("bad", None)])
def test_comment_success_returns_numeric_id(setup, number, expected):
    _, client, manager, _ = setup
    unlock(client)
    manager.result = {"ok": True, "data": {"result": "true", "data": number}}
    response = comment(client)
    assert response.json.get("comment_id") == expected


def test_plain_write_contract_unchanged(setup):
    _, client, manager, contexts = setup
    unlock(client)
    assert client.get("/write?board=programming").status_code == 200
    assert "modify_pid" not in contexts[-1][1]
    manager.calls.clear()
    manager.result = {"ok": True, "data": {"result": "true", "no": 789}}
    response = post(client, body="[원본 1]\n[사진 1]")
    assert response.json["read_url"] == "/read?board=programming&pid=789"
    op, payload = manager.calls[0]
    assert op == "write" and payload["body"] == "[원본 1]\n[사진 1]"
    assert "blocks" not in payload and "no" not in payload and "fingerprint" not in payload


@pytest.mark.parametrize("complete", [True, False, None])
def test_read_passes_comments_complete_to_template(setup, monkeypatch, complete):
    _, client, manager, contexts = setup

    async def read_stub(*args, **kwargs):
        data = {"title": "제목", "html": "<div>본문</div>"}
        if complete is not None:
            data["_comments_complete"] = complete
        return data, [], []

    monkeypatch.setattr(routes, "async_read", read_stub)
    response = client.get("/read?board=programming&pid=123")
    assert response.status_code == 200
    assert contexts[-1][0] == "read.html"
    assert contexts[-1][1]["comments_complete"] is bool(complete)
    assert "_comments_complete" not in contexts[-1][1]["data"]
    assert not manager.calls


def test_account_page_shows_update_required_block(setup):
    import flask

    app = setup[0]
    blocked = {"configured": True, "logged_in": False, "user_id": None, "name": None,
               "blocked": True, "reason": "update_required"}
    account = {"ok": True, **blocked, "accounts": {"1": blocked}}
    with app.test_request_context("/write/account"):
        html = flask.render_template("write_account.html", account=account)
    assert 'data-reason="update_required"' in html
    assert "디시가 앱 업데이트를 요구해요. 미러 서버의 앱 버전을 올려야 해요." in html
    assert "아직 로그인하지 않았어요" not in html
