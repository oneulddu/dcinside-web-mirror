import os
import re
import threading
import time

from .dc import api as dc_api
from .async_bridge import dc_api_context
from .cache_utils import cache_get as _shared_cache_get
from .cache_utils import cache_prune as _shared_cache_prune
from .cache_utils import env_int as _env_int
from .cache_utils import safe_int as _safe_int


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


MAX_PAGE = 31
RELATED_LIMIT = 12
DOCS_PER_PAGE_ESTIMATE = max(int(getattr(dc_api, "DOCS_PER_PAGE", 200)), 1)
RELATED_PAGE_FETCH_SIZE = DOCS_PER_PAGE_ESTIMATE
RELATED_PAGE_PROBE_STEPS = max(_env_int("MIRROR_RELATED_PAGE_PROBE_STEPS", 4), 1)
RELATED_TAIL_PAGES = max(_env_int("MIRROR_RELATED_TAIL_PAGES", 1), 0)
BOARD_PAGE_CACHE_TTL = max(_env_int("MIRROR_BOARD_PAGE_CACHE_TTL", 20), 0)
BOARD_TIME_CACHE_TTL = max(_env_int("MIRROR_BOARD_TIME_CACHE_TTL", BOARD_PAGE_CACHE_TTL), 0)
READ_CACHE_TTL = max(_env_int("MIRROR_READ_CACHE_TTL", 0), 0)
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
_BOARD_TIME_CACHE = {}
_READ_CACHE = {}
_LATEST_ID_CACHE = {}
_AUTHOR_CODE_CACHE = {}
_BOARD_PAGE_CACHE_LOCK = threading.Lock()
_BOARD_INDEX_CACHE_LOCK = threading.Lock()
_BOARD_TIME_CACHE_LOCK = threading.Lock()
_READ_CACHE_LOCK = threading.Lock()
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


def _copy_rows(rows):
    return [dict(row) for row in (rows or [])]


def _copy_categories(categories):
    return [dict(row) for row in (categories or [])]


def _normalize_search_pos(value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized or None


def _copy_read_payload(payload):
    data, comments, images = payload
    copied_data = dict(data or {})
    if "related_posts" in copied_data:
        copied_data["related_posts"] = _copy_rows(copied_data.get("related_posts"))
    return copied_data, _copy_rows(comments), list(images or [])


def _is_read_payload_cacheable(payload):
    data, _comments, _images = payload
    return (data or {}).get("html") != "게시글 데이터를 가져오는 데 실패했습니다."


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
    search_pos=None,
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
        _normalize_search_pos(search_pos),
    )


def _read_cache_key(api_id, board, kind=None, recommend=0, search_type=None, search_keyword=None, head_id=None):
    return (
        board,
        kind or "",
        str(api_id),
        _safe_int(recommend, 0),
        (search_type or "").strip(),
        (search_keyword or "").strip(),
        "" if head_id is None else str(head_id).strip(),
    )


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
    search_pos=None,
    search_nav_collector=None,
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
        _normalize_search_pos(search_pos),
    )
    cached = _cache_get(_BOARD_PAGE_CACHE, _BOARD_PAGE_CACHE_LOCK, cache_key)
    if cached is not None:
        if isinstance(cached, tuple) and len(cached) == 2:
            cached_rows, cached_search_nav = cached
        else:
            cached_rows, cached_search_nav = cached, None
        if search_nav_collector is not None:
            search_nav_collector.clear()
            if cached_search_nav is not None:
                search_nav_collector.update(cached_search_nav)
        return _copy_rows(cached_rows)

    posts = []
    search_nav = {} if search_nav_collector is not None or (search_keyword or "").strip() else None
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
        search_pos=search_pos,
        headtexts_collector=[],
        search_nav_collector=search_nav,
    ):
        row = _index_item_to_dict(item)
        row["source_page"] = _safe_int(page, 1)
        posts.append(row)
    if search_nav_collector is not None:
        search_nav_collector.clear()
        search_nav_collector.update(search_nav)
    if posts:
        _cache_set(
            _BOARD_PAGE_CACHE,
            _BOARD_PAGE_CACHE_LOCK,
            cache_key,
            (_copy_rows(posts), dict(search_nav) if search_nav is not None else None),
            BOARD_PAGE_CACHE_TTL,
            BOARD_PAGE_CACHE_MAX_ITEMS,
        )
    return posts


def _normalize_target_ids(target_ids):
    return tuple(str(value).strip() for value in (target_ids or []) if str(value).strip())


