import asyncio
import os
import re
import threading
import time

from . import dc_api


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


MAX_PAGE = 31
RELATED_LIMIT = 12
DOCS_PER_PAGE_ESTIMATE = max(int(getattr(dc_api, "DOCS_PER_PAGE", 200)), 1)
RELATED_PAGE_FETCH_SIZE = DOCS_PER_PAGE_ESTIMATE
RELATED_PAGE_PROBE_STEPS = max(_env_int("MIRROR_RELATED_PAGE_PROBE_STEPS", 4), 1)
RELATED_TAIL_PAGES = max(_env_int("MIRROR_RELATED_TAIL_PAGES", 1), 0)
BOARD_PAGE_CACHE_TTL = max(_env_int("MIRROR_BOARD_PAGE_CACHE_TTL", 20), 0)
LATEST_ID_CACHE_TTL = 20
RELATED_CACHE_TTL = 90
AUTHOR_CODE_CACHE_TTL = 600
BOARD_PAGE_CACHE_MAX_ITEMS = 2048
LATEST_ID_CACHE_MAX_ITEMS = 512
RELATED_CACHE_MAX_ITEMS = 2048
AUTHOR_CODE_CACHE_MAX_ITEMS = 8192
AUTHOR_CODE_FETCH_CONCURRENCY = max(_env_int("MIRROR_AUTHOR_CODE_FETCH_CONCURRENCY", 5), 1)

_CACHE_LOCK = threading.Lock()
_BOARD_PAGE_CACHE = {}
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


def _comment_to_dict(comment):
    comment_author, comment_author_code = _normalize_author(comment.author, comment.author_id)
    is_reply = bool(getattr(comment, "is_reply", False)) or _is_reply_comment(comment.parent_id)
    return {
        "time": comment.time,
        "contents": comment.contents,
        "author": comment_author,
        "author_code": comment_author_code,
        "parent_id": comment.parent_id,
        "is_reply": is_reply,
        "dccon": comment.dccon,
    }


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
        "is_mobile_source": bool(getattr(item, "is_mobile_source", False)),
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


def _cache_prune(cache, now, max_items):
    expired_keys = [key for key, entry in cache.items() if entry["expires_at"] < now]
    for key in expired_keys:
        cache.pop(key, None)
    overflow = len(cache) - max(max_items, 0)
    if overflow <= 0:
        return
    oldest_keys = sorted(cache, key=lambda key: cache[key]["expires_at"])[:overflow]
    for key in oldest_keys:
        cache.pop(key, None)


def _cache_set(cache, key, value, ttl, max_items):
    expires_at = time.time() + max(_safe_int(ttl, 0), 0)
    with _CACHE_LOCK:
        _cache_prune(cache, time.time(), max_items)
        cache[key] = {"value": value, "expires_at": expires_at}


async def _fetch_board_page(
    api,
    page,
    board,
    recommend,
    kind=None,
    page_size=RELATED_PAGE_FETCH_SIZE,
):
    cache_key = (
        board,
        kind or "",
        _safe_int(recommend, 0),
        _safe_int(page, 1),
        _safe_int(page_size, RELATED_PAGE_FETCH_SIZE),
    )
    cached = _cache_get(_BOARD_PAGE_CACHE, cache_key)
    if cached is not None:
        return [dict(row) for row in cached]

    posts = []
    async for item in api.board(
        board_id=board,
        num=page_size,
        start_page=page,
        recommend=recommend,
        kind=kind,
        max_scan_pages=1,
    ):
        row = _index_item_to_dict(item)
        row["source_page"] = _safe_int(page, 1)
        posts.append(row)
    if posts:
        _cache_set(
            _BOARD_PAGE_CACHE,
            cache_key,
            [dict(row) for row in posts],
            BOARD_PAGE_CACHE_TTL,
            BOARD_PAGE_CACHE_MAX_ITEMS,
        )
    return posts


async def _fill_missing_author_code(api, board, kind, row, recommend=0):
    if not row:
        return row
    if row.get("author_code"):
        return row
    if row.get("is_mobile_source"):
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
        doc = await api.document(board_id=board, document_id=doc_id, kind=kind, recommend=bool(_safe_int(recommend, 0)))
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
        AUTHOR_CODE_CACHE_MAX_ITEMS,
    )
    return row


async def _fill_missing_author_codes(api, board, kind, rows, recommend=0):
    semaphore = asyncio.Semaphore(AUTHOR_CODE_FETCH_CONCURRENCY)

    async def fill(row):
        async with semaphore:
            return await _fill_missing_author_code(api, board, kind, row, recommend=recommend)

    await asyncio.gather(*(fill(row) for row in rows))
    return rows


async def _read_document_with_api(api, api_id, board, kind=None, recommend=0):
    data = {}
    comments = []
    images = []
    doc = await api.document(board_id=board, document_id=api_id, kind=kind, recommend=bool(_safe_int(recommend, 0)))
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


async def async_read(api_id, board, kind=None, recommend=0):
    async with dc_api.API() as api:
        return await _read_document_with_api(
            api,
            api_id,
            board,
            kind=kind,
            recommend=recommend,
        )


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
            data.append(tdata)
        await _fill_missing_author_codes(api, board, kind, data, recommend=recommend)
    return data


