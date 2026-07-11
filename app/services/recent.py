import base64
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import request


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


RECENT_COOKIE_NAME = "recent_galleries"
RECENT_CACHE_KEY_COOKIE_NAME = "recent_galleries_key"
RECENT_COOKIE_TTL = _env_int("MIRROR_RECENT_COOKIE_TTL", 60 * 60 * 24 * 30)
RECENT_MAX_ITEMS = _env_int("MIRROR_RECENT_MAX_ITEMS", 30)
RECENT_SERVER_CACHE_TTL = _env_int("MIRROR_RECENT_SERVER_CACHE_TTL", min(RECENT_COOKIE_TTL, 60 * 60 * 24))
RECENT_SERVER_CACHE_MAX_KEYS = _env_int("MIRROR_RECENT_SERVER_CACHE_MAX_KEYS", 2048)
RECENT_COOKIE_MAX_BYTES = _env_int("MIRROR_RECENT_COOKIE_MAX_BYTES", 3600)
RECENT_SERVER_CACHE = {}
RECENT_SERVER_CACHE_LOCK = threading.Lock()
RECENT_CACHE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RECENT_GALLERY_KINDS = {"minor", "mini", "person"}


def format_recent_time(ts):
    if not ts:
        return "-"
    kst = timezone(timedelta(hours=9))
    try:
        parsed = float(ts)
        if not math.isfinite(parsed):
            return "-"
        return datetime.fromtimestamp(parsed, tz=kst).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "-"


def recent_cache_key(create=False):
    raw = (request.cookies.get(RECENT_CACHE_KEY_COOKIE_NAME) or "").strip()
    if RECENT_CACHE_KEY_RE.fullmatch(raw):
        return raw
    if create:
        return secrets.token_urlsafe(24)
    return None


def copy_recent_entries(entries):
    return [dict(row) for row in (entries or [])[:RECENT_MAX_ITEMS] if isinstance(row, dict)]


def normalize_recent_kind(value):
    kind = (value or "").strip().lower()
    if kind == "normal" or kind not in RECENT_GALLERY_KINDS:
        return None
    return kind


def normalize_recent_entry(item):
    if not isinstance(item, dict):
        return None

    board = (item.get("board") or "").strip()
    if not board:
        return None
    name = (item.get("name") or "").strip()
    if name == board:
        name = ""

    return {
        "board": board,
        "name": name[:80] or None,
        "kind": normalize_recent_kind(item.get("kind")),
        "recommend": 1 if _safe_int(item.get("recommend", 0), 0) == 1 else 0,
        "visited_at": _safe_float(item.get("visited_at", 0), 0.0),
    }


def recent_entry_identity(row):
    return (
        row.get("board"),
        row.get("kind"),
        1 if _safe_int(row.get("recommend", 0), 0) == 1 else 0,
    )


def recent_entry_visit_identity(row):
    return (
        row.get("board"),
        1 if _safe_int(row.get("recommend", 0), 0) == 1 else 0,
    )


def recent_entries_may_be_same_gallery(left, right):
    if recent_entry_identity(left) == recent_entry_identity(right):
        return True
    if recent_entry_visit_identity(left) != recent_entry_visit_identity(right):
        return False
    return not left.get("kind") or not right.get("kind")


def merge_recent_entry_detail(primary, secondary):
    secondary_kind = normalize_recent_kind(secondary.get("kind"))
    if not primary.get("kind") and secondary_kind:
        primary["kind"] = secondary_kind
    if not primary.get("name") and secondary.get("name"):
        primary["name"] = secondary.get("name")
    return primary


def merge_recent_entries(new_row, rows):
    deduped = [new_row]
    for row in rows:
        if recent_entries_may_be_same_gallery(new_row, row):
            merge_recent_entry_detail(new_row, row)
            continue
        deduped.append(row)
    return deduped[:RECENT_MAX_ITEMS]


def dedupe_recent_entries(rows):
    deduped = []
    for row in rows:
        for existing in deduped:
            if recent_entries_may_be_same_gallery(existing, row):
                merge_recent_entry_detail(existing, row)
                break
        else:
            deduped.append(row)
    return deduped[:RECENT_MAX_ITEMS]


