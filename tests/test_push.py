import asyncio
import base64
import fcntl
import json
import os
import stat
from types import SimpleNamespace

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import create_app, routes
from app.services import push
from app.services.dc.api import DocumentNotFoundError, DocumentUnavailableError

ORIGIN = {"Origin": "https://localhost"}  # Existing write gate compares netloc, not scheme.
ENDPOINT = "https://fcm.googleapis.com/test/sub"
NOW = 1_795_180_000_000
DAY = 86_400_000
_real_send = push._send


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def row(id="post", **changes):
    return dict(dict(board="abc", kind="minor", pid="123", id=id, title="제목",
                     at=NOW, base=False, known="0"), **changes)


def comment(id, reply_to=None, **changes):
    return dict(dict(id=id, reply_to=reply_to, author="쓴이", contents=" 하나\n  둘 "), **changes)


class Inbox:
    def __init__(self, tmp_path):
        self.app = create_app()
        self.path = tmp_path / "private" / "push.json"
        self.app.config.update(TESTING=True, WRITE_KEY="key", SECRET_KEY="test", VISITOR_CHECK=False,
                               PUBLIC_BASE_URL="https://mirror.example:443/", PUSH_STATE=self.path)
        self.client = self.app.test_client()
        assert self.client.post("/write/unlock", data={"key": "key"}, headers=ORIGIN).status_code == 303
        point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.info = {"endpoint": ENDPOINT, "keys": {"p256dh": b64(point), "auth": b64(b"x" * 16)}}
        self.now = NOW
        self.sent = []
        self.reads = []
        self.comments = []
        self.complete = True
        self.stale = False
        self.status = 201
        self.on_send = None
        self.on_read = None
        self.error = None

    def post(self, path, body):
        return self.client.post("/push/" + path, json=body, headers=ORIGIN)

    def sync(self, rows, rev=1, endpoint=ENDPOINT):
        return self.post("sync", {"subscription": dict(self.info, endpoint=endpoint), "rows": rows, "rev": rev})

    def send(self, subscription, private, payload):
        self.sent.append((subscription["endpoint"], payload))
        if self.on_send:
            self.on_send()
        return self.status

    async def read(self, pid, board, kind):
        self.reads.append((pid, board, kind))
        if self.on_read:
            self.on_read()
        if self.error:
            raise self.error
        return {"_comments_complete": self.complete, "_served_stale": self.stale}, self.comments, []

    def poll(self, after_ms=0):
        self.now += after_ms
        with self.app.app_context():
            push.poll_step()


@pytest.fixture
def inbox(monkeypatch, tmp_path):
    box = Inbox(tmp_path)
    monkeypatch.setattr(push, "_now_ms", lambda: box.now)
    monkeypatch.setattr(push, "_last_registration", float("-inf"))
    monkeypatch.setattr(push, "time", SimpleNamespace(monotonic=lambda: (box.now - NOW) / 1000))
    monkeypatch.setattr(push, "_send", box.send)
    monkeypatch.setattr(push, "async_read", box.read)
    monkeypatch.setattr(push, "run_async", asyncio.run)
    return box


def test_push_gate_and_context(inbox):
    fresh = inbox.app.test_client()
    for method, path in [("GET", "/push/key"), ("POST", "/push/sync"), ("POST", "/push/unsubscribe")]:
        locked = fresh.open(path, method=method)
        assert (locked.status_code, locked.json) == (403, {"ok": False, "code": "locked"})
        assert locked.headers["Cache-Control"] == "no-store"
        inbox.app.config["VISITOR_CHECK"] = True
        assert fresh.open(path, method=method).json["code"] == "visitor_check_required"
        inbox.app.config["PUBLIC_BASE_URL"] = "http://example.test"
        assert fresh.open(path, method=method).status_code == 404
        inbox.app.config.update(VISITOR_CHECK=False, PUBLIC_BASE_URL="https://example.test")
    assert inbox.client.get("/push/key", headers={"Sec-Fetch-Site": "none"}).status_code == 403
    assert inbox.client.post("/push/sync", json={}).status_code == 403
    assert not inbox.path.exists()
    for path, flag in [("/write", True), ("/poker", False)]:
        with inbox.app.test_request_context(path):
            assert routes.dc_write_context()["push_configured"] is flag
    inbox.app.config["WRITE_KEY"] = ""
    assert fresh.get("/push/key").status_code == 404
    inbox.poll()
    assert not inbox.path.parent.exists()


