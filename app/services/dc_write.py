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
from datetime import datetime
from html import unescape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE = "mirror_write"
COOKIE_AGE = 180 * 24 * 60 * 60
LIMITS = dict(subject_max=40, body_max=10000, comment_max=400, nickname_max=20, password_min=2, password_max=20,
              image_max_count=10, image_max_bytes=10 * 1024 * 1024, image_total_bytes=30 * 1024 * 1024,
              poll_title_max=40, poll_item_max=30, poll_items_max=20,
              video_max_count=3, video_max_bytes=20 * 1024 * 1024,
              upload_total_bytes=30 * 1024 * 1024)
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


class InvalidVideo(ValueError):
    pass


class InvalidWriteField(ValueError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def _future_date(value, *, five_minutes=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", value):
        return False
    try:
        date = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul"))
    except ValueError:
        return False
    return date > datetime.now(ZoneInfo("Asia/Seoul")) and (not five_minutes or date.minute % 5 == 0)


def new_post_fields(data, *, account):
    """Validate new-post-only JSON before creating uploads or contacting DC."""
    def invalid(field, message):
        raise InvalidWriteField(field, message)

    html = data.get("html", False)
    if type(html) is not bool:
        invalid("body", "HTML 사용 여부를 다시 선택해 주세요.")
    if html and isinstance(data.get("body"), str):
        body = unescape(data["body"])
        if re.search(r"<\s*(?:script|iframe|object|embed|form)\b|\bon\w+\s*=", body, re.I) or re.search(
                r"javascript\s*:", re.sub(r"[\x00-\x20]", "", body), re.I):
            invalid("body", "HTML에 스크립트, 외부 삽입 요소나 실행 가능한 속성을 넣을 수 없어요.")
    options = data.get("options", {})
    if not isinstance(options, dict) or any(type(options.get(key, False)) is not bool for key in ("no_repost", "secret")):
        invalid("options", "글 설정을 다시 확인해 주세요.")
    if "secret_password" in options and (not options.get("secret") or not isinstance(options["secret_password"], str)
                                         or not 2 <= len(options["secret_password"]) <= 20):
        invalid("options", "비밀글 비밀번호는 비밀글에서만 2~20자로 입력해 주세요.")
    if "auto_delete" in options and not _future_date(options["auto_delete"], five_minutes=True):
        invalid("options", "자동 삭제 시간은 서울 시간 기준 미래 시각을 5분 단위로 입력해 주세요.")
    poll = data.get("poll")
    if "poll" in data:
        if not isinstance(poll, dict):
            invalid("poll", "투표 설정을 다시 확인해 주세요.")
        title, items = poll.get("title"), poll.get("items")
        if not isinstance(title, str) or not title.strip() or len(title) > LIMITS["poll_title_max"]:
            invalid("poll", "투표 제목은 1~40자로 입력해 주세요.")
        if not isinstance(items, list) or not 2 <= len(items) <= LIMITS["poll_items_max"] or any(
                not isinstance(item, str) or not item.strip() or len(item) > LIMITS["poll_item_max"] for item in items):
            invalid("poll", "투표 항목은 1~30자로 2~20개 입력해 주세요.")
        multiple = poll.get("multiple", 0)
        if type(multiple) is not int or (multiple != 0 and not 2 <= multiple <= len(items)):
            invalid("poll", "복수 선택 수는 0 또는 2부터 항목 수까지 입력해 주세요.")
        if type(poll.get("preview", False)) is not bool:
            invalid("poll", "투표 결과 미리보기 설정을 다시 확인해 주세요.")
        if "end_date" in poll and not _future_date(poll["end_date"]):
            invalid("poll", "투표 마감은 서울 시간 기준 미래 시각으로 입력해 주세요.")
    dccons = data.get("dccons", [])
    if not isinstance(dccons, list) or (dccons and not account) or any(
            not isinstance(icon, dict) or any(type(icon.get(key)) is not int or not 1 <= icon[key] <= 9007199254740991
                                              for key in ("package_idx", "detail_idx")) for icon in dccons):
        invalid("dccons", "디시콘은 로그인한 계정에서 올바른 항목을 선택해 주세요.")
    # Pass only supported fields; credentials and arbitrary library options never cross this boundary.
    result = {}
    for key in ("html", "options", "poll", "dccons"):
        if key in data:
            result[key] = data[key]
    return result


# 본문 한 줄이 "[사진 N]"뿐이면 그 자리에 N번째 사진을 넣는다.
PHOTO_MARKER = re.compile(r"^[ \t]*\[사진[ ]?([0-9]{1,2})\][ \t]*$")
MODIFY_MARKER = re.compile(r"^[ \t]*\[(사진|원본)[ ]?([0-9]{1,2})\][ \t]*$")
CONTENT_MARKER = re.compile(r"^[ \t]*\[(사진|디시콘|동영상)[ ]?([0-9]{1,5})\][ \t]*$")


def photo_blocks(body, count, position="top", *, photos=None, originals=None, dccons=0, videos=0):
    """Split the body at photo markers into ordered text/image blocks.

    Photos without a marker keep the old top/bottom placement so older clients still work.
    """
    modifying = photos is not None
    if modifying:
        originals = originals if originals is not None else []
        if not isinstance(photos, list) or not isinstance(originals, list):
            raise InvalidPhotoMarker("사진과 원본 목록을 다시 확인해 주세요.")
        photo_entries, keeps, uploads = [], set(), set()
        for photo in photos:
            if not isinstance(photo, dict) or len(photo) != 1:
                raise InvalidPhotoMarker("사진 목록을 다시 확인해 주세요.")
            key = next(iter(photo))
            index = photo[key]
            if key not in ("keep", "new") or type(index) is not int or index < 0:
                raise InvalidPhotoMarker("사진 목록을 다시 확인해 주세요.")
            used_indexes = keeps if key == "keep" else uploads
            if index in used_indexes:
                raise InvalidPhotoMarker("같은 사진이나 원본을 두 번 넣을 수 없어요.")
            used_indexes.add(index)
            photo_entries.append({"keep" if key == "keep" else "image": index})
        for index in originals:
            if type(index) is not int or index < 0 or index in keeps:
                raise InvalidPhotoMarker("원본 목록을 다시 확인해 주세요.")
            keeps.add(index)
        if uploads != set(range(count)):
            raise InvalidPhotoMarker("새 사진 목록과 올린 파일이 맞지 않아요.")
        entries = {"사진": photo_entries, "원본": [{"keep": index} for index in originals]}
    else:
        entries = {"사진": [{"image": index} for index in range(count)]}
        if dccons or videos:
            entries.update(디시콘=[{"dccon": index} for index in range(dccons)],
                           동영상=[{"video": index} for index in range(videos)])
    blocks, used, lines = [], set(), []

    def flush():
        text = "\n".join(lines).strip("\n")
        if text.strip():
            blocks.append({"text": text})
        lines.clear()

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = (MODIFY_MARKER if modifying else CONTENT_MARKER if dccons or videos else PHOTO_MARKER).match(line)
        if not match:
            lines.append(line)
            continue
        label, number = (match.group(1), int(match.group(2))) if modifying or dccons or videos else ("사진", int(match.group(1)))
        if not 1 <= number <= len(entries[label]):
            raise InvalidPhotoMarker(f"본문의 [{label} {number}]에 맞는 {label}이 없어요. 그 줄을 지우거나 {label}을 넣어 주세요.")
        if (label, number) in used:
            raise InvalidPhotoMarker(f"본문에 [{label} {number}] 줄이 두 번 있어요. 하나만 남겨 주세요.")
        used.add((label, number))
        flush()
        blocks.append(entries[label][number - 1])
    flush()
    if dccons and any(("디시콘", index + 1) not in used for index in range(dccons)):
        raise InvalidPhotoMarker("선택한 디시콘마다 [디시콘 N] 줄을 한 번씩 넣어 주세요.")
    rest = [entry for label, items in entries.items() for index, entry in enumerate(items) if (label, index + 1) not in used]
    return rest + blocks if position == "top" and not modifying else blocks + rest


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
    with _prepared_uploads(files, video=False) as uploads:
        yield uploads


@contextmanager
def prepared_videos(files):
    with _prepared_uploads(files, video=True) as uploads:
        yield uploads


@contextmanager
def _prepared_uploads(files, *, video):
    """Keep private, server-named uploads alive only for this worker request."""
    if video and len(files) > LIMITS["video_max_count"]:
        raise InvalidVideo("동영상은 3개까지 올릴 수 있어요.")
    if not video and len(files) > LIMITS["image_max_count"]:
        raise InvalidImage("사진은 10장까지 올릴 수 있어요.")
    directory = None
    try:
        images, total = [], 0
        try:
            if files:
                directory = tempfile.mkdtemp(prefix="mirror-dcimg-", dir=current_app.config.get("DC_APP_UPLOADS"))
                os.chmod(directory, 0o700)
            for index, file in enumerate(files, 1):
                data = file.stream.read(LIMITS["video_max_bytes" if video else "image_max_bytes"] + 1)
                if video and len(data) > LIMITS["video_max_bytes"]:
                    raise InvalidVideo(f"{index}번째 동영상은 20MB까지 올릴 수 있어요.")
                if not video and len(data) > LIMITS["image_max_bytes"]:
                    raise InvalidImage(f"{index}번째 사진은 10MB까지 올릴 수 있어요.")
                total += len(data)
                if total > LIMITS["image_total_bytes"]:
                    if video:
                        raise InvalidVideo("사진과 동영상은 모두 합쳐 30MB까지 올릴 수 있어요.")
                    raise InvalidImage(IMAGE_TOTAL_MESSAGE)
                if video:
                    # The official app transcodes to MP4/H.264. Do not accept arbitrary
                    # video/* or trust browser MIME/filenames; this mirror does not transcode.
                    detected = ("video/mp4", "mp4") if len(data) >= 16 and data[4:8] == b"ftyp" and data[8:12] in (
                        b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ") else None
                    if detected is None:
                        raise InvalidVideo(f"{index}번째 동영상은 MP4 파일만 올릴 수 있어요.")
                else:
                    detected = _image_format(data)
                if detected is None:
                    raise InvalidImage(f"{index}번째 파일은 JPG, PNG, GIF, WEBP 사진만 올릴 수 있어요.")
                content_type, extension = detected
                path = Path(directory).resolve() / f"{secrets.token_hex(16)}.{extension}"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(data)
                images.append({"path": str(path), "type": content_type,
                               "name": f"{'video' if video else 'image'}{index}.{extension}"})
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


def account_configured():
    return bool(account_slots())


def account_slots():
    return [slot for slot, suffix in (("1", ""), ("2", "_2"))
            if os.environ.get(f"MIRROR_DC_LOGIN_ID{suffix}") and os.environ.get(f"MIRROR_DC_LOGIN_PASSWORD{suffix}")]


def account_fields(data):
    """Only public session metadata can cross the worker boundary."""
    return {key: data.get(key) for key in ("configured", "logged_in", "user_id", "name", "blocked", "reason")}


def account_status():
    try:
        result = get_manager().request("account_status", deadline=time.monotonic() + 8)
    except WorkerUnavailable:
        return unavailable(), 503
    if not result.get("ok"):
        return unavailable(), 503
    data = result["data"]
    accounts = data.get("accounts", {})
    return {"ok": True, **account_fields(data),
            "accounts": {slot: account_fields(fields) for slot, fields in accounts.items()
                         if slot in ("1", "2") and isinstance(fields, dict)}}, 200


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
    "comment": "댓글을 보냈지만 결과를 확인하지 못했어요. 다시 쓰기 전에 글을 새로 열어 댓글을 확인해 주세요.",
    "delete": "글 삭제 결과를 확인하지 못했어요. 다시 삭제하기 전에 목록을 확인해 주세요.",
    "comment_delete": "댓글 삭제 결과를 확인하지 못했어요. 다시 삭제하기 전에 글을 새로 열어 댓글을 확인해 주세요.",
    "modify": "글 수정 결과를 확인하지 못했어요. 다시 수정하기 전에 글을 새로 열어 확인해 주세요.",
    "upvote": "추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.",
    "downvote": "비추천 결과를 확인하지 못했어요. 글을 새로 열어 확인해 주세요.",
}
_last_success = {}
# 성공한 뒤 같은 동작을 다시 보낼 수 있을 때까지 기다리는 시간(초).
INTERVALS = {"write": 20, "comment": 10, "modify": 20, "delete": 3, "comment_delete": 3, "modify_info": 1, "dccons": 0}
LOGIN_MESSAGES = {
    "otp": "디시 OTP 6자리를 입력해 주세요.",
    "captcha": "로그인 보안 문자를 입력해 주세요.",
    "failed": "디시 계정에 로그인하지 못했어요. 계정 설정을 확인하고 다시 로그인해 주세요.",
    "expired": "디시 로그인이 만료됐어요. 계정 화면에서 다시 로그인해 주세요.",
}

UPDATE_REQUIRED = "디시가 앱 업데이트를 요구해요. 미러 서버의 앱 버전을 올려야 해요."


def update_required(result):
    """Accept only the worker's fixed category, never its upstream text."""
    failure = result.get("error", {}) if not result.get("ok") else result.get("data", {})
    if failure.get("reason") == "update_required" and (
            not result.get("ok") or failure.get("result") in (False, "false")):
        return {"ok": False, "code": "update_required", "message": UPDATE_REQUIRED}
    return None


def perform(op, **payload):
    deadline = time.monotonic() + (55 if op in ("write", "modify") and (payload.get("images") or payload.get("videos")) else 40)
    # The interval check and success timestamp must surround the serialized call.
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not _action_lock.acquire(timeout=remaining):
        return unavailable(), 503
    try:
        if time.monotonic() >= deadline:
            return unavailable(), 503
        # Login pacing belongs solely to the worker, which also owns lazy login.
        if op != "account_login":
            interval = INTERVALS.get(op, 3)
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
        data = dict(result.get("data", {}))
        error = dict(result.get("error", {}))
        # Diagnostics are worker-only metadata, never a cause or browser field.
        data_diag = data.pop("diag_cause", None)
        error_diag = error.pop("diag_cause", None)
        diag = error_diag if not result.get("ok") else data_diag
        if op in SENDS and payload.get("session", {}).get("type") == "login" and isinstance(diag, str) and diag:
            current_app.logger.warning("dc %s board=%s no=%s diag=%r",
                                       op, payload.get("gallery", "-"), payload.get("no", "-"), diag[:200])
        update = update_required(result)
        if update:
            return update, 200
        if error.get("name") == "AccountInvalid":
            return {"ok": False, "code": "invalid", "field": "mode", "message": "계정을 다시 선택해 주세요."}, 400
        if error.get("name") == "LoginCooldown":
            return {"ok": False, "code": "retry", "message": "잠시 후 다시 시도해 주세요.",
                    "retry_after": error["retry_after"]}, 429
        if error.get("name") == "LoginNeeded":
            reason = error.get("reason")
            reason = reason if reason in LOGIN_MESSAGES else "failed"
            account = error.get("account")
            account = account if account in ("1", "2") else "1"
            failure = {"ok": False, "code": "login_needed", "reason": reason, "account": account,
                       "message": LOGIN_MESSAGES[reason]}
            if reason == "captcha" and isinstance(error.get("captcha_key"), str) and CAPTCHA_KEY.fullmatch(error["captcha_key"]):
                failure["captcha_key"] = error["captcha_key"]
            return failure, 200
        if result.get("ok") and data.get("result") in (True, "true"):
            if op != "account_login":
                _last_success[op] = time.monotonic()
            return {"ok": True, "data": data}, 200
        cause = error.get("cause") if not result.get("ok") else data.get("cause")
        cause = cause if isinstance(cause, str) else ""
        name = error.get("name")
        if payload.get("session", {}).get("type") == "login" and cause and cause not in (
                "stale", "잠시후 다시 이용해주세요.", "잠시 후 다시 시도해 주세요."):
            cause = "보안 코드를 입력해 주세요." if name == "CaptchaRequiredError" else "디시에서 요청을 받지 않았어요."
        if op == "modify" and name == "WorkerError" and error.get("sent") is False and cause == "stale":
            return {"ok": False, "code": "rejected", "reason": "stale",
                    "message": "글이 그사이 바뀌었어요. 수정 화면을 다시 열어 주세요."}, 200
        if error.get("sent") is False and name not in ("ApiError", "CaptchaRequiredError"):
            return unavailable(), 503
        if name == "CaptchaRequiredError" or (cause and re.search(r"captcha|보안\s?코드|자동\s?입력|코드가\s?설정된\s?갤러리", cause, re.I)):
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


# 글의 추천 설정(비추천 사용, 추천 보안 문자). 앱 API의 글 보기 응답이 기준이다.
# 비추천은 말머리마다 꺼질 수 있어서 갤러리가 아니라 글 단위로 저장한다.
VOTE_OPTIONS_TTL = 10 * 60
VOTE_OPTIONS_MAX = 2000
_vote_options = {}
_vote_options_lock = threading.Lock()


def cached_vote_options(board, no):
    with _vote_options_lock:
        entry = _vote_options.get((board, no))
        if entry and entry[0] > time.monotonic():
            return entry[1]
    return None


def vote_options(board, no):
    """Return options, a fixed update-required failure, or None on other failures."""
    cached = cached_vote_options(board, no)
    if cached is not None:
        return cached
    try:
        result = get_manager().request("vote_info", deadline=time.monotonic() + 8, gallery=board, no=no)
    except WorkerUnavailable:
        return None
    update = update_required(result)
    if update:
        return update
    data = result.get("data") if result.get("ok") else None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), bool) for key in ("down", "captcha")):
        return None
    options = {"down": data["down"], "captcha": data["captcha"]}
    with _vote_options_lock:
        now = time.monotonic()
        if len(_vote_options) >= VOTE_OPTIONS_MAX:
            for key in [key for key, entry in _vote_options.items() if entry[0] <= now]:
                del _vote_options[key]
            while len(_vote_options) >= VOTE_OPTIONS_MAX:
                del _vote_options[next(iter(_vote_options))]
        _vote_options[(board, no)] = (now + VOTE_OPTIONS_TTL, options)
    return options