def _board_time_cache_key(board, kind, recommend, page, search_type=None, search_keyword=None, head_id=None, target_ids=None, search_pos=None):
    return (
        board,
        kind or "",
        _safe_int(recommend, 0),
        _safe_int(page, 1),
        (search_type or "").strip(),
        (search_keyword or "").strip(),
        "" if head_id is None else str(head_id).strip(),
        _normalize_search_pos(search_pos),
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
    search_pos=None,
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
        search_pos=search_pos,
    )
    cached = _cache_get(_BOARD_TIME_CACHE, _BOARD_TIME_CACHE_LOCK, cache_key)
    if cached is not None:
        return dict(cached)

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
            search_pos=search_pos,
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
        return {
            "title": "삭제되거나 찾을 수 없는 게시글입니다.",
            "author": "-",
            "author_code": None,
            "time": "-",
            "voteup_count": 0,
            "html": "게시글 데이터를 가져오는 데 실패했습니다.",
        }, [], []
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
    if should_fetch_comments:
        async for com in doc.comments():
            comment_id = str(getattr(com, "id", "") or "").strip()
            if comment_id and comment_id in seen_comment_ids:
                continue
            if comment_id:
                seen_comment_ids.add(comment_id)
            comments.append(_comment_to_dict(com))
    for img in doc.images:
        images.append(img.src)
    return data, comments, images


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

    async with dc_api_context() as api:
        payload = await _read_document_with_api(
            api,
            api_id,
            board,
            kind=kind,
            recommend=recommend,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
        )
    if READ_CACHE_TTL > 0 and _is_read_payload_cacheable(payload):
        _cache_set(
            _READ_CACHE,
            _READ_CACHE_LOCK,
            cache_key,
            _copy_read_payload(payload),
            READ_CACHE_TTL,
            READ_CACHE_MAX_ITEMS,
        )
    return payload


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
    search_pos=None,
):
    if limit is None:
        fetch_num = MAX_PAGE
    else:
        try:
            fetch_num = max(int(limit), 0)
        except (TypeError, ValueError):
            fetch_num = MAX_PAGE
    if fetch_num == 0:
        return [], [], {} if (search_keyword or "").strip() else None
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
        search_pos=search_pos,
    )
    cached = _cache_get(_BOARD_INDEX_CACHE, _BOARD_INDEX_CACHE_LOCK, cache_key)
    if cached is not None:
        rows, categories, search_nav = cached
        return _copy_rows(rows), _copy_categories(categories), dict(search_nav) if search_nav is not None else None

    data = []
    headtexts = []
    search_nav = {} if (search_keyword or "").strip() else None
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
            search_pos=search_pos,
            search_nav_collector=search_nav,
        ):
            data.append(_index_item_to_dict(item))
        await _fill_missing_author_codes(api, board, kind, data, recommend=recommend)
        categories = _normalize_head_categories(headtexts, head_id=head_id)
    if data or categories:
        _cache_set(
            _BOARD_INDEX_CACHE,
            _BOARD_INDEX_CACHE_LOCK,
            cache_key,
            (_copy_rows(data), _copy_categories(categories), dict(search_nav) if search_nav is not None else None),
            BOARD_PAGE_CACHE_TTL,
            BOARD_INDEX_CACHE_MAX_ITEMS,
        )
    return data, categories, search_nav


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
    search_pos=None,
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
    search_pos_value = _normalize_search_pos(search_pos)
    last_successful_search_nav = None

    if target_id <= 0 or fetch_limit == 0:
        return [], False

    board_key = (
        board,
        kind or "",
        recommend_value,
        search_type_value,
        search_keyword_value,
        head_id_value,
        search_pos_value,
    )

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
                search_pos=search_pos_value,
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
        nonlocal last_successful_search_nav
        page = max(_safe_int(start_page, 1), 1)
        checked = set()
        steps = 0

        while steps < max_probe and page >= 1:
            if page in checked:
                break
            checked.add(page)
            steps += 1

            search_nav = {} if search_keyword_value else None
            page_posts = await _fetch_board_page(
                api,
                page,
                board,
                recommend_value,
                kind=kind,
                search_type=search_type_value,
                search_keyword=search_keyword_value,
                head_id=head_id_value or None,
                search_pos=search_pos_value,
                search_nav_collector=search_nav,
            )
            if not page_posts:
                break
            if search_nav is not None:
                last_successful_search_nav = dict(search_nav)

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
    visited_search_positions = {search_pos_value}
    unvisited_next_block_remains = False
    while len(related) < collect_limit and loaded_tail < max_tail:
        search_nav = {} if search_keyword_value else None
        page_posts = await _fetch_board_page(
            api,
            next_page,
            board,
            recommend_value,
            kind=kind,
            search_type=search_type_value,
            search_keyword=search_keyword_value,
            head_id=head_id_value or None,
            search_pos=search_pos_value,
            search_nav_collector=search_nav,
        )
        loaded_tail += 1
        if not page_posts:
            next_pos = _normalize_search_pos(
                (last_successful_search_nav or {}).get("next_pos")
            )
            if search_keyword_value and next_pos is not None and next_pos not in visited_search_positions:
                unvisited_next_block_remains = True
                if loaded_tail < max_tail:
                    visited_search_positions.add(next_pos)
                    search_pos_value = next_pos
                    next_page = 1
                    last_successful_search_nav = None
                    unvisited_next_block_remains = False
                    continue
            break
        if search_nav is not None:
            last_successful_search_nav = dict(search_nav)
        append_rows(page_posts)
        next_page += 1

    if (
        search_keyword_value
        and len(related) < collect_limit
        and loaded_tail >= max_tail
        and last_successful_search_nav is not None
    ):
        next_pos = _normalize_search_pos(last_successful_search_nav.get("next_pos"))
        unvisited_next_block_remains = (
            next_pos is not None and next_pos not in visited_search_positions
        )

    return related[:fetch_limit], len(related) > fetch_limit or unvisited_next_block_remains


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
    search_pos=None,
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
            search_pos=search_pos,
        )
