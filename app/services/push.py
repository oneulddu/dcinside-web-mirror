"""Web Push for the local inbox. The browser owns intent; the poller owns progress."""
import base64
import copy
import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import current_app
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .async_bridge import run_async
from .core import async_read
from .dc.api import DocumentNotFoundError
from .state_file import lock_file as _lock_file, save as _save

log = logging.getLogger(__name__)
MAX_ROWS = 50
MAX_SUBSCRIPTIONS = 10
DAY_MS = 86_400_000
NUMBER = re.compile(r"[1-9][0-9]{0,14}")
PROGRESS = re.compile(r"[0-9]{1,15}")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
WELCOME = {"title": "푸시 알림을 켰어요", "body": "새 댓글과 답글이 달리면 이렇게 알려요.",
           "url": "/", "tag": "mirror:welcome"}
_last_registration = float("-inf")
_registration_lock = threading.Lock()


class PushError(Exception):
    STATUS = {"invalid": 400, "gone": 410, "retry": 429, "unavailable": 503}

    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.status = self.STATUS[code]


def _now_ms():
    return time.time_ns() // 1_000_000


def _require(condition):
    if not condition:
        raise PushError("invalid")


def _https_url(value):
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        # Accessing port validates both its syntax and its range.
        if parsed.scheme == "https" and parsed.hostname and parsed.port != 0:
            return parsed
    except ValueError:
        pass
    return None


def configured(app):
    return bool(app.config.get("WRITE_KEY") and _https_url(app.config.get("PUBLIC_BASE_URL")))


def _endpoint_host(endpoint):
    _require(isinstance(endpoint, str) and len(endpoint) <= 1000
             and re.fullmatch(r"(?:[a-zA-Z0-9_.!~*'();/?:@&=+$,-]|%[0-9A-Fa-f]{2})+", endpoint))
    parsed = _https_url(endpoint)
    _require(parsed and parsed.username is None and parsed.port in (None, 443))
    host = parsed.hostname
    _require(parsed.netloc.lower() in (host, host + ":443") and len(host) <= 253
             and all(DNS_LABEL.fullmatch(label) for label in host.split(".")))
    _require(host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
             or host.endswith((".push.apple.com", ".notify.windows.com")))
    return host


def _decode(value, length):
    _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value))
    raw = base64.urlsafe_b64decode(value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4))
    _require(len(raw) == length and value in (_encode(raw), base64.urlsafe_b64encode(raw).decode()))
    return raw


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _subscription(value):
    _require(isinstance(value, dict))
    endpoint = value.get("endpoint")
    _endpoint_host(endpoint)
    keys = value.get("keys")
    _require(isinstance(keys, dict))
    try:
        point = _decode(keys.get("p256dh"), 65)
        _require(point[0] == 4)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        auth = _decode(keys.get("auth"), 16)
    except ValueError:
        raise PushError("invalid") from None
    return {"endpoint": endpoint, "keys": {"p256dh": _encode(point), "auth": _encode(auth)}}


def _row(raw):
    _require(isinstance(raw, dict))
    for field, pattern in (("board", r"[A-Za-z0-9_]{1,80}"), ("pid", NUMBER), ("known", PROGRESS)):
        _require(isinstance(raw.get(field), str) and re.fullmatch(pattern, raw[field]))
    _require(isinstance(raw.get("id"), str) and (raw["id"] == "post" or NUMBER.fullmatch(raw["id"])))
    _require(raw.get("kind") in ("", "normal", "minor", "mini", "person"))
    _require(isinstance(raw.get("title"), str) and len(raw["title"]) <= 100)
    _require(type(raw.get("at")) is int and type(raw.get("base")) is bool)
    return {k: raw[k] for k in ("board", "kind", "pid", "id", "at", "title", "base", "known")}


def _post(r):
    return r["board"], r["kind"], r["pid"]


def _generation(r):
    return (*_post(r), r["id"], r["at"])


