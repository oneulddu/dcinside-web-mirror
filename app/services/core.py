import asyncio
import logging
import os
import re
import threading
import time

from .dc import api as dc_api
from .async_bridge import dc_api_context
from .singleflight import claim_flight
from .cache_utils import cache_get as _shared_cache_get
from .cache_utils import cache_prune as _shared_cache_prune
from .cache_utils import env_int as _env_int
from .cache_utils import safe_int as _safe_int

logger = logging.getLogger(__name__)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


MAX_PAGE = 31
RELATED_LIMIT = 12
DOCS_PER_PAGE_ESTIMATE = max(int(getattr(dc_api, "BOARD_LIST_PAGE_SIZE", 30)), 1)
RELATED_PAGE_FETCH_SIZE = DOCS_PER_PAGE_ESTIMATE
RELATED_PAGE_PROBE_STEPS = max(_env_int("MIRROR_RELATED_PAGE_PROBE_STEPS", 4), 1)
RELATED_TAIL_PAGES = max(_env_int("MIRROR_RELATED_TAIL_PAGES", 1), 0)
BOARD_PAGE_CACHE_TTL = max(_env_int("MIRROR_BOARD_PAGE_CACHE_TTL", 20), 0)
BOARD_FORCE_REFRESH_COOLDOWN = max(_env_int("MIRROR_BOARD_FORCE_REFRESH_COOLDOWN", 5), 0)
BOARD_TIME_CACHE_TTL = max(_env_int("MIRROR_BOARD_TIME_CACHE_TTL", BOARD_PAGE_CACHE_TTL), 0)
READ_CACHE_TTL = max(_env_int("MIRROR_READ_CACHE_TTL", 30), 0)
READ_STALE_TTL = max(_env_int("MIRROR_READ_STALE_TTL", 300), 0)
READ_FETCH_TIMEOUT = max(_env_int("MIRROR_READ_FETCH_TIMEOUT", 50), 1)
READ_SINGLEFLIGHT_TIMEOUT = max(
    _env_int("MIRROR_READ_SINGLEFLIGHT_TIMEOUT", 55),
    1,
)
BOARD_FILL_AUTHOR_CODES = _env_bool("MIRROR_BOARD_FILL_AUTHOR_CODES", False)
LATEST_ID_CACHE_TTL = 20
AUTHOR_CODE_CACHE_TTL = 3600
BOARD_PAGE_CACHE_MAX_ITEMS = 2048
BOARD_INDEX_CACHE_MAX_ITEMS = 2048
BOARD_TIME_CACHE_MAX_ITEMS = BOARD_PAGE_CACHE_MAX_ITEMS
READ_CACHE_MAX_ITEMS = 512
LATEST_ID_CACHE_MAX_ITEMS = 512
AUTHOR_CODE_CACHE_MAX_ITEMS = 8192
CACHE_PRUNE_EVERY = max(_env_int("MIRROR_CACHE_PRUNE_EVERY", 64), 1)
CACHE_PRUNE_MIN_INTERVAL = max(_env_int("MIRROR_CACHE_PRUNE_MIN_INTERVAL", 1), 0)

_BOARD_PAGE_CACHE = {}
_BOARD_INDEX_CACHE = {}
_BOARD_REFRESH_CACHE = {}
_BOARD_TIME_CACHE = {}
_READ_CACHE = {}
_READ_STALE_CACHE = {}
_READ_INFLIGHT = {}
_BOARD_INFLIGHT = {}
_LATEST_ID_CACHE = {}
_AUTHOR_CODE_CACHE = {}
_BOARD_PAGE_CACHE_LOCK = threading.Lock()
_BOARD_INDEX_CACHE_LOCK = threading.Lock()
_BOARD_TIME_CACHE_LOCK = threading.Lock()
_READ_CACHE_LOCK = threading.Lock()
_READ_STALE_CACHE_LOCK = threading.Lock()
_READ_INFLIGHT_LOCK = threading.Lock()
_BOARD_INFLIGHT_LOCK = threading.Lock()
_LATEST_ID_CACHE_LOCK = threading.Lock()
_AUTHOR_CODE_CACHE_LOCK = threading.Lock()
_CACHE_PRUNE_STATE = {}
_CACHE_PRUNE_STATE_LOCK = threading.Lock()
_AUTHOR_CODE_SUFFIX_RE = re.compile(r"\(([^()\s]{1,64})\)\s*$")
_AUTHOR_CODE_OPEN_RE = re.compile(r"\(([^()\s]{1,64})$")
_ANON_NAME_RE = re.compile(r"ㅇㅇ(\d*)")
_TIME_SECONDS_RE = re.compile(r"(\b\d{1,2}:\d{2}):\d{2}(?:\.\d+)?")