def merge_recent_generations(primary_rows, secondary_rows):
    primary = copy_recent_entries(primary_rows)
    secondary = copy_recent_entries(secondary_rows)
    if not secondary:
        return dedupe_recent_entries(primary)

    combined = primary + secondary
    combined.sort(
        key=lambda row: _safe_float(row.get("visited_at", 0), 0.0),
        reverse=True,
    )
    return dedupe_recent_entries(combined)


def merge_recent_entry_names(rows, named_rows):
    if not rows or not named_rows:
        return rows

    names_by_identity = {
        recent_entry_identity(row): row.get("name")
        for row in named_rows
        if isinstance(row, dict) and row.get("name")
    }
    merged = []
    for row in rows:
        copied = dict(row)
        if not copied.get("name"):
            name = names_by_identity.get(recent_entry_identity(copied))
            if not name:
                for named_row in named_rows:
                    if (
                        isinstance(named_row, dict)
                        and named_row.get("name")
                        and recent_entries_may_be_same_gallery(copied, named_row)
                    ):
                        name = named_row.get("name")
                        break
            if name:
                copied["name"] = name
        if not copied.get("kind"):
            for named_row in named_rows:
                named_kind = normalize_recent_kind(named_row.get("kind")) if isinstance(named_row, dict) else None
                if (
                    isinstance(named_row, dict)
                    and named_kind
                    and recent_entries_may_be_same_gallery(copied, named_row)
                ):
                    copied["kind"] = named_kind
                    break
        merged.append(copied)
    return merged


def make_recent_server_cache_entry(entries, now, ttl):
    return {
        "entries": copy_recent_entries(entries),
        "expires_at": now + ttl,
        "last_seen": now,
    }


def prune_recent_server_cache_locked(now=None):
    now = time.time() if now is None else now
    expired_keys = [
        key
        for key, entry in RECENT_SERVER_CACHE.items()
        if (
            not isinstance(entry, dict)
            or float(entry.get("expires_at", 0.0) or 0.0) <= now
        )
    ]
    for key in expired_keys:
        RECENT_SERVER_CACHE.pop(key, None)

    max_keys = max(_safe_int(RECENT_SERVER_CACHE_MAX_KEYS, 0), 0)
    overflow = len(RECENT_SERVER_CACHE) - max_keys
    if overflow <= 0:
        return

    def last_seen_for(key):
        entry = RECENT_SERVER_CACHE[key]
        if isinstance(entry, dict):
            return float(entry.get("last_seen", 0.0) or 0.0)
        return 0.0

    oldest_keys = sorted(
        RECENT_SERVER_CACHE,
        key=last_seen_for,
    )[:overflow]
    for key in oldest_keys:
        RECENT_SERVER_CACHE.pop(key, None)


def get_recent_server_cache(key):
    if not key:
        return []

    now = time.time()
    with RECENT_SERVER_CACHE_LOCK:
        entry = RECENT_SERVER_CACHE.get(key)
        if not entry:
            return []
        if not isinstance(entry, dict):
            RECENT_SERVER_CACHE.pop(key, None)
            return []

        expires_at = float(entry.get("expires_at", 0.0) or 0.0)
        if expires_at <= now:
            RECENT_SERVER_CACHE.pop(key, None)
            return []

        entry["last_seen"] = now
        return copy_recent_entries(entry.get("entries", []))


def set_recent_server_cache(key, entries):
    if not key:
        return copy_recent_entries(entries)

    ttl = max(_safe_int(RECENT_SERVER_CACHE_TTL, 0), 0)
    max_keys = max(_safe_int(RECENT_SERVER_CACHE_MAX_KEYS, 0), 0)
    if ttl <= 0 or max_keys <= 0:
        return copy_recent_entries(entries)

    now = time.time()
    with RECENT_SERVER_CACHE_LOCK:
        current = RECENT_SERVER_CACHE.get(key)
        current_rows = []
        if isinstance(current, dict) and float(current.get("expires_at", 0.0) or 0.0) > now:
            current_rows = current.get("entries", [])

        merged = merge_recent_generations(entries, current_rows)
        RECENT_SERVER_CACHE[key] = make_recent_server_cache_entry(merged, now, ttl)
        prune_recent_server_cache_locked(now)
        return copy_recent_entries(merged)