async def _related_by_position_with_api(
    api,
    api_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
    source_page=None,
    recommend=0,
):
    target_id = _safe_int(api_id, 0)
    fetch_limit = max(_safe_int(limit, RELATED_LIMIT), 0)
    max_probe = max(_safe_int(probe_steps, RELATED_PAGE_PROBE_STEPS), 1)
    max_tail = max(_safe_int(tail_pages, RELATED_TAIL_PAGES), 0)
    if target_id <= 0 or fetch_limit == 0:
        return []

    source_page_value = _safe_int(source_page, 0)
    recommend_value = _safe_int(recommend, 0)
    related_key = (board, kind or "", recommend_value, target_id, fetch_limit, source_page_value)
    cached_related = _cache_get(_RELATED_CACHE, related_key)
    if cached_related is not None:
        return cached_related

    if recommend_value:
        # Recommended mobile read pages normally include the next recommended
        # list in their HTML. This path is only a narrow fallback for cases where
        # the document fetch falls back to PC markup and embedded related posts
        # are unavailable. Do not estimate or probe broad page ranges here.
        candidate_pages = []
        if source_page_value > 0:
            candidate_pages.append(source_page_value)
        if 1 not in candidate_pages:
            candidate_pages.append(1)

        for page in candidate_pages:
            page_posts = await _fetch_board_page(api, page, board, recommend_value, kind=kind)
            page_ids = [_safe_int(row.get("id"), 0) for row in page_posts]
            if target_id not in page_ids:
                continue

            found_index = page_ids.index(target_id)
            related = []
            for row in page_posts[found_index + 1 :]:
                rid = _safe_int(row.get("id"), 0)
                if rid <= 0:
                    continue
                related.append(row)
                if len(related) >= fetch_limit:
                    break

            result = related[:fetch_limit]
            _cache_set(_RELATED_CACHE, related_key, result, RELATED_CACHE_TTL, RELATED_CACHE_MAX_ITEMS)
            return result

        _cache_set(_RELATED_CACHE, related_key, [], RELATED_CACHE_TTL, RELATED_CACHE_MAX_ITEMS)
        return []

    board_key = (board, kind or "", recommend_value)

    async def estimate_page_from_latest_id():
        latest_id = _cache_get(_LATEST_ID_CACHE, board_key)
        if latest_id is None:
            first_page = await _fetch_board_page(api, 1, board, recommend_value, kind=kind, page_size=1)
            if not first_page:
                return None
            latest_id = _safe_int(first_page[0].get("id"), target_id)
            _cache_set(_LATEST_ID_CACHE, board_key, latest_id, LATEST_ID_CACHE_TTL, LATEST_ID_CACHE_MAX_ITEMS)
        return max(1, ((latest_id - target_id) // DOCS_PER_PAGE_ESTIMATE) + 1)

    async def find_target_from_page(start_page, single_page=False):
        found_page = None
        found_index = -1
        found_posts = []
        page = start_page
        checked = set()
        steps = 0

        while steps < max_probe and page >= 1:
            if page in checked:
                break
            checked.add(page)
            steps += 1

            page_posts = await _fetch_board_page(api, page, board, recommend_value, kind=kind)
            if not page_posts:
                break

            page_ids = [_safe_int(row.get("id"), 0) for row in page_posts]
            if target_id in page_ids:
                found_page = page
                found_index = page_ids.index(target_id)
                found_posts = page_posts
                break
            if single_page:
                break

            valid_ids = [pid for pid in page_ids if pid > 0]
            if not valid_ids:
                break
            if recommend_value:
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

        return found_page, found_index, found_posts

    if source_page_value > 0:
        found_page, found_index, found_posts = await find_target_from_page(
            source_page_value,
            single_page=bool(recommend_value),
        )
        if found_page is None:
            if recommend_value:
                if source_page_value != 1:
                    found_page, found_index, found_posts = await find_target_from_page(1)
            else:
                estimated_page = await estimate_page_from_latest_id()
                if estimated_page is not None and estimated_page != source_page_value:
                    found_page, found_index, found_posts = await find_target_from_page(estimated_page)
    else:
        if recommend_value:
            found_page, found_index, found_posts = await find_target_from_page(1)
        else:
            estimated_page = await estimate_page_from_latest_id()
            if estimated_page is None:
                return []
            found_page, found_index, found_posts = await find_target_from_page(estimated_page)

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
            _cache_set(_RELATED_CACHE, related_key, result, RELATED_CACHE_TTL, RELATED_CACHE_MAX_ITEMS)
            return result

    next_page = found_page + 1
    loaded_tail = 0
    while len(related) < fetch_limit and loaded_tail < max_tail:
        page_posts = await _fetch_board_page(api, next_page, board, recommend_value, kind=kind)
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
    if result:
        _cache_set(_RELATED_CACHE, related_key, result, RELATED_CACHE_TTL, RELATED_CACHE_MAX_ITEMS)
    return result


async def async_related_by_position(
    api_id,
    board,
    kind=None,
    limit=RELATED_LIMIT,
    probe_steps=RELATED_PAGE_PROBE_STEPS,
    tail_pages=RELATED_TAIL_PAGES,
    source_page=None,
    recommend=0,
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
            source_page=source_page,
            recommend=recommend,
        )