def test_key_persistence_and_unreadable_state(inbox):
    key = inbox.client.get("/push/key", headers={"Sec-Fetch-Site": "same-origin"}).json["key"]
    point = base64.urlsafe_b64decode(key + "=")
    assert "=" not in key and len(point) == 65 and point[0] == 4
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    assert inbox.sync([row()]).status_code == 200
    assert stat.S_IMODE(inbox.path.stat().st_mode) == 0o600
    assert inbox.client.get("/push/key").json["key"] == key
    for corrupt in (b"{", b"{}", b'{"subscriptions":{}}'):
        inbox.path.write_bytes(corrupt)
        for response in [inbox.client.get("/push/key"), inbox.sync([]), inbox.post("unsubscribe", {"endpoint": ENDPOINT})]:
            assert (response.status_code, response.json) == (503, {"ok": False, "code": "unavailable"})
        assert inbox.path.read_bytes() == corrupt


def test_failed_atomic_replace_keeps_old_state(inbox, monkeypatch):
    key = inbox.client.get("/push/key").json

    def fail_replace(*args):
        raise OSError("write failed")

    monkeypatch.setattr(push.os, "replace", fail_replace)
    response = inbox.sync([row()])
    assert (response.status_code, response.json) == (503, {"ok": False, "code": "unavailable"})
    assert not inbox.sent
    assert inbox.client.get("/push/key").json == key
    inbox.poll()
    assert not inbox.reads


def test_state_replacement_syncs_parent_after_key_is_visible(inbox, monkeypatch):
    fsync = os.fsync
    durable = []

    def sync_fd(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            assert os.path.samestat(info, inbox.path.parent.stat())
            durable.append(json.loads(inbox.path.read_text())["public"])
        fsync(fd)

    monkeypatch.setattr(os, "fsync", sync_fd)
    key = inbox.client.get("/push/key").json["key"]
    assert durable == [key]


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/path", "https://fcm.googleapis.com.evil.test", "https://push.apple.com",
    "https://bad_.push.apple.com", "https://.push.apple.com", "https://notnotify.windows.com",
    "https://fcm.googleapis.com:444/", "https://fcm.googleapis.com:/", "https://user@fcm.googleapis.com/",
    "https://fcm.googleapis.com/#", "https://fcm.googleapis.com/\ncap", ENDPOINT + "a" * 1000,
    "https://evil.test\\@fcm.googleapis.com/p", "https://[invalid", "https://fcm.googleapis.com/%xx",
])
def test_invalid_endpoint(inbox, endpoint):
    assert inbox.sync([], endpoint=endpoint).status_code == 400
    assert not inbox.sent and not inbox.path.exists()


def test_invalid_records_keys_and_request_limit(inbox):
    for change in [dict(board="bad-board"), dict(kind="latest"), dict(pid="01"), dict(id="0"),
                   dict(known="1.2"), dict(at=True), dict(base=0), dict(title="가" * 101)]:
        assert inbox.sync([row(**change)]).json == {"ok": False, "code": "invalid"}
    assert inbox.sync([row()] * 51).status_code == 400
    assert inbox.sync([], rev=True).status_code == 400
    for change in [dict(p256dh=b64(b"\x04" + bytes(64))), dict(p256dh=b64(b"\x02" + bytes(32))),
                   dict(auth=b64(bytes(17))), dict(auth="???"), dict(auth="a")]:
        info = dict(inbox.info, keys=dict(inbox.info["keys"], **change))
        assert inbox.post("sync", {"subscription": info, "rev": 1, "rows": []}).status_code == 400
    oversized = dict(subscription=inbox.info, rev=1, rows=[], ignored="x" * 65536)
    assert inbox.post("sync", oversized).status_code == 400
    assert not inbox.sent and not inbox.path.exists()


