import asyncio
import re
import threading
import time

from . import dc_api

MAX_PAGE = 31
RELATED_LIMIT = 12
DOCS_PER_PAGE_ESTIMATE = max(int(getattr(dc_api, "DOCS_PER_PAGE", 200)), 1)
RELATED_PAGE_FETCH_SIZE = DOCS_PER_PAGE_ESTIMATE
RELATED_PAGE_PROBE_STEPS = 8
RELATED_TAIL_PAGES = 3
LATEST_ID_CACHE_TTL = 20
RELATED_CACHE_TTL = 90
AUTHOR_CODE_CACHE_TTL = 600

_CACHE_LOCK = threading.Lock()
_LATEST_ID_CACHE = {}
_RELATED_CACHE = {}
_AUTHOR_CODE_CACHE = {}


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    matched = re.search(r"\(([^()\s]{1,64})\)\s*$", raw)
    if matched:
        return raw[:matched.start()].strip(), matched.group(1).strip()
    matched = re.search(r"\(([^()\s]{1,64})$", raw)
    if matched:
        return raw[:matched.start()].strip(), matched.group(1).strip()
    return raw, None


def _normalize_author(author, author_id=None):
    name, inline_code = _split_name_and_inline_code(author)
    code = _clean_author_code(author_id) or _clean_author_code(inline_code)
    if not name:
        return "익명", code
    anon_match = re.fullmatch(r"ㅇㅇ(\d*)", name)
    if anon_match:
        suffix = anon_match.group(1) or ""
        return f"익명{suffix}", code
    if name.endswith("갤러"):
        return "익명", code
    return name, code


def _is_reply_comment(parent_id):
    value = str(parent_id or "").strip().lower()
    if value in {"", "0", "1", "none", "null"}:
        return False
    try:
        # Mobile comment payload uses "m_no", where 0/1 is not a reply thread id.
        return int(value) > 1
    except (TypeError, ValueError):
        return False


def _index_item_to_dict(item):
    author, author_code = _normalize_author(item.author, getattr(item, "author_id", None))
    return {
        "id": item.id,
        "subject": getattr(item, "subject", None),
        "title": item.title,
        "author": author,
        "author_code": author_code,
        "time": item.time,
        "comment_count": item.comment_count,
        "voteup_count": item.voteup_count,
        "view_count": item.view_count,
        "isimage": item.isimage,
        "isrecommend": item.isrecommend,
        "isdcbest": item.isdcbest,
        "ishit": item.ishit,
    }


def _cache_get(cache, key):
    now = time.time()
    with _CACHE_LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        if entry["expires_at"] < now:
            cache.pop(key, None)
            return None
        return entry["value"]


def _cache_set(cache, key, value, ttl):
    expires_at = time.time() + max(_safe_int(ttl, 0), 0)
    with _CACHE_LOCK:
        cache[key] = {"value": value, "expires_at": expires_at}


async def _fetch_board_page(
    api,
    page,
    board,
    recommend,
    kind=None,
    page_size=RELATED_PAGE_FETCH_SIZE,
):
    posts = []
    async for item in api.board(
        board_id=board,
        num=page_size,
        start_page=page,
        recommend=recommend,
        kind=kind,
        max_scan_pages=1,
    ):
        posts.append(_index_item_to_dict(item))
    return posts


async def _fill_missing_author_code(api, board, kind, row):
    if not row:
        return row
    if row.get("author_code"):
        return row
    doc_id = row.get("id")
    if not doc_id:
        return row
    cache_key = (board, kind or "", str(doc_id))
    cached = _cache_get(_AUTHOR_CODE_CACHE, cache_key)
    if cached is not None:
        row["author"] = cached.get("author", row.get("author"))
        row["author_code"] = cached.get("author_code")
        return row
    try:
        doc = await api.document(board_id=board, document_id=doc_id, kind=kind)
    except Exception:
        return row
    if not doc:
        return row
    author, author_code = _normalize_author(doc.author, doc.author_id)
    row["author"] = author
    row["author_code"] = author_code
    _cache_set(
        _AUTHOR_CODE_CACHE,
        cache_key,
        {"author": author, "author_code": author_code},
        AUTHOR_CODE_CACHE_TTL,
    )
    return row


async def _read_document_with_api(api, api_id, board, kind=None):
    data = {}
    comments = []
    images = []
    doc = await api.document(board_id=board, document_id=api_id, kind=kind)
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
    data = {
        "title": doc.title,
        "author": author,
        "author_code": author_code,
        "time": doc.time,
        "voteup_count": doc.voteup_count,
        "html": doc.html,
    }
    async for com in doc.comments():
        comment_author, comment_author_code = _normalize_author(com.author, com.author_id)
        is_reply = bool(getattr(com, "is_reply", False)) or _is_reply_comment(com.parent_id)
        comments.append(
            {
                "time": com.time,
                "contents": com.contents,
                "author": comment_author,
                "author_code": comment_author_code,
                "parent_id": com.parent_id,
                "is_reply": is_reply,
                "dccon": com.dccon,
            }
        )
    for img in doc.images:
        images.append(img.src)
    return data, comments, images


