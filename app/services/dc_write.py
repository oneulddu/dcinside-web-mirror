"""Isolated, synchronous DC app worker and process-wide write access controls.

No request/session/credential values are logged. Transport failures are never
replayed: a timed-out upstream write may already have succeeded.
"""
import atexit
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import subprocess
import threading
import time
from collections import deque

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE = "mirror_write"
COOKIE_AGE = 180 * 24 * 60 * 60
LIMITS = dict(subject_max=40, body_max=10000, nickname_max=20, password_min=2, password_max=20)
CAPTCHA_KEY = re.compile(r"^[0-9a-f]{16}$")
MAX_IMAGE = 200 * 1024


class WorkerUnavailable(Exception):
    def __init__(self, sent=False):
        super().__init__()
        self.sent = sent


class WorkerManager:
    def __init__(self, node=None, directory=None, state=None, timeout=40):
        self.node = node if node is not None else shutil.which("node")
        self.directory = Path(directory or Path(__file__).resolve().parents[2] / "dc_app")
        self.state = str(state or self.directory.parent / "instance/dc_app_credentials.json")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process = None
        atexit.register(self.close)

    def _discard(self):
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdin.close()
            process.stdout.close()

    def close(self):
        with self._lock:
            self._discard()

    def request(self, op, deadline=None, **payload):
        if deadline is None:
            deadline = time.monotonic() + self.timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            raise WorkerUnavailable(sent=False)
        sent = False
        try:
            try:
                if time.monotonic() >= deadline:
                    raise WorkerUnavailable(sent=False)
                if self._process is None or self._process.poll() is not None:
                    self._discard()
                    if not self.node:
                        raise WorkerUnavailable()
                    env = dict(os.environ, MIRROR_DC_APP_STATE=self.state)
                    # Worker stderr is deliberately discarded, including dependency crashes.
                    self._process = subprocess.Popen(
                        [self.node, str(self.directory / "worker.mjs")],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        env=env, bufsize=0,
                    )
                    os.set_blocking(self._process.stdin.fileno(), False)
                    os.set_blocking(self._process.stdout.fileno(), False)
                process = self._process
                identifier = secrets.token_hex(12)
                pending = memoryview((json.dumps(dict(payload, id=identifier, op=op)) + "\n").encode())
                received = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdin, selectors.EVENT_WRITE)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining) or time.monotonic() >= deadline:
                            raise WorkerUnavailable()
                        if pending:
                            written = os.write(process.stdin.fileno(), pending)
                            sent = sent or written > 0
                            pending = pending[written:]
                            if not pending:
                                selector.unregister(process.stdin)
                                selector.register(process.stdout, selectors.EVENT_READ)
                        else:
                            chunk = os.read(process.stdout.fileno(), 65536)
                            if not chunk:
                                raise WorkerUnavailable()
                            received.extend(chunk)
                            if len(received) > 400000:
                                raise WorkerUnavailable()
                            if b"\n" not in received:
                                continue
                            line, extra = received.split(b"\n", 1)
                            result = json.loads(line)
                            if extra or not isinstance(result, dict) or result.get("id") != identifier or type(result.get("ok")) is not bool:
                                raise WorkerUnavailable()
                            if not isinstance(result.get("data" if result["ok"] else "error"), dict):
                                raise WorkerUnavailable()
                            return result
            except (OSError, ValueError, WorkerUnavailable):
                self._discard()
                raise WorkerUnavailable(sent=sent) from None
        finally:
            self._lock.release()


_manager_lock = threading.Lock()
_managers = {}


def get_manager():
    config = current_app.config
    options = (config.get("DC_APP_NODE", shutil.which("node")), config.get("DC_APP_DIR"), config.get("DC_APP_STATE"))
    with _manager_lock:
        if options not in _managers:
            _managers[options] = WorkerManager(*options)
        return _managers[options]


def configured():
    return bool(current_app.config.get("WRITE_KEY"))


def _key():
    return str(current_app.config.get("WRITE_KEY") or "")