def test_sync_revision_welcome_rate_limit_and_unsubscribe(inbox):
    # A racing stale request sees the persisted subscription during the welcome send.
    stale = []
    inbox.on_send = lambda: stale.append(inbox.sync([], rev=8).json)
    assert inbox.sync([row()], rev=8).json == {"ok": True, "created": True, "stale": False, "welcomed": True}
    assert stale == [{"ok": True, "created": False, "stale": True, "welcomed": False}]
    assert inbox.sent == [(ENDPOINT, {"title": "푸시 알림을 켰어요", "body": "새 댓글과 답글이 달리면 이렇게 알려요.",
                                     "url": "/", "tag": "mirror:welcome"})]
    inbox.on_send = None
    inbox.now += 4999
    second = "https://one.notify.windows.com:443/s"
    limited = inbox.sync([], endpoint=second)
    assert (limited.status_code, limited.json) == (429, {"ok": False, "code": "retry"})
    assert inbox.sync([row()], rev=9).json == {"ok": True, "created": False, "stale": False, "welcomed": False}
    inbox.now += 1
    inbox.status = 410
    gone = inbox.sync([], endpoint=second)
    assert (gone.status_code, gone.json) == (410, {"ok": False, "code": "gone"})
    inbox.now += 5000
    inbox.status = 503
    assert inbox.sync([], endpoint=second).json == {"ok": True, "created": True, "stale": False, "welcomed": False}
    for _ in range(2):
        assert inbox.post("unsubscribe", {"endpoint": ENDPOINT}).json == {"ok": True}
    inbox.poll()
    assert not inbox.reads


def test_encrypted_transport_uses_configured_subject_no_redirect(inbox, monkeypatch):
    attempts = []

    def provider(adapter, request, *, timeout, verify, **kwargs):
        assert timeout == 10 and verify is True
        assert request.headers["Content-Encoding"] == "aes128gcm"
        assert b"mirror:welcome" not in request.body
        token = request.headers["Authorization"].split("t=")[1].split(",")[0]
        claims = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        attempts.append((request.url, claims["aud"], claims["sub"]))
        response = requests.Response()
        response.url = request.url
        response.request = request
        response._content = b""
        response.status_code = 307
        response.headers["Location"] = "https://different.example/"
        return response

    monkeypatch.setattr(push, "_send", _real_send)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", provider)
    for host in ("updates.push.services.mozilla.com", "device.push.apple.com"):
        endpoint = f"https://{host}/cap"
        result = inbox.sync([], endpoint=endpoint)
        assert result.json["created"] and not result.json["welcomed"]
        inbox.now += 5000
    assert attempts == [(f"https://{h}/cap", f"https://{h}", "https://mirror.example:443/")
                        for h in ("updates.push.services.mozilla.com", "device.push.apple.com")]


def test_baseline_replies_own_comments_batch_and_newest_preview(inbox):
    assert inbox.sync([row(base=True), row("10"), row("12")]).status_code == 200
    inbox.sent.clear()
    inbox.comments = [comment("10"), comment("20"), comment("12", "10")]
    inbox.poll()
    assert not inbox.sent
    # A sync cannot re-baseline the post, advance known, or roll back a comment cursor.
    assert inbox.sync([row(base=True, known="99"), row("10"), row("12")], rev=2).status_code == 200
    inbox.comments += [comment("19", "10"), comment("21", "12"), comment("22"), comment("23", "10",
                       author="가" * 50, contents="word  " * 50)]
    inbox.poll(120000)
    assert inbox.sent == [(ENDPOINT, {"title": "새 댓글·답글 3개 · 제목",
                           "body": "가" * 40 + ": " + ("word " * 16),
                           "url": "/read?board=abc&pid=123&kind=minor#comment-23", "tag": "mirror:abc:123"})]
    inbox.comments.append(comment("24", "10", dccon={}))
    inbox.poll(120000)
    assert inbox.sent[-1][1]["title"] == "새 답글 · 제목"
    assert inbox.sent[-1][1]["body"] == "쓴이: 이모티콘"