async def async_read(api_id, board, kind=None):
    async with dc_api.API() as api:
        return await _read_document_with_api(api, api_id, board, kind=kind)


async def async_index(
    page,
    board,
    recommend,
    kind=None,
    document_id_upper_limit=None,
    document_id_lower_limit=None,
    limit=None,
    max_scan_pages=None,
):
    if limit is None:
        fetch_num = MAX_PAGE
    else:
        try:
            fetch_num = max(int(limit), 0)
        except (TypeError, ValueError):
            fetch_num = MAX_PAGE
    if fetch_num == 0:
        return []
    if max_scan_pages is None:
        scan_limit = None
    else:
        try:
            scan_limit = max(int(max_scan_pages), 0)
        except (TypeError, ValueError):
            scan_limit = None

    data = []
    async with dc_api.API() as api:
        async for item in api.board(
            board_id=board,
            num=fetch_num,
            start_page=page,
            recommend=recommend,
            kind=kind,
            document_id_upper_limit=document_id_upper_limit,
            document_id_lower_limit=document_id_lower_limit,
            max_scan_pages=scan_limit,
        ):
            tdata = _index_item_to_dict(item)
            await _fill_missing_author_code(api, board, kind, tdata)
            data.append(tdata)
    return data


async def _related_by_position_with_api(
    api,
    api_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
):
    target_id = _safe_int(api_id, 0)
    fetch_limit = max(_safe_int(limit, RELATED_LIMIT), 0)
    max_probe = max(_safe_int(probe_steps, RELATED_PAGE_PROBE_STEPS), 1)
    max_tail = max(_safe_int(tail_pages, RELATED_TAIL_PAGES), 0)
    if target_id <= 0 or fetch_limit == 0:
        return []

    board_key = (board, kind or "")
    related_key = (board, kind or "", target_id, fetch_limit)
    cached_related = _cache_get(_RELATED_CACHE, related_key)
    if cached_related is not None:
        return cached_related

    latest_id = _cache_get(_LATEST_ID_CACHE, board_key)
    if latest_id is None:
        first_page = await _fetch_board_page(api, 1, board, 0, kind=kind, page_size=1)
        if not first_page:
            return []
        latest_id = _safe_int(first_page[0].get("id"), target_id)
        _cache_set(_LATEST_ID_CACHE, board_key, latest_id, LATEST_ID_CACHE_TTL)

    estimated_page = max(1, ((latest_id - target_id) // DOCS_PER_PAGE_ESTIMATE) + 1)
    found_page = None
    found_index = -1
    found_posts = []
    page = estimated_page
    checked = set()
    steps = 0

    while steps < max_probe and page >= 1:
        if page in checked:
            break
        checked.add(page)
        steps += 1

        page_posts = await _fetch_board_page(api, page, board, 0, kind=kind)
        if not page_posts:
            break

        page_ids = [_safe_int(row.get("id"), 0) for row in page_posts]
        if target_id in page_ids:
            found_page = page
            found_index = page_ids.index(target_id)
            found_posts = page_posts
            break

        valid_ids = [pid for pid in page_ids if pid > 0]
        if not valid_ids:
            break
        page_max = max(valid_ids)
        page_min = min(valid_ids)
        if target_id > page_max:
            page = max(1, page - 1)
        elif target_id < page_min:
            page += 1
        else:
            page += 1

    if found_page is None:
        return []

    related = []
    for row in found_posts[found_index + 1 :]:
        rid = _safe_int(row.get("id"), 0)
        if rid >= target_id:
            continue
        related.append(row)
        if len(related) >= fetch_limit:
            result = related[:fetch_limit]
            for item in result:
                await _fill_missing_author_code(api, board, kind, item)
            _cache_set(_RELATED_CACHE, related_key, result, RELATED_CACHE_TTL)
            return result

    next_page = found_page + 1
    loaded_tail = 0
    while len(related) < fetch_limit and loaded_tail < max_tail:
        page_posts = await _fetch_board_page(api, next_page, board, 0, kind=kind)
        if not page_posts:
            break
        for row in page_posts:
            rid = _safe_int(row.get("id"), 0)
            if rid <= 0 or rid >= target_id:
                continue
            related.append(row)
            if len(related) >= fetch_limit:
                break
        next_page += 1
        loaded_tail += 1

    result = related[:fetch_limit]
    for row in result:
        await _fill_missing_author_code(api, board, kind, row)
    _cache_set(_RELATED_CACHE, related_key, result, RELATED_CACHE_TTL)
    return result


async def async_related_by_position(
    api_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
):
    async with dc_api.API() as api:
        return await _related_by_position_with_api(
            api,
            api_id,
            board,
            kind=kind,
            limit=limit,
            probe_steps=probe_steps,
            tail_pages=tail_pages,
        )