def replace_recent_server_cache(key, entries):
    rows = dedupe_recent_entries(copy_recent_entries(entries))
    if not key:
        return rows

    ttl = max(_safe_int(RECENT_SERVER_CACHE_TTL, 0), 0)
    max_keys = max(_safe_int(RECENT_SERVER_CACHE_MAX_KEYS, 0), 0)
    now = time.time()
    with RECENT_SERVER_CACHE_LOCK:
        if not rows or ttl <= 0 or max_keys <= 0:
            RECENT_SERVER_CACHE.pop(key, None)
            return rows

        RECENT_SERVER_CACHE[key] = make_recent_server_cache_entry(rows, now, ttl)
        prune_recent_server_cache_locked(now)
        return rows


def load_recent_entries():
    raw = request.cookies.get(RECENT_COOKIE_NAME, "")
    rows = []
    if raw:
        raw = raw.strip().strip('"')
        decoded = raw
        try:
            padded = raw + "=" * (-len(raw) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except Exception:
            decoded = raw.replace("\\054", ",")
        try:
            parsed = json.loads(decoded)
        except Exception:
            parsed = []

        if isinstance(parsed, list):
            for item in parsed:
                row = normalize_recent_entry(item)
                if row:
                    rows.append(row)

    server_rows = get_recent_server_cache(recent_cache_key())
    if rows:
        cookie_rows = merge_recent_entry_names(rows, server_rows)
        return merge_recent_generations(cookie_rows, server_rows)

    return dedupe_recent_entries(server_rows)


def _encode_recent_rows(rows):
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _fit_recent_cookie_value(rows):
    max_bytes = max(_safe_int(RECENT_COOKIE_MAX_BYTES, 0), 0)
    encoded = _encode_recent_rows(rows)
    if len(encoded.encode("ascii")) <= max_bytes:
        return encoded

    compact_rows = []
    for row in rows:
        compact = dict(row)
        compact.pop("name", None)
        compact_rows.append(compact)

    encoded = _encode_recent_rows(compact_rows)
    if len(encoded.encode("ascii")) <= max_bytes:
        return encoded

    low = 0
    high = len(compact_rows)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = _encode_recent_rows(compact_rows[:middle])
        if len(candidate.encode("ascii")) <= max_bytes:
            low = middle
        else:
            high = middle - 1

    encoded = _encode_recent_rows(compact_rows[:low])
    if len(encoded.encode("ascii")) <= max_bytes:
        return encoded
    return ""


def save_recent_cookie(response, entries):
    rows = entries[:RECENT_MAX_ITEMS]
    encoded = _fit_recent_cookie_value(rows)
    response.set_cookie(
        RECENT_COOKIE_NAME,
        encoded,
        max_age=RECENT_COOKIE_TTL,
        path="/",
        samesite="Lax",
        secure=request.is_secure,
    )


def save_recent_cache_key_cookie(response, key):
    if not key:
        return
    response.set_cookie(
        RECENT_CACHE_KEY_COOKIE_NAME,
        key,
        max_age=RECENT_COOKIE_TTL,
        path="/",
        samesite="Lax",
        secure=request.is_secure,
        httponly=True,
    )


def touch_recent_gallery(response, board, kind, recommend=0, name=None):
    board_id = (board or "").strip()
    if not board_id:
        return

    cache_key = recent_cache_key(create=True)
    rows = load_recent_entries()
    new_row = normalize_recent_entry({
        "board": board_id,
        "name": name,
        "kind": (kind or "").strip().lower() or None,
        "recommend": 1 if _safe_int(recommend, 0) == 1 else 0,
        "visited_at": time.time(),
    })
    deduped = merge_recent_entries(new_row, rows)
    deduped = set_recent_server_cache(cache_key, deduped)

    save_recent_cache_key_cookie(response, cache_key)
    save_recent_cookie(response, deduped)


def remove_recent_gallery(response, board, kind, recommend=0):
    board_id = (board or "").strip()
    if not board_id:
        return False

    target = normalize_recent_entry({
        "board": board_id,
        "kind": (kind or "").strip().lower() or None,
        "recommend": 1 if _safe_int(recommend, 0) == 1 else 0,
    })
    rows = load_recent_entries()
    remaining = [row for row in rows if not recent_entries_may_be_same_gallery(target, row)]
    removed = len(remaining) != len(rows)

    remaining = replace_recent_server_cache(recent_cache_key(), remaining)
    save_recent_cookie(response, remaining)
    return removed


def clear_recent_galleries(response):
    replace_recent_server_cache(recent_cache_key(), [])
    save_recent_cookie(response, [])
