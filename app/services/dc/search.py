"""Search segments are separate from page numbers inside each segment."""

import re
from urllib.parse import parse_qs, urljoin, urlparse


def normalize_search_pos(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"-?[0-9]{1,20}", value):
        return None
    return str(int(value))


def search_cache_suffix(search_keyword, search_pos):
    pos = normalize_search_pos(search_pos) if (search_keyword or "").strip() else None
    return (pos,) if pos is not None else ()


def search_pagination(parsed, used_url, pagination):
    origin = urlparse(used_url)
    query = parse_qs(origin.query, keep_blank_values=True)
    mobile = origin.hostname == "m.dcinside.com"
    keyword_key = "serval" if mobile else "s_keyword"
    cursor_key = "s_pos" if mobile else "search_pos"
    if not query.get(keyword_key, [""])[0].strip():
        return pagination
    containers = parsed.xpath("//*[@id='pagination_div']") if mobile else parsed.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' bottom_paging_box ')][./em]"
    )
    if len(containers) != 1:
        return pagination
    current = pagination.get("current_page") or pagination.get("requested_page") or 1
    pos = normalize_search_pos(query.get(cursor_key, [""])[0]) or ""
    state = dict(pagination, current_page=current, search_pos=pos, search_pagination=True)
    targets = {"next": [], "prev": []}
    for link in containers[0].xpath(".//a[@href]" if mobile else "./a[@href]"):
        try:
            href = urlparse(urljoin(used_url, link.get("href") or ""))
            port = href.port
        except ValueError:
            continue
        if (href.scheme not in {"http", "https"} or href.hostname != origin.hostname
                or href.username or href.password or port not in {None, 80, 443}
                or href.path.rstrip("/") != origin.path.rstrip("/")):
            continue
        target = parse_qs(href.query, keep_blank_values=True)
        if any(len(values) != 1 for values in target.values()):
            continue
        if any(target.get(key) != query.get(key) for key in (keyword_key, "s_type", "id")):
            continue
        raw_page = target.get("page", [""])[0]
        if not re.fullmatch(r"[0-9]{1,9}", raw_page) or int(raw_page) < 1:
            continue
        page = int(raw_page)
        classes = set((link.get("class") or "").split())
        direction = "next" if classes & {"next", "search_next", "page_next"} else (
            "prev" if classes & {"prev", "search_prev", "page_prev"} else None
        )
        raw_pos = target.get(cursor_key, [pos])[0]
        if raw_pos and normalize_search_pos(raw_pos) is None:
            continue
        target_pos = normalize_search_pos(raw_pos) or ""
        if target_pos == pos:
            if page == current:
                continue
            direction = "next" if page > current else "prev"
            priority = abs(page - current)
        elif direction:
            priority = 10**10  # Prefer adjacent pages inside the current segment.
        else:
            continue
        targets[direction].append((priority, page, target_pos))
    for direction, candidates in targets.items():
        if candidates:
            _, page, target_pos = min(candidates)
            state[f"{direction}_page"] = page
            state[f"{direction}_search_pos"] = target_pos
    state["has_next"] = "next_page" in state
    if current < (pagination.get("requested_page") or current):
        state["search_clamped"] = True
    return state
