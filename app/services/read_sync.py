"""Single-user read history. Sequence numbers track changes, not reading order."""
import fcntl
import json
import logging
import re
import secrets
import time
from pathlib import Path

from flask import current_app

from . import state_file
from .dc_links import BOARD_ID_RE

MAX_ITEMS = 1500
FUTURE_GRACE_MS = 5 * 60 * 1000
log = logging.getLogger(__name__)


class ReadSyncError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.status = 400 if code == "invalid" else 503


def _valid_key(key):
    if re.fullmatch(r"poker:[1-9][0-9]{0,11}", key):
        return True
    board, _, pid = key.partition("|")
    return BOARD_ID_RE.fullmatch(board) and re.fullmatch(r"0*[1-9][0-9]*", pid)


def _uint(value):
    return type(value) is int and value >= 0


def _items(value):
    return isinstance(value, dict) and len(value) <= MAX_ITEMS and all(_valid_key(k) for k in value)


def _load(path):
    if path.is_symlink():
        raise ValueError("symlink")
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
    except FileNotFoundError:
        return None
    if (not isinstance(state, dict) or set(state) != {"id", "rev", "items"}
            or not isinstance(state["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", state["id"])
            or not _uint(state["rev"]) or not _items(state["items"])):
        raise ValueError("invalid state")
    seen = set()
    for entry in state["items"].values():
        if (not isinstance(entry, list) or len(entry) != 2 or not _uint(entry[0])
                or not _uint(entry[1]) or not 0 < entry[1] <= state["rev"] or entry[1] in seen):
            raise ValueError("invalid entry")
        seen.add(entry[1])
    return state


def sync(payload):
    if (not isinstance(payload, dict) or not _uint(payload.get("since"))
            or (payload.get("id") is not None and not isinstance(payload["id"], str))
            or not _items(payload.get("items")) or not all(_uint(t) for t in payload["items"].values())):
        raise ReadSyncError("invalid")
    path = Path(current_app.config.get("READ_SYNC_STATE") or Path(current_app.instance_path) / "read_sync.json")
    try:
        with state_file.lock_file(path, ".lock") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = _load(path)
            created = state is None
            if created:
                state = {"id": secrets.token_hex(16), "rev": 0, "items": {}}
            rev, items = state["rev"], state["items"]
            client_id = payload.get("id")
            since = payload["since"]
            reset = since > rev or (client_id != state["id"] if client_id is not None else since > 0)
            if reset:
                since = 0
            now = time.time_ns() // 1_000_000
            for key, at in payload["items"].items():
                if at > now + FUTURE_GRACE_MS:
                    at = now
                if key not in items or at > items[key][0]:
                    rev += 1
                    items[key] = [at, rev]
            if created or rev != state["rev"]:
                if len(items) > MAX_ITEMS:
                    newest = sorted(items, key=lambda k: (-items[k][0], k))[:MAX_ITEMS]
                    items = {k: items[k] for k in newest}
                state_file.save(path, {"id": state["id"], "rev": rev, "items": items})
            return {"ok": True, "id": state["id"], "rev": rev, "reset": reset,
                    "items": {k: at for k, (at, seq) in items.items() if seq > since}}
    except (OSError, ValueError, RecursionError) as exc:
        log.error("read sync state unavailable type=%s", type(exc).__name__)
        raise ReadSyncError("unavailable") from None