def _fingerprint():
    return hashlib.sha256(_key().encode()).hexdigest()[:32]


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="mirror-write")


def enabled():
    if not configured():
        return False
    try:
        value = _signer().loads(request.cookies.get(COOKIE, ""), max_age=COOKIE_AGE)
        return isinstance(value, str) and hmac.compare_digest(value, _fingerprint())
    except BadSignature:
        return False


def unlock_cookie(response):
    response.set_cookie(COOKIE, _signer().dumps(_fingerprint()), max_age=COOKIE_AGE,
                        httponly=True, secure=request.is_secure, samesite="Lax")


_unlock_lock = threading.Lock()
_unlock_failures = deque()


def check_key(candidate):
    with _unlock_lock:
        now = time.monotonic()
        while _unlock_failures and _unlock_failures[0] <= now - 60:
            _unlock_failures.popleft()
        if len(_unlock_failures) >= 5:
            return "limited"
        if hmac.compare_digest(candidate.encode(), _key().encode()):
            return None
        _unlock_failures.append(now)
        return "wrong"


_action_lock = threading.Lock()
_last_success = {}


def perform(op, **payload):
    deadline = time.monotonic() + 40
    # The interval check and success timestamp must surround the serialized call.
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not _action_lock.acquire(timeout=remaining):
        return unavailable(), 503
    try:
        if time.monotonic() >= deadline:
            return unavailable(), 503
        interval = 20 if op == "write" else 3
        previous = _last_success.get(op)
        wait = interval - (time.monotonic() - previous) if previous is not None else 0
        if wait > 0:
            return {"ok": False, "code": "retry", "message": "잠시 후 다시 시도해 주세요.", "retry_after": math.ceil(wait)}, 429
        try:
            result = get_manager().request(op, deadline=deadline, **payload)
        except WorkerUnavailable as exc:
            if exc.sent and op in {"write", "upvote"}:
                message = ("디시에 보냈지만 결과를 확인하지 못했어요. 다시 보내기 전에 목록을 확인해 주세요."
                           if op == "write" else "추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.")
                return {"ok": False, "code": "uncertain", "message": message}, 503
            return unavailable(), 503
        data = result.get("data", {})
        error = result.get("error", {})
        if result.get("ok") and data.get("result") in (True, "true"):
            _last_success[op] = time.monotonic()
            return {"ok": True, "data": data}, 200
        cause = error.get("cause") if not result.get("ok") else data.get("cause")
        cause = cause if isinstance(cause, str) else ""
        name = error.get("name")
        if name == "CaptchaRequiredError" or (cause and re.search(r"captcha|보안\s?코드|자동\s?입력", cause, re.I)):
            return {"ok": False, "code": "captcha_required", "message": cause or "보안 코드를 입력해 주세요."}, 200
        if cause == "잠시후 다시 이용해주세요.":
            return {"ok": False, "code": "retry", "message": cause, "retry_after": 30}, 429
        # 짧은 시간에 여러 번 보내면 디시가 이 문구로 막는다. 거절이 아니라 기다렸다가 다시 보낼 일이다.
        if re.fullmatch(r"잠시\s?후\s?다시\s?시도해\s?주세요\.?", cause):
            return {"ok": False, "code": "retry", "message": cause, "retry_after": 30}, 429
        if name == "ApiError" or (result.get("ok") and data.get("result") in (False, "false")):
            return {"ok": False, "code": "rejected", "message": cause or "디시에서 글을 받지 않았어요."}, 200
        # 워커가 디시와 통신하다 실패했으면 디시가 이미 받았을 수 있다.
        if op in {"write", "upvote"}:
            message = ("디시에 보냈지만 결과를 확인하지 못했어요. 다시 보내기 전에 목록을 확인해 주세요."
                       if op == "write" else "추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.")
            return {"ok": False, "code": "uncertain", "message": message}, 503
        return unavailable(), 503
    finally:
        _action_lock.release()


def unavailable():
    return {"ok": False, "code": "unavailable", "message": "디시 연결을 사용할 수 없어요. 잠시 후 다시 시도해 주세요."}
