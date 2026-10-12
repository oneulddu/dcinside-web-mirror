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
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import urlsplit

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE = "mirror_write"
COOKIE_AGE = 180 * 24 * 60 * 60
LIMITS = dict(subject_max=40, body_max=10000, nickname_max=20, password_min=2, password_max=20,
              image_max_count=10, image_max_bytes=10 * 1024 * 1024, image_total_bytes=30 * 1024 * 1024)
IMAGE_TOTAL_MESSAGE = "사진은 모두 합쳐 30MB까지 올릴 수 있어요."
CAPTCHA_KEY = re.compile(r"^[0-9a-f]{16}$")
MAX_IMAGE = 200 * 1024


class WorkerUnavailable(Exception):
    def __init__(self, sent=False):
        super().__init__()
        self.sent = sent


class InvalidImage(ValueError):
    pass


class InvalidPhotoMarker(ValueError):
    pass


# 본문 한 줄이 "[사진 N]"뿐이면 그 자리에 N번째 사진을 넣는다.
PHOTO_MARKER = re.compile(r"^[ \t]*\[사진[ ]?([0-9]{1,2})\][ \t]*$")


def photo_blocks(body, count, position="top"):
    """Split the body at photo markers into ordered text/image blocks.

    Photos without a marker keep the old top/bottom placement so older clients still work.
    """
    blocks, used, lines = [], set(), []

    def flush():
        text = "\n".join(lines).strip("\n")
        if text.strip():
            blocks.append({"text": text})
        lines.clear()

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = PHOTO_MARKER.match(line)
        if not match:
            lines.append(line)
            continue
        number = int(match.group(1))
        if not 1 <= number <= count:
            raise InvalidPhotoMarker(f"본문의 [사진 {number}]에 맞는 사진이 없어요. 그 줄을 지우거나 사진을 넣어 주세요.")
        if number in used:
            raise InvalidPhotoMarker(f"본문에 [사진 {number}] 줄이 두 번 있어요. 하나만 남겨 주세요.")
        used.add(number)
        flush()
        blocks.append({"image": number - 1})
    flush()
    rest = [{"image": index} for index in range(count) if index + 1 not in used]
    return rest + blocks if position == "top" else blocks + rest


def _image_format(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


@contextmanager
def prepared_images(files):
    """Keep private, server-named uploads alive only for this worker request."""
    if len(files) > LIMITS["image_max_count"]:
        raise InvalidImage("사진은 10장까지 올릴 수 있어요.")
    directory = None
    try:
        images, total = [], 0
        try:
            if files:
                directory = tempfile.mkdtemp(prefix="mirror-dcimg-", dir=current_app.config.get("DC_APP_UPLOADS"))
                os.chmod(directory, 0o700)
            for index, file in enumerate(files, 1):
                data = file.stream.read(LIMITS["image_max_bytes"] + 1)
                if len(data) > LIMITS["image_max_bytes"]:
                    raise InvalidImage(f"{index}번째 사진은 10MB까지 올릴 수 있어요.")
                total += len(data)
                if total > LIMITS["image_total_bytes"]:
                    raise InvalidImage(IMAGE_TOTAL_MESSAGE)
                detected = _image_format(data)
                if detected is None:
                    raise InvalidImage(f"{index}번째 파일은 JPG, PNG, GIF, WEBP 사진만 올릴 수 있어요.")
                content_type, extension = detected
                path = Path(directory).resolve() / f"{secrets.token_hex(16)}.{extension}"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(data)
                images.append({"path": str(path), "type": content_type, "name": f"image{index}.{extension}"})
        except OSError:
            # Filesystem exceptions can contain private paths; do not surface them.
            raise WorkerUnavailable() from None
        yield images
    finally:
        if directory is not None:
            # A cleanup failure must not turn a completed write into an error response.
            shutil.rmtree(directory, ignore_errors=True)


class WorkerManager:
    def __init__(self, node=None, directory=None, state=None, timeout=40, proxy=None):
        self.node = node if node is not None else shutil.which("node")
        self.directory = Path(directory or Path(__file__).resolve().parents[2] / "dc_app")
        self.state = str(state or self.directory.parent / "instance/dc_app_credentials.json")
        self.timeout = timeout
        self.proxy = proxy
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
                    env = worker_env(self.state, self.proxy)
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


_PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "NODE_USE_ENV_PROXY")


def worker_env(state, proxy=None):
    """Worker environment. With a proxy, every worker request (DC and app_id issuance) goes through it."""
    env = {key: value for key, value in os.environ.items() if key not in _PROXY_ENV}
    env["MIRROR_DC_APP_STATE"] = state
    if proxy:
        env.update(NODE_USE_ENV_PROXY="1", HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    return env


RELAY_DOWN = "집 중계기에 연결할 수 없어 지금은 디시에 보낼 수 없어요. 잠시 후 다시 시도해 주세요."


def relay_reachable(proxy, timeout=2):
    """Quick TCP check so an offline relay is reported as not sent instead of uncertain."""
    try:
        parts = urlsplit(proxy)
        if not parts.hostname:
            return False
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout):
            return True
    except (OSError, ValueError, TypeError):
        return False