def test_baseline_round_still_pushes_reply(inbox):
    inbox.sync([row(base=True), row("10")])
    inbox.comments = [comment("11"), comment("12", "10")]
    inbox.poll()
    assert [p["title"] for _, p in inbox.sent] == ["푸시 알림을 켰어요", "새 답글 · 제목"]


def test_transient_retry_then_gone(inbox):
    inbox.sync([row(known="9"), row("12")])
    inbox.sent.clear()
    inbox.comments = [comment("9"), comment("10"), comment("12")]
    inbox.status = 503
    inbox.poll()
    inbox.status = 201
    inbox.poll(120000)
    inbox.poll(120000)
    expected = {"title": "새 댓글 · 제목", "body": "쓴이: 하나 둘",
                "url": "/read?board=abc&pid=123&kind=minor#comment-10", "tag": "mirror:abc:123"}
    assert inbox.sent == [(ENDPOINT, expected)] * 2
    inbox.comments.append(comment("13"))
    inbox.status = 404
    inbox.poll(120000)
    assert inbox.sent[-1][1]["url"].endswith("#comment-13")
    inbox.status = 201
    assert inbox.sync([]).json["created"]


def test_retry_preserves_post_baseline_but_retries_reply(inbox):
    inbox.sync([row(base=True), row("1")])
    inbox.sent.clear()
    inbox.comments = [comment("2"), comment("3", "1")]
    inbox.status = 503
    inbox.poll()
    inbox.comments.append(comment("4"))
    inbox.status = 201
    inbox.poll(120_000)
    assert [payload["title"] for _, payload in inbox.sent] == ["새 답글 · 제목", "새 댓글·답글 2개 · 제목"]
    assert inbox.sent[-1][1]["url"].endswith("#comment-4")


def test_stale_complete_poll_neither_sends_nor_baselines(inbox):
    inbox.sync([row(base=True), row("1")])
    inbox.sent.clear()
    inbox.comments = [comment("2", "1")]
    inbox.stale = True
    inbox.poll()
    assert not inbox.sent
    inbox.stale = False
    inbox.comments.append(comment("3"))
    inbox.poll(120_000)
    assert [payload["title"] for _, payload in inbox.sent] == ["새 답글 · 제목"]


def test_sync_adding_own_comment_during_fetch_suppresses_push(inbox):
    inbox.sync([row()])
    inbox.sent.clear()
    inbox.comments = [comment("5")]
    inbox.on_read = lambda: inbox.sync([row(), row("5")], rev=2)
    inbox.poll()
    assert not inbox.sent


def test_poll_commit_does_not_resurrect_removed_or_advance_replaced_row(inbox):
    inbox.sync([row(), row("1")])
    inbox.sent.clear()
    inbox.comments = [comment("10")]
    # Replace evaluated rows during the send, before its progress is committed.
    inbox.on_send = lambda: inbox.sync([row(at=NOW+1, title="새 세대"), row("3", pid="456")], rev=2)
    inbox.poll()
    inbox.on_send = None
    inbox.comments = [comment("10"), comment("11", "1")]
    inbox.poll(30_000)
    assert inbox.sent[-1][1]["title"] == "새 댓글·답글 2개 · 새 세대"
    inbox.comments = [comment("4", "3")]
    inbox.poll(30_000)
    assert inbox.sent[-1][1]["url"] == "/read?board=abc&pid=456&kind=minor#comment-4"
    inbox.on_send = lambda: inbox.post("unsubscribe", {"endpoint": ENDPOINT})
    inbox.comments = [comment("15")]
    inbox.poll(120_000)
    reads = list(inbox.reads)
    inbox.poll(120_000)
    assert inbox.reads == reads