def _digest(endpoint):
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _public_key(key):
    return _encode(key.public_key.public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint))


def _new_state():
    key = Vapid()
    key.generate_keys()
    return {"private": key.private_pem().decode("ascii"), "public": _public_key(key),
            "subscriptions": {}, "last_fetch": 0}


def _validate_state(state):
    key = Vapid.from_pem(state["private"].encode("ascii"))
    _require(isinstance(key.private_key.curve, ec.SECP256R1) and _public_key(key) == state["public"])
    subs = state["subscriptions"]
    _require(type(state["last_fetch"]) is int and isinstance(subs, dict) and len(subs) <= MAX_SUBSCRIPTIONS)
    for digest, record in subs.items():
        info = _subscription(record["subscription"])
        _require(digest == _digest(info["endpoint"]) and type(record["rev"]) is int
                 and type(record["synced"]) is int and re.fullmatch(r"[a-f0-9]{32}", record["generation"]))
        _require(isinstance(record["rows"], list) and len(record["rows"]) <= MAX_ROWS)
        for row in record["rows"]:
            _row(row)
            _require(type(row["checked"]) is int)


def _state_path():
    return Path(current_app.config.get("PUSH_STATE") or Path(current_app.instance_path) / "push_state.json")


@contextmanager
def _state(*, create=False):
    path = _state_path()
    try:
        with _lock_file(path, ".lock") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with path.open(encoding="utf-8") as source:
                    state = json.load(source)
                _validate_state(state)
            except FileNotFoundError:
                if path.is_symlink():
                    raise PushError("unavailable") from None
                state = None
            except Exception as exc:
                log.error("push state unreadable type=%s", type(exc).__name__)
                raise PushError("unavailable") from None
            before = copy.deepcopy(state)
            if create and state is None:
                state = _new_state()
            yield state
            if state != before:
                _save(path, state)
    except OSError as exc:
        log.error("push state I/O type=%s", type(exc).__name__)
        raise PushError("unavailable") from None


def public_key():
    with _state(create=True) as state:
        return {"ok": True, "key": state["public"]}


class _PushSession(requests.Session):
    def post(self, url, **kwargs):
        _endpoint_host(url)
        return super().post(url, allow_redirects=False, verify=True, **kwargs)


def _send(subscription, private, payload):
    host = _endpoint_host(subscription["endpoint"])
    try:
        key = Vapid.from_pem(private.encode("ascii"))
        # py-vapid's optional sub regex rejects valid HTTPS URLs with paths/ports.
        # configured() already requires an HTTPS URL from the operator.
        key.conf["no-strict"] = True
        with _PushSession() as session:
            session.trust_env = False
            response = webpush(subscription_info=subscription, data=json.dumps(payload, ensure_ascii=True),
                               vapid_private_key=key, vapid_claims={"sub": current_app.config["PUBLIC_BASE_URL"]},
                               ttl=86400, timeout=10, requests_session=session)
        status = response.status_code
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else 0
    except Exception as exc:
        log.warning("push send host=%s type=%s", host, type(exc).__name__)
        status = 0
    log.info("push send host=%s status=%d", host, status)
    return status


def _expire(subs, now):
    cutoff = now - 30 * DAY_MS
    for digest, record in list(subs.items()):
        record["rows"] = [r for r in record["rows"] if r["at"] >= cutoff]
        if not record["rows"] and record["synced"] < cutoff:
            del subs[digest]


def _admit_registration():
    global _last_registration
    with _registration_lock:
        now = time.monotonic()
        if now - _last_registration < 5:
            raise PushError("retry")
        _last_registration = now