_manager_lock = threading.Lock()
_managers = {}


def get_manager():
    config = current_app.config
    options = (config.get("DC_APP_NODE", shutil.which("node")), config.get("DC_APP_DIR"), config.get("DC_APP_STATE"),
               40, config.get("DC_APP_PROXY"))
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
# 디시에 보낸 뒤 결과를 모르면 다시 보내지 않도록 안내할 동작과 그 문구.
SENDS = {
    "write": "디시에 보냈지만 결과를 확인하지 못했어요. 다시 보내기 전에 목록을 확인해 주세요.",
    "upvote": "추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.",
    "downvote": "비추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.",
}
_last_success = {}


def perform(op, **payload):
    deadline = time.monotonic() + (55 if op == "write" and payload.get("images") else 40)
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
        proxy = current_app.config.get("DC_APP_PROXY")
        if proxy and not relay_reachable(proxy):
            return {"ok": False, "code": "unavailable", "reason": "relay_down", "message": RELAY_DOWN}, 503
        try:
            result = get_manager().request(op, deadline=deadline, **payload)
        except WorkerUnavailable as exc:
            if exc.sent and op in SENDS:
                return {"ok": False, "code": "uncertain", "message": SENDS[op]}, 503
            return unavailable(), 503
        data = result.get("data", {})
        error = result.get("error", {})
        if result.get("ok") and data.get("result") in (True, "true"):
            _last_success[op] = time.monotonic()
            return {"ok": True, "data": data}, 200
        cause = error.get("cause") if not result.get("ok") else data.get("cause")
        cause = cause if isinstance(cause, str) else ""
        name = error.get("name")
        if name == "WorkerError" and error.get("sent") is False:
            return unavailable(), 503
        if name == "CaptchaRequiredError" or (cause and re.search(r"captcha|보안\s?코드|자동\s?입력", cause, re.I)):
            return {"ok": False, "code": "captcha_required", "message": cause or "보안 코드를 입력해 주세요."}, 200
        if cause == "잠시후 다시 이용해주세요.":
            return {"ok": False, "code": "retry", "message": cause, "retry_after": 30}, 429
        # 짧은 시간에 여러 번 보내면 디시가 이 문구로 막는다. 거절이 아니라 기다렸다가 다시 보낼 일이다.
        if re.fullmatch(r"잠시\s?후\s?다시\s?시도해\s?주세요\.?", cause):
            return {"ok": False, "code": "retry", "message": cause, "retry_after": 30}, 429
        if name == "ApiError" or (result.get("ok") and data.get("result") in (False, "false")):
            rejected = {"ok": False, "code": "rejected", "message": cause or "디시에서 글을 받지 않았어요.", "cause": cause}
            # 디시가 접속 위치(서버 IP)를 VPN·통신사 차단 대상으로 본 경우다. 다시 눌러도 같다.
            if error.get("vpn_ban") is True or data.get("carrier_vpn_ban") in (True, "true"):
                rejected.update(reason="ip_blocked", message="디시가 이 미러 서버의 접속 위치(IP)를 막아서 받지 않았어요.")
            return rejected, 200
        # 워커가 디시와 통신하다 실패했으면 디시가 이미 받았을 수 있다.
        if op in SENDS:
            return {"ok": False, "code": "uncertain", "message": SENDS[op]}, 503
        return unavailable(), 503
    finally:
        _action_lock.release()


def unavailable():
    return {"ok": False, "code": "unavailable", "message": "디시 연결을 사용할 수 없어요. 잠시 후 다시 시도해 주세요."}


# 갤러리의 추천 설정(비추천 사용, 추천 보안 문자). 앱 API의 글 보기 응답이 기준이다.
VOTE_OPTIONS_TTL = 30 * 60
_vote_options = {}
_vote_options_lock = threading.Lock()


def cached_vote_options(board):
    with _vote_options_lock:
        entry = _vote_options.get(board)
        if entry and entry[0] > time.monotonic():
            return entry[1]
    return None


def vote_options(board, no):
    """Return {"down", "captcha"} for a gallery, or None when DC could not be read."""
    cached = cached_vote_options(board)
    if cached is not None:
        return cached
    try:
        result = get_manager().request("vote_info", deadline=time.monotonic() + 8, gallery=board, no=no)
    except WorkerUnavailable:
        return None
    data = result.get("data") if result.get("ok") else None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), bool) for key in ("down", "captcha")):
        return None
    options = {"down": data["down"], "captcha": data["captcha"]}
    with _vote_options_lock:
        _vote_options[board] = (time.monotonic() + VOTE_OPTIONS_TTL, options)
    return options