def _clean_author_code(code):
    value = (code or "").strip()
    if not value:
        return None
    if value.startswith("(") and value.endswith(")") and len(value) > 2:
        value = value[1:-1].strip()
    return value or None


def _split_name_and_inline_code(author):
    raw = (author or "").strip()
    if not raw:
        return "", None

    # Prefer clean "(code)" suffix, but also tolerate malformed trailing "(code".
    matched = _AUTHOR_CODE_SUFFIX_RE.search(raw)
    if matched:
        return raw[:matched.start()].strip(), matched.group(1).strip()
    matched = _AUTHOR_CODE_OPEN_RE.search(raw)
    if matched:
        return raw[:matched.start()].strip(), matched.group(1).strip()
    return raw, None


def _normalize_author(author, author_id=None):
    author = (author or "").replace("\u00ad", "").replace("&shy;", "")
    name, inline_code = _split_name_and_inline_code(author)
    code = _clean_author_code(author_id) or _clean_author_code(inline_code)
    if not name:
        return "익명", code
    anon_match = _ANON_NAME_RE.fullmatch(name)
    if anon_match:
        suffix = anon_match.group(1) or ""
        return f"익명{suffix}", code
    if name.endswith("갤러"):
        return "익명", code
    return name, code


def _normalize_author_role(role):
    value = str(role or "").strip().lower()
    if value in {"manager", "submanager"}:
        return value
    return None


def _is_reply_comment(parent_id):
    value = str(parent_id or "").strip().lower()
    if value in {"", "0", "1", "none", "null"}:
        return False
    try:
        # Mobile comment payload uses "m_no", where 0/1 is not a reply thread id.
        return int(value) > 1
    except (TypeError, ValueError):
        return False


def format_display_time(value):
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
    text = str(value or "").strip()
    if not text:
        return "-"
    return _TIME_SECONDS_RE.sub(r"\1", text)


def _comment_to_dict(comment):
    comment_author, comment_author_code = _normalize_author(comment.author, comment.author_id)
    is_reply = bool(getattr(comment, "is_reply", False)) or _is_reply_comment(comment.parent_id)
    return {
        "id": str(getattr(comment, "id", "") or "").strip(),
        "time": format_display_time(comment.time),
        "contents": comment.contents,
        "author": comment_author,
        "author_code": comment_author_code,
        "author_role": _normalize_author_role(getattr(comment, "author_role", None)),
        "parent_id": comment.parent_id,
        "is_reply": is_reply,
        "dccon": comment.dccon,
    }


def _index_time_display(item):
    raw_time = (getattr(item, "time_text", None) or "").strip()
    if not bool(getattr(item, "time_is_precise", True)):
        return raw_time or "-"
    return format_display_time(getattr(item, "time", None))


def _index_item_to_dict(item):
    author, author_code = _normalize_author(item.author, getattr(item, "author_id", None))
    needs_time_hydrate = not bool(getattr(item, "time_is_precise", True))
    return {
        "id": item.id,
        "subject": getattr(item, "subject", None),
        "title": item.title,
        "has_image": bool(getattr(item, "has_image", False) or getattr(item, "isimage", False)),
        "has_video": bool(getattr(item, "has_video", False) or getattr(item, "isvideo", False)),
        "author": author,
        "author_code": author_code,
        "author_role": _normalize_author_role(getattr(item, "author_role", None)),
        "time": format_display_time(item.time),
        "time_display": _index_time_display(item),
        "needs_time_hydrate": needs_time_hydrate,
        "comment_count": item.comment_count,
        "voteup_count": item.voteup_count,
        "view_count": item.view_count,
        "isimage": item.isimage,
        "isvideo": bool(getattr(item, "isvideo", False)),
        "isrecommend": item.isrecommend,
        "isdcbest": item.isdcbest,
        "ishit": item.ishit,
        "is_mobile_source": bool(getattr(item, "is_mobile_source", False)),
    }


def _cache_get(cache, lock, key):
    return _shared_cache_get(cache, lock, key)


def _cache_prune(cache, now, max_items):
    _shared_cache_prune(cache, now, max_items)


def _should_prune_cache(cache, now, max_items):
    with _CACHE_PRUNE_STATE_LOCK:
        state = _CACHE_PRUNE_STATE.setdefault(id(cache), {"sets": 0, "last_pruned_at": 0.0})
        state["sets"] += 1
        should_prune = (
            state["sets"] >= CACHE_PRUNE_EVERY
            or now - float(state["last_pruned_at"] or 0.0) >= CACHE_PRUNE_MIN_INTERVAL
            or len(cache) > max(max_items, 0)
        )
        if should_prune:
            state["sets"] = 0
            state["last_pruned_at"] = now
        return should_prune