def sync(body):
    _require(isinstance(body, dict) and type(body.get("rev")) is int)
    info = _subscription(body.get("subscription"))
    _require(isinstance(body.get("rows"), list) and len(body["rows"]) <= MAX_ROWS)
    rows = [_row(r) for r in body["rows"]]
    rev = body["rev"]
    digest = _digest(info["endpoint"])
    with _state(create=True) as state:
        now = _now_ms()
        subs = state["subscriptions"]
        existing = subs.get(digest)
        if existing and rev <= existing["rev"]:
            return {"ok": True, "created": False, "welcomed": False, "stale": True}
        _expire(subs, now)
        existing = subs.get(digest)
        if existing is None:
            _admit_registration()
            if len(subs) >= MAX_SUBSCRIPTIONS:
                del subs[min(subs, key=lambda k: subs[k]["synced"])]
        previous = {_generation(r): r for r in existing["rows"]} if existing else {}
        for row in rows:
            old = previous.get(_generation(row))
            if old is not None:
                row.update({k: old[k] for k in ("known", "base", "checked")})
            else:
                row["checked"] = 0
                if row["id"] != "post":
                    row.update(base=False, known=str(max(int(row["known"]), int(row["id"]))))
        gen = existing["generation"] if existing else secrets.token_hex(16)
        subs[digest] = {"generation": gen, "subscription": info, "rev": rev, "synced": now,
                        "rows": [r for r in rows if r["at"] >= now - 30 * DAY_MS]}
        private = state["private"]
    welcomed = False
    if existing is None:
        status = _send(info, private, WELCOME)
        if status in (404, 410):
            with _state() as state:
                record = _current_record(state, digest, gen, info)
                if record is not None:
                    del state["subscriptions"][digest]
            raise PushError("gone")
        welcomed = 200 <= status < 300
    return {"ok": True, "created": existing is None, "welcomed": welcomed, "stale": False}


def unsubscribe(body):
    _require(isinstance(body, dict))
    endpoint = body.get("endpoint")
    _endpoint_host(endpoint)
    with _state() as state:
        if state is not None:
            state["subscriptions"].pop(_digest(endpoint), None)
    return {"ok": True}


def _current_record(state, digest, gen, info):
    record = state["subscriptions"].get(digest) if state is not None else None
    if record and record["generation"] == gen and record["subscription"] == info:
        return record
    return None


def _interval(age):
    if age < DAY_MS // 4:
        return 120_000
    return 600_000 if age < 3 * DAY_MS else 3_600_000


def _reserve_post():
    with _state() as state:
        if state is None:
            return None
        now = _now_ms()
        subs = state["subscriptions"]
        _expire(subs, now)
        if now < state["last_fetch"] + 30_000:
            return None
        posts = {}
        for digest, record in subs.items():
            for row in record["rows"]:
                posts.setdefault(_post(row), {}).setdefault(digest, []).append(row)
        due = {}
        for post, watches in posts.items():
            rows = [r for group in watches.values() for r in group]
            checked = max(r["checked"] for r in rows)
            due[post] = checked + _interval(now - max(r["at"] for r in rows)) if checked else 0
        if not due or min(due.values()) > now:
            return None
        post = min(due, key=due.get)
        snapshot = []
        for digest, rows in posts[post].items():
            for row in rows:
                row["checked"] = now
            record = subs[digest]
            snapshot.append((digest, record["generation"], record["subscription"], rows))
        # Reserve before releasing the lock: failed reads and leader handoffs are paced too.
        state["last_fetch"] = now
        return post, snapshot, state["private"]


def _matches(post, rows, comments):
    own = {r["id"] for r in rows if r["id"] != "post"}
    count = 0
    newest = None
    for number, comment in comments.items():
        if str(number) in own:
            continue
        reply = next((r for r in rows if r["id"] != "post" and r["id"] == comment.get("reply_to")
                      and number > int(r["known"])), None)
        watch = reply or next((r for r in rows if r["id"] == "post" and not r["base"]
                               and number > int(r["known"])), None)
        if watch:
            count += 1
            if newest is None or number > newest[0]:
                newest = number, comment, watch
    if not count:
        return None, 0
    number, comment, watch = newest
    board, kind, pid = post
    label = ("새 답글" if watch["id"] != "post" else "새 댓글") if count == 1 else f"새 댓글·답글 {count}개"
    query = {"board": board, "pid": pid}
    if kind:
        query["kind"] = kind
    author = str(comment.get("author") or "")[:40]
    text = "이모티콘" if comment.get("dccon") is not None else " ".join(str(comment.get("contents") or "").split())[:80]
    return {"title": f"{label} · {watch['title']}", "body": f"{author}: {text}",
            "url": f"/read?{urlencode(query)}#comment-{number}", "tag": f"mirror:{board}:{pid}"}, count