@pytest.mark.parametrize("failure", ["partial", DocumentUnavailableError, RuntimeError, DocumentNotFoundError])
def test_failed_reads_are_rescheduled_without_starvation(inbox, failure):
    inbox.sync([row(base=True), row(pid="124")])
    inbox.sent.clear()
    inbox.comments = [comment("10")]
    if failure == "partial":
        inbox.complete = False
    else:
        inbox.error = failure()
    inbox.poll()
    inbox.complete, inbox.error = True, None
    inbox.poll(29_999)
    assert inbox.reads == [(123, "abc", "minor")] and not inbox.sent
    inbox.poll(1)
    assert inbox.sent[-1][1]["url"].endswith("pid=124&kind=minor#comment-10")
    inbox.comments = [comment("11")]
    inbox.poll(90_000)
    assert len(inbox.sent) == 1
    assert [pid for pid, _, _ in inbox.reads] == ([123, 124] if failure is DocumentNotFoundError else [123, 124, 123])


@pytest.mark.parametrize("age,interval", [(0, 120_000), (6 * 3600_000, 600_000), (3 * DAY, 3600_000)])
def test_poll_interval_uses_newest_row(inbox, age, interval):
    inbox.sync([row(at=NOW-age), row("3", at=NOW-20*DAY)])
    inbox.poll()
    inbox.poll(interval - 1)
    assert len(inbox.reads) == 1
    inbox.poll(1)
    assert len(inbox.reads) == 2


def test_expiry_and_least_recently_synced_eviction(inbox):
    endpoints = [ENDPOINT + str(i) for i in range(11)]
    for endpoint in endpoints[:10]:
        assert inbox.sync([row()], endpoint=endpoint).json["created"]
        inbox.now += 5000
    assert not inbox.sync([row()], endpoint=endpoints[0], rev=2).json["created"]
    assert inbox.sync([row()], endpoint=endpoints[10]).json["created"]
    assert inbox.sync([], endpoint=endpoints[1]).status_code == 429  # evicted
    assert inbox.sync([], endpoint=endpoints[0]).json["stale"]
    inbox.now += 30 * DAY + 1
    inbox.poll()
    assert not inbox.reads
    assert inbox.sync([], rev=1, endpoint=endpoints[0]).json["created"]


def test_reloading_worker_retries_leadership_and_survives_bad_state(inbox, caplog):
    inbox.sync([row()])
    good = inbox.path.read_bytes()
    # Execute finite ticks in this thread. Another open file description stands
    # for the retiring worker; no background thread or wall-clock wait in pytest.
    with open(str(inbox.path) + ".leader", "w") as retiring:
        fcntl.flock(retiring, fcntl.LOCK_EX)
        ticks = []

        def tick(_):
            ticks.append(len(inbox.reads))
            if len(ticks) == 1:
                fcntl.flock(retiring, fcntl.LOCK_UN)
                inbox.path.write_bytes(b"{")
            elif len(ticks) == 2:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(retiring, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inbox.path.write_bytes(good)

        stop = SimpleNamespace(is_set=lambda: len(ticks) >= 3, wait=tick)
        push._poll_loop(inbox.app, stop)
        fcntl.flock(retiring, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert ticks == [0, 0, 1]
    assert "JSONDecodeError" in caplog.text and ENDPOINT not in caplog.text


def test_shutdown_caps_wait_for_stuck_daemon():
    waits = []
    stop = push.threading.Event()
    push.stop_poller((stop, SimpleNamespace(join=lambda timeout=None: waits.append(timeout))))
    assert stop.is_set() and waits == [5]