def _cache_set(cache, lock, key, value, ttl, max_items):
    expires_at = time.time() + max(_safe_int(ttl, 0), 0)
    with lock:
        cache[key] = {"value": value, "expires_at": expires_at}
        now = time.time()
        if _should_prune_cache(cache, now, max_items):
            _cache_prune(cache, now, max_items)


def _claim_board_force_refresh(cache_key):
    if BOARD_FORCE_REFRESH_COOLDOWN <= 0:
        return True

    now = time.time()
    with _BOARD_INDEX_CACHE_LOCK:
        entry = _BOARD_REFRESH_CACHE.get(cache_key)
        if entry and entry["expires_at"] > now:
            return False
        _BOARD_REFRESH_CACHE[cache_key] = {
            "value": True,
            "expires_at": now + BOARD_FORCE_REFRESH_COOLDOWN,
        }
        _cache_prune(_BOARD_REFRESH_CACHE, now, BOARD_INDEX_CACHE_MAX_ITEMS)
        return True


def _copy_rows(rows):
    return [dict(row) for row in (rows or [])]


def _copy_categories(categories):
    return [dict(row) for row in (categories or [])]


def _copy_pagination(pagination):
    return dict(pagination or {})


def _copy_board_payload(payload, pagination_collector=None):
    rows, categories = payload[:2]
    pagination = payload[2] if len(payload) >= 3 else {}
    if pagination_collector is not None:
        pagination_collector.update(_copy_pagination(pagination))
    return _copy_rows(rows), _copy_categories(categories)


def _copy_read_payload(payload):
    data, comments, images = payload
    copied_data = dict(data or {})
    if "related_posts" in copied_data:
        copied_data["related_posts"] = _copy_rows(copied_data.get("related_posts"))
    return copied_data, _copy_rows(comments), list(images or [])


def _copy_read_payload_for_cache(payload):
    copied_data, copied_comments, copied_images = _copy_read_payload(payload)
    copied_data["related_posts"] = []
    return copied_data, copied_comments, copied_images


def _is_read_payload_cacheable(payload):
    data, _comments, _images = payload
    return bool((data or {}).get("_comments_complete", True))


def _copy_read_flight_error(error):
    if isinstance(error, dc_api.DocumentNotFoundError):
        return dc_api.DocumentNotFoundError(str(error))
    if isinstance(error, dc_api.DocumentUnavailableError):
        return dc_api.DocumentUnavailableError(str(error))
    return dc_api.DocumentUnavailableError("concurrent document fetch failed")