def _commit(digest, gen, info, rows, outcome, highest):
    with _state() as state:
        record = _current_record(state, digest, gen, info)
        if record is None:
            return
        if outcome == "gone":
            del state["subscriptions"][digest]
            return
        evaluated = {_generation(r) for r in rows}
        if outcome == "not_found":
            record["rows"] = [r for r in record["rows"] if _generation(r) not in evaluated]
        else:
            for row in record["rows"]:
                if _generation(row) in evaluated:
                    row.update(base=False, known=str(max(int(row["known"]), highest)))


def poll_step():
    """One leader tick in an app context; no network while a state transaction is open."""
    if not configured(current_app):
        return
    job = _reserve_post()
    if not job:
        return
    post, watches, private = job
    board, kind, pid = post
    try:
        data, raw_comments, _ = run_async(async_read(int(pid), board, kind=None if kind in ("", "normal") else kind))
        if not data.get("_comments_complete") or data.get("_served_stale"):
            log.info("push poll board=%s pid=%s outcome=incomplete", board, pid)
            return
        comments = {int(c["id"]): c for c in raw_comments if NUMBER.fullmatch(c.get("id") or "")}
    except DocumentNotFoundError:
        for digest, gen, info, rows in watches:
            _commit(digest, gen, info, rows, "not_found", 0)
        log.info("push poll board=%s pid=%s outcome=not_found", board, pid)
        return
    except Exception as exc:
        log.info("push poll board=%s pid=%s outcome=fetch_failure type=%s", board, pid, type(exc).__name__)
        return

    highest = max(comments, default=0)
    for digest, gen, info, _ in watches:
        with _state() as state:
            record = _current_record(state, digest, gen, info)
            if record is None:
                continue
            rows = [row for row in record["rows"] if _post(row) == post]
        if not rows:
            continue
        payload, count = _matches(post, rows, comments)
        outcome = "no_match"
        if payload:
            status = _send(info, private, payload)
            outcome = "gone" if status in (404, 410) else "accepted" if 200 <= status < 300 else "retry"
        # A post baseline observes this fetch independently of any matched reply's delivery.
        evaluated = [r for r in rows if r["id"] == "post" and r["base"]] if outcome == "retry" else rows
        if evaluated:
            _commit(digest, gen, info, evaluated, outcome, highest)
        log.info("push poll board=%s pid=%s outcome=%s matches=%d rows=%d", board, pid, outcome, count, len(rows))


def _poll_loop(app, stop):
    leader = None
    try:
        with app.app_context():
            while not stop.is_set():
                try:
                    if leader is None:
                        candidate = _lock_file(_state_path(), ".leader")
                        try:
                            fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            candidate.close()  # A retiring worker can still hold leadership.
                        except Exception:
                            candidate.close()
                            raise
                        else:
                            leader = candidate
                    if leader is not None:
                        poll_step()
                except Exception as exc:
                    log.error("push poll loop failure type=%s", type(exc).__name__)
                stop.wait(5)
    finally:
        if leader is not None:
            leader.close()


def start_poller(app):
    if configured(app):
        stop = threading.Event()
        thread = threading.Thread(target=_poll_loop, args=(app, stop), daemon=True, name="inbox-push")
        thread.start()
        return stop, thread
    return None


def stop_poller(poller):
    if poller:
        stop, thread = poller
        stop.set()
        thread.join(timeout=5)