async def _wait_for_read_flight(flight):
    try:
        payload, error = await flight.wait(READ_SINGLEFLIGHT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise dc_api.DocumentUnavailableError("document fetch wait timed out") from exc
    if error is not None:
        raise _copy_read_flight_error(error)
    if payload is None:
        raise dc_api.DocumentUnavailableError("document fetch completed without a payload")
    return _copy_read_payload(payload)


async def _load_board_once(key, load):
    with claim_flight(_BOARD_INFLIGHT, _BOARD_INFLIGHT_LOCK, key) as (flight, is_owner):
        if not is_owner:
            payload, error = await flight.wait()
            if error is not None:
                raise dc_api.DocumentUnavailableError("concurrent board fetch failed") from error
            return payload
        flight.value = await load()
        return flight.value


def _board_index_cache_key(
    page,
    board,
    recommend,
    kind=None,
    fetch_num=MAX_PAGE,
    scan_limit=None,
    document_id_upper_limit=None,
    document_id_lower_limit=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
):
    return (
        board,
        kind or "",
        _safe_int(recommend, 0),
        _safe_int(page, 1),
        _safe_int(fetch_num, MAX_PAGE),
        None if scan_limit is None else _safe_int(scan_limit, 0),
        "" if document_id_upper_limit is None else str(document_id_upper_limit).strip(),
        "" if document_id_lower_limit is None else str(document_id_lower_limit).strip(),
        (search_type or "").strip(),
        (search_keyword or "").strip(),
        "" if head_id is None else str(head_id).strip(),
    )


def _read_cache_key(api_id, board, kind=None, recommend=0, search_type=None, search_keyword=None, head_id=None):
    return board, kind or "", str(api_id)


def _author_code_cache_key(board, kind, doc_id):
    return (board, kind or "", str(doc_id))


def _cache_author_code(board, kind, doc_id, author, author_code, author_role=None):
    if not doc_id:
        return
    _cache_set(
        _AUTHOR_CODE_CACHE,
        _AUTHOR_CODE_CACHE_LOCK,
        _author_code_cache_key(board, kind, doc_id),
        {
            "author": author,
            "author_code": author_code,
            "author_role": _normalize_author_role(author_role),
        },
        AUTHOR_CODE_CACHE_TTL,
        AUTHOR_CODE_CACHE_MAX_ITEMS,
    )


async def _fetch_board_page(
    api,
    page,
    board,
    recommend,
    kind=None,
    page_size=RELATED_PAGE_FETCH_SIZE,
    search_type=None,
    search_keyword=None,
    head_id=None,
):
    cache_key = (
        board,
        kind or "",
        _safe_int(recommend, 0),
        _safe_int(page, 1),
        _safe_int(page_size, RELATED_PAGE_FETCH_SIZE),
        (search_type or "").strip(),
        (search_keyword or "").strip(),
        "" if head_id is None else str(head_id).strip(),
    )
    cached = _cache_get(_BOARD_PAGE_CACHE, _BOARD_PAGE_CACHE_LOCK, cache_key)
    if cached is not None:
        return _copy_rows(cached)

    async def load():
        cached = _cache_get(_BOARD_PAGE_CACHE, _BOARD_PAGE_CACHE_LOCK, cache_key)
        if cached is not None:
            return cached

        posts = []
        async for item in api.board(
            board_id=board,
            num=page_size,
            start_page=page,
            recommend=recommend,
            kind=kind,
            max_scan_pages=1,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
            headtexts_collector=[],
        ):
            row = _index_item_to_dict(item)
            row["source_page"] = _safe_int(page, 1)
            posts.append(row)
        if posts:
            _cache_set(
                _BOARD_PAGE_CACHE,
                _BOARD_PAGE_CACHE_LOCK,
                cache_key,
                _copy_rows(posts),
                BOARD_PAGE_CACHE_TTL,
                BOARD_PAGE_CACHE_MAX_ITEMS,
            )
        return posts

    return _copy_rows(await _load_board_once(("page", cache_key), load))


def _normalize_target_ids(target_ids):
    return tuple(str(value).strip() for value in (target_ids or []) if str(value).strip())


def _board_time_cache_key(board, kind, recommend, page, search_type=None, search_keyword=None, head_id=None, target_ids=None):
    return (
        board,
        kind or "",
        _safe_int(recommend, 0),
        _safe_int(page, 1),
        (search_type or "").strip(),
        (search_keyword or "").strip(),
        "" if head_id is None else str(head_id).strip(),
        _normalize_target_ids(target_ids),
    )


async def async_board_precise_times(
    page,
    board,
    recommend,
    kind=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    target_ids=None,
):
    normalized_target_ids = _normalize_target_ids(target_ids)
    cache_key = _board_time_cache_key(
        board,
        kind,
        recommend,
        page,
        search_type=search_type,
        search_keyword=search_keyword,
        head_id=head_id,
        target_ids=normalized_target_ids,
    )
    cached = _cache_get(_BOARD_TIME_CACHE, _BOARD_TIME_CACHE_LOCK, cache_key)
    if cached is not None:
        return dict(cached)

    async def load():
        cached = _cache_get(_BOARD_TIME_CACHE, _BOARD_TIME_CACHE_LOCK, cache_key)
        if cached is not None:
            return cached

        async with dc_api_context() as api:
            precise_times = await api.board_precise_times(
                board_id=board,
                page=page,
                recommend=bool(_safe_int(recommend, 0)),
                kind=kind,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
                target_ids=normalized_target_ids,
            )

        result = {str(doc_id): format_display_time(value) for doc_id, value in (precise_times or {}).items()}
        _cache_set(
            _BOARD_TIME_CACHE,
            _BOARD_TIME_CACHE_LOCK,
            cache_key,
            dict(result),
            BOARD_TIME_CACHE_TTL,
            BOARD_TIME_CACHE_MAX_ITEMS,
        )
        return result

    return dict(await _load_board_once(("times", cache_key), load))


def _normalize_head_category(row):
    if not row:
        return None
    head_id = row.get("head_id")
    if head_id is not None:
        head_id = str(head_id).strip()
        if not head_id:
            head_id = None
    return {
        "head_id": head_id,
        "label": row.get("label") or "전체",
        "active": bool(row.get("active")),
    }


def _normalize_head_categories(rows, head_id=None):
    categories = []
    seen = set()
    for row in rows or []:
        category = _normalize_head_category(row)
        if not category:
            continue
        key = "" if category["head_id"] is None else category["head_id"]
        if key in seen:
            continue
        seen.add(key)
        categories.append(category)

    if not categories:
        return []

    current_key = "" if head_id is None else str(head_id).strip()
    has_active = False
    for category in categories:
        key = "" if category["head_id"] is None else category["head_id"]
        category["active"] = key == current_key
        has_active = has_active or category["active"]
    if not has_active:
        categories[0]["active"] = True
    return categories


async def _fill_missing_author_code(api, board, kind, row, recommend=0, allow_fetch=True):
    if not row:
        return row
    if row.get("author_code"):
        return row
    doc_id = row.get("id")
    if not doc_id:
        return row
    cache_key = _author_code_cache_key(board, kind, doc_id)
    cached = _cache_get(_AUTHOR_CODE_CACHE, _AUTHOR_CODE_CACHE_LOCK, cache_key)
    if cached is not None:
        row["author"] = cached.get("author", row.get("author"))
        row["author_code"] = cached.get("author_code")
        if cached.get("author_role"):
            row["author_role"] = cached.get("author_role")
        return row
    if not allow_fetch:
        return row
    if row.get("is_mobile_source"):
        return row
    try:
        doc = await api.document(board_id=board, document_id=doc_id, kind=kind, recommend=bool(_safe_int(recommend, 0)))
    except Exception:
        return row
    if not doc:
        return row
    author, author_code = _normalize_author(doc.author, doc.author_id)
    row["author"] = author
    row["author_code"] = author_code
    row["author_role"] = _normalize_author_role(getattr(doc, "author_role", None))
    _cache_author_code(board, kind, doc_id, author, author_code, row["author_role"])
    return row


async def _fill_missing_author_codes(api, board, kind, rows, recommend=0):
    if not BOARD_FILL_AUTHOR_CODES:
        return rows

    for row in rows:
        await _fill_missing_author_code(api, board, kind, row, recommend=recommend, allow_fetch=False)
    return rows


async def _read_document_with_api(api, api_id, board, kind=None, recommend=0, search_type=None, search_keyword=None, head_id=None):
    data = {}
    comments = []
    images = []
    doc = await api.document(
        board_id=board,
        document_id=api_id,
        kind=kind,
        recommend=bool(_safe_int(recommend, 0)),
        search_type=search_type,
        search_keyword=search_keyword,
        head_id=head_id,
    )
    if doc is None:
        raise dc_api.DocumentUnavailableError("document parser returned no payload")
    author, author_code = _normalize_author(doc.author, doc.author_id)
    author_role = _normalize_author_role(getattr(doc, "author_role", None))
    _cache_author_code(board, kind, api_id, author, author_code, author_role)
    data = {
        "title": doc.title,
        "author": author,
        "author_code": author_code,
        "author_role": author_role,
        "time": format_display_time(doc.time),
        "voteup_count": doc.voteup_count,
        "contents": getattr(doc, "contents", ""),
        "html": doc.html,
        "related_posts": [_index_item_to_dict(item) for item in getattr(doc, "related_posts", [])],
        "_comment_prefer_mobile": bool(getattr(doc, "is_mobile_source", True)),
    }
    seen_comment_ids = set()
    embedded_comments = list(getattr(doc, "embedded_comments", []) or [])
    embedded_total = _safe_int(getattr(doc, "embedded_comment_total", 0), 0)
    for com in embedded_comments:
        comment_id = str(getattr(com, "id", "") or "").strip()
        if comment_id:
            seen_comment_ids.add(comment_id)
        comments.append(_comment_to_dict(com))

    should_fetch_comments = (
        not embedded_comments
        or embedded_total <= 0
        or embedded_total > len(embedded_comments)
    )
    comments_complete = not should_fetch_comments
    if should_fetch_comments:
        async for com in doc.comments():
            comment_id = str(getattr(com, "id", "") or "").strip()
            if comment_id and comment_id in seen_comment_ids:
                continue
            if comment_id:
                seen_comment_ids.add(comment_id)
            comments.append(_comment_to_dict(com))
        comment_status = getattr(doc, "comment_status", None)
        comments_complete = True if comment_status is None else bool(comment_status.get("complete"))
    data["_comments_complete"] = comments_complete
    for img in doc.images:
        images.append(img.src)
    return data, comments, images


async def _refresh_cached_comments(payload, api_id, board, kind=None):
    data, comments, images = _copy_read_payload(payload)
    if data.get("_comments_complete"):
        return data, comments, images

    seen_comment_ids = {
        str(comment.get("id") or "").strip()
        for comment in comments
        if str(comment.get("id") or "").strip()
    }
    status = {}
    try:
        async with dc_api_context() as api:
            async for comment in api.comments(
                board,
                api_id,
                kind=kind,
                prefer_mobile=bool(data.get("_comment_prefer_mobile", True)),
                status_collector=status,
            ):
                comment_id = str(getattr(comment, "id", "") or "").strip()
                if comment_id and comment_id in seen_comment_ids:
                    continue
                if comment_id:
                    seen_comment_ids.add(comment_id)
                comments.append(_comment_to_dict(comment))
    except Exception as exc:
        logger.info(
            "cached comment refresh failed: board=%s document=%s reason=%s",
            board,
            api_id,
            type(exc).__name__,
        )
    data["_comments_complete"] = bool(status.get("complete"))
    return data, comments, images


async def _load_read_payload(
    cache_key,
    api_id,
    board,
    kind=None,
    recommend=0,
    search_type=None,
    search_keyword=None,
    head_id=None,
):
    body_cached = _cache_get(_READ_STALE_CACHE, _READ_STALE_CACHE_LOCK, cache_key)
    if body_cached is not None:
        refreshed = await _refresh_cached_comments(body_cached, api_id, board, kind=kind)
        refreshed[0]["_body_from_cache"] = True
        return refreshed

    async with dc_api_context() as api:
        return await _read_document_with_api(
            api,
            api_id,
            board,
            kind=kind,
            recommend=recommend,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
        )


async def async_read(api_id, board, kind=None, recommend=0, search_type=None, search_keyword=None, head_id=None):
    cache_key = _read_cache_key(
        api_id,
        board,
        kind=kind,
        recommend=recommend,
        search_type=search_type,
        search_keyword=search_keyword,
        head_id=head_id,
    )
    if READ_CACHE_TTL > 0:
        cached = _cache_get(_READ_CACHE, _READ_CACHE_LOCK, cache_key)
        if cached is not None:
            return _copy_read_payload(cached)

    with claim_flight(_READ_INFLIGHT, _READ_INFLIGHT_LOCK, cache_key) as (flight, is_owner):
        if not is_owner:
            return await _wait_for_read_flight(flight)

        try:
            if READ_CACHE_TTL > 0:
                cached = _cache_get(_READ_CACHE, _READ_CACHE_LOCK, cache_key)
                if cached is not None:
                    flight.value = _copy_read_payload(cached)
                    return _copy_read_payload(cached)

            payload = await asyncio.wait_for(
                _load_read_payload(
                    cache_key,
                    api_id,
                    board,
                    kind=kind,
                    recommend=recommend,
                    search_type=search_type,
                    search_keyword=search_keyword,
                    head_id=head_id,
                ),
                timeout=READ_FETCH_TIMEOUT,
            )
            body_from_cache = bool(payload[0].pop("_body_from_cache", False))
            body_cache_payload = _copy_read_payload_for_cache(payload)
            if _is_read_payload_cacheable(payload):
                cache_payload = body_cache_payload
            else:
                cache_payload = None
            if READ_CACHE_TTL > 0 and cache_payload is not None:
                _cache_set(
                    _READ_CACHE,
                    _READ_CACHE_LOCK,
                    cache_key,
                    cache_payload,
                    READ_CACHE_TTL,
                    READ_CACHE_MAX_ITEMS,
                )
            if READ_STALE_TTL > 0 and not body_from_cache:
                _cache_set(
                    _READ_STALE_CACHE,
                    _READ_STALE_CACHE_LOCK,
                    cache_key,
                    body_cache_payload,
                    READ_STALE_TTL,
                    READ_CACHE_MAX_ITEMS,
                )
            flight.value = body_cache_payload
            return payload
        except asyncio.TimeoutError as exc:
            stale = _cache_get(_READ_STALE_CACHE, _READ_STALE_CACHE_LOCK, cache_key)
            if stale is not None:
                stale_payload = _copy_read_payload(stale)
                stale_payload[0]["_served_stale"] = True
                flight.value = _copy_read_payload(stale_payload)
                logger.warning("serving cached document after comment refresh timeout: board=%s document=%s", board, api_id)
                return stale_payload
            timeout_error = dc_api.DocumentUnavailableError("document fetch timed out")
            flight.error = timeout_error
            raise timeout_error from exc
        except dc_api.DocumentUnavailableError as exc:
            stale = _cache_get(_READ_STALE_CACHE, _READ_STALE_CACHE_LOCK, cache_key)
            if stale is not None:
                stale_payload = _copy_read_payload(stale)
                stale_payload[0]["_served_stale"] = True
                flight.value = _copy_read_payload(stale_payload)
                logger.warning("serving stale document after upstream failure: board=%s document=%s", board, api_id)
                return stale_payload
            flight.error = exc
            raise
        except asyncio.CancelledError:
            flight.error = dc_api.DocumentUnavailableError("document fetch was cancelled")
            raise
        except Exception as exc:
            flight.error = exc
            raise


async def async_index_with_head_categories(
    page,
    board,
    recommend,
    kind=None,
    document_id_upper_limit=None,
    document_id_lower_limit=None,
    limit=None,
    max_scan_pages=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    pagination_collector=None,
    force_refresh=False,
):
    if pagination_collector is not None:
        pagination_collector.clear()
    if limit is None:
        fetch_num = MAX_PAGE
    else:
        try:
            fetch_num = max(int(limit), 0)
        except (TypeError, ValueError):
            fetch_num = MAX_PAGE
    if fetch_num == 0:
        return [], []
    if max_scan_pages is None:
        scan_limit = None
    else:
        try:
            scan_limit = max(int(max_scan_pages), 0)
        except (TypeError, ValueError):
            scan_limit = None

    cache_key = _board_index_cache_key(
        page,
        board,
        recommend,
        kind=kind,
        fetch_num=fetch_num,
        scan_limit=scan_limit,
        document_id_upper_limit=document_id_upper_limit,
        document_id_lower_limit=document_id_lower_limit,
        search_type=search_type,
        search_keyword=search_keyword,
        head_id=head_id,
    )
    force_refresh_requested = bool(force_refresh)
    if force_refresh_requested:
        force_refresh = _claim_board_force_refresh(cache_key)
    if not force_refresh:
        cached = _cache_get(_BOARD_INDEX_CACHE, _BOARD_INDEX_CACHE_LOCK, cache_key)
        if cached is not None:
            return _copy_board_payload(cached, pagination_collector)

    async def load():
        if not force_refresh:
            cached = _cache_get(_BOARD_INDEX_CACHE, _BOARD_INDEX_CACHE_LOCK, cache_key)
            if cached is not None:
                return cached

        data = []
        headtexts = []
        pagination = {}
        async with dc_api_context() as api:
            async for item in api.board(
                board_id=board,
                num=fetch_num,
                start_page=page,
                recommend=recommend,
                kind=kind,
                document_id_upper_limit=document_id_upper_limit,
                document_id_lower_limit=document_id_lower_limit,
                max_scan_pages=scan_limit,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
                headtexts_collector=headtexts,
                pagination_collector=pagination,
            ):
                data.append(_index_item_to_dict(item))
            await _fill_missing_author_codes(api, board, kind, data, recommend=recommend)
            categories = _normalize_head_categories(headtexts, head_id=head_id)
        if data or categories or (force_refresh_requested and force_refresh):
            cache_ttl = BOARD_PAGE_CACHE_TTL
            if not data and not categories:
                cache_ttl = min(BOARD_PAGE_CACHE_TTL, BOARD_FORCE_REFRESH_COOLDOWN)
            _cache_set(
                _BOARD_INDEX_CACHE,
                _BOARD_INDEX_CACHE_LOCK,
                cache_key,
                (_copy_rows(data), _copy_categories(categories), _copy_pagination(pagination)),
                cache_ttl,
                BOARD_INDEX_CACHE_MAX_ITEMS,
            )
        return data, categories, pagination

    # An explicit refresh owns a different empty-result policy. Keep it apart
    # from ordinary fetches, including when it joins during a cold-cache miss.
    payload = await _load_board_once(("index", cache_key, force_refresh_requested), load)
    return _copy_board_payload(payload, pagination_collector)


async def _related_after_position_with_api(
    api,
    api_id,
    after_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
    source_page=None,
    recommend=0,
    search_type=None,
    search_keyword=None,
    head_id=None,
):
    current_id = _safe_int(api_id, 0)
    target_id = _safe_int(after_id, 0) or current_id
    fetch_limit = max(_safe_int(limit, RELATED_LIMIT), 0)
    max_probe = max(_safe_int(probe_steps, RELATED_PAGE_PROBE_STEPS), 1)
    max_tail = max(_safe_int(tail_pages, RELATED_TAIL_PAGES), 0)
    source_page_value = _safe_int(source_page, 0)
    recommend_value = _safe_int(recommend, 0)
    search_keyword_value = (search_keyword or "").strip()
    search_type_value = (search_type or "").strip()
    head_id_value = "" if head_id is None else str(head_id).strip()

    if target_id <= 0 or fetch_limit == 0:
        return [], False

    board_key = (board, kind or "", recommend_value, search_type_value, search_keyword_value, head_id_value)

    async def estimate_page_from_latest_id():
        if recommend_value:
            return 1

        latest_id = _cache_get(_LATEST_ID_CACHE, _LATEST_ID_CACHE_LOCK, board_key)
        if latest_id is None:
            first_page = await _fetch_board_page(
                api,
                1,
                board,
                recommend_value,
                kind=kind,
                page_size=1,
                search_type=search_type_value,
                search_keyword=search_keyword_value,
                head_id=head_id_value or None,
            )
            if not first_page:
                return None
            latest_id = _safe_int(first_page[0].get("id"), target_id)
            _cache_set(
                _LATEST_ID_CACHE,
                _LATEST_ID_CACHE_LOCK,
                board_key,
                latest_id,
                LATEST_ID_CACHE_TTL,
                LATEST_ID_CACHE_MAX_ITEMS,
            )
        return max(1, ((latest_id - target_id) // DOCS_PER_PAGE_ESTIMATE) + 1)

    async def find_target_from_page(start_page):
        page = max(_safe_int(start_page, 1), 1)
        checked = set()
        steps = 0

        while steps < max_probe and page >= 1:
            if page in checked:
                break
            checked.add(page)
            steps += 1

            page_posts = await _fetch_board_page(
                api,
                page,
                board,
                recommend_value,
                kind=kind,
                search_type=search_type_value,
                search_keyword=search_keyword_value,
                head_id=head_id_value or None,
            )
            if not page_posts:
                break

            page_ids = [_safe_int(row.get("id"), 0) for row in page_posts]
            if target_id in page_ids:
                return page, page_ids.index(target_id), page_posts

            valid_ids = [pid for pid in page_ids if pid > 0]
            if not valid_ids:
                break

            if recommend_value:
                # Recommended posts must follow the actual recommended list
                # order. Do not infer page movement from numeric post ids.
                page += 1
                continue

            page_max = max(valid_ids)
            page_min = min(valid_ids)
            if target_id > page_max:
                page = max(1, page - 1)
            elif target_id < page_min:
                page += 1
            else:
                page += 1

        return None, -1, []

    found_page = None
    found_index = -1
    found_posts = []

    attempted_candidate_pages = set()
    candidate_pages = [source_page_value] if source_page_value > 0 else []
    for candidate_page in candidate_pages:
        attempted_candidate_pages.add(candidate_page)
        found_page, found_index, found_posts = await find_target_from_page(candidate_page)
        if found_page is not None:
            break

    if found_page is None:
        fallback_pages = []
        estimated_page = await estimate_page_from_latest_id()
        if estimated_page:
            fallback_pages.append(estimated_page)
        if recommend_value:
            fallback_pages.append(1)
        if not fallback_pages:
            fallback_pages.append(1)

        for candidate_page in fallback_pages:
            candidate_page = max(_safe_int(candidate_page, 1), 1)
            if candidate_page in attempted_candidate_pages:
                continue
            attempted_candidate_pages.add(candidate_page)
            found_page, found_index, found_posts = await find_target_from_page(candidate_page)
            if found_page is not None:
                break

    if found_page is None:
        return [], False

    collect_limit = fetch_limit + 1
    related = []
    seen_ids = {str(current_id)}
    for row in found_posts[: found_index + 1]:
        prefix_id = _safe_int(row.get("id"), 0)
        if prefix_id > 0:
            seen_ids.add(str(prefix_id))

    def append_rows(rows):
        for row in rows:
            rid = _safe_int(row.get("id"), 0)
            if rid <= 0:
                continue
            rid_key = str(rid)
            if rid_key in seen_ids:
                continue
            seen_ids.add(rid_key)
            related.append(row)
            if len(related) >= collect_limit:
                return True
        return False

    append_rows(found_posts[found_index + 1 :])

    next_page = found_page + 1
    loaded_tail = 0
    while len(related) < collect_limit and loaded_tail < max_tail:
        page_posts = await _fetch_board_page(
            api,
            next_page,
            board,
            recommend_value,
            kind=kind,
            search_type=search_type_value,
            search_keyword=search_keyword_value,
            head_id=head_id_value or None,
        )
        if not page_posts:
            break
        append_rows(page_posts)
        next_page += 1
        loaded_tail += 1

    return related[:fetch_limit], len(related) > fetch_limit


async def async_related_after_position(
    api_id,
    after_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
    source_page=None,
    recommend=0,
    search_type=None,
    search_keyword=None,
    head_id=None,
):
    async with dc_api_context() as api:
        return await _related_after_position_with_api(
            api,
            api_id,
            after_id,
            board,
            kind=kind,
            limit=limit,
            probe_steps=probe_steps,
            tail_pages=tail_pages,
            source_page=source_page,
            recommend=recommend,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
        )
