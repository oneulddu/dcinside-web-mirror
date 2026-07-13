import re
from urllib.parse import parse_qs, urlparse

from flask import has_request_context, url_for


BOARD_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
GALLERY_KIND_BY_PC_PREFIX = {
    "mgallery": "minor",
    "mini": "mini",
    "person": "person",
}
PC_GALLERY_HOSTS = {"gall.dcinside.com", "search.dcinside.com"}
MOBILE_GALLERY_HOST = "m.dcinside.com"


def _first_query_value(query, *names):
    for name in names:
        values = query.get(name)
        if values and values[0] not in {None, ""}:
            return str(values[0]).strip()
    return None


def _is_safe_board_id(value):
    return bool(value and BOARD_ID_RE.fullmatch(value))


def _is_positive_int(value):
    return bool(value and str(value).isdigit() and int(value) > 0)


def _append_fragment(url, fragment):
    return f"{url}#{fragment}" if fragment else url


def _add_kind(params, kind):
    if kind:
        params["kind"] = kind


def _add_recommend(params, query):
    recommend = _first_query_value(query, "recommend")
    exception_mode = (_first_query_value(query, "exception_mode") or "").lower()
    if recommend == "1" or exception_mode == "recommend":
        params["recommend"] = 1


def _add_head_id(params, query):
    head_id = _first_query_value(query, "headid", "head_id", "search_head")
    if head_id and re.fullmatch(r"\d{1,8}", head_id):
        params["headid"] = head_id


def _add_search_params(params, query, source_pattern=None):
    search_type = _first_query_value(query, "s_type", "search_type")
    search_keyword = _first_query_value(query, "serval", "s_keyword", "search_keyword", "keyword")
    if search_keyword:
        if search_type:
            params["s_type"] = search_type
        params["serval"] = search_keyword
        search_pos = _first_query_value(query, "s_pos", "search_pos")
        try:
            normalized_search_pos = int(search_pos) if search_pos is not None else 0
        except ValueError:
            normalized_search_pos = 0
        if normalized_search_pos:
            params["s_pos"] = normalized_search_pos
        if source_pattern:
            params["source_pattern"] = source_pattern


def _add_board_context(params, query, source_pattern=None):
    _add_recommend(params, query)
    _add_head_id(params, query)
    _add_search_params(params, query, source_pattern=source_pattern)


def _board_href(board_id, query, kind, fragment, source_pattern=None):
    if not _is_safe_board_id(board_id):
        return None
    params = {"board": board_id}
    page = _first_query_value(query, "page")
    if _is_positive_int(page):
        params["page"] = int(page)
    _add_kind(params, kind)
    _add_board_context(params, query, source_pattern=source_pattern)
    return _append_fragment(url_for("main.board", **params), fragment)


def _read_href(board_id, document_id, query, kind, fragment, source_pattern=None):
    if not _is_safe_board_id(board_id) or not _is_positive_int(document_id):
        return None
    params = {"board": board_id, "pid": int(document_id)}
    source_page = _first_query_value(query, "source_page", "page")
    if _is_positive_int(source_page):
        params["source_page"] = int(source_page)
    _add_kind(params, kind)
    _add_board_context(params, query, source_pattern=source_pattern)
    return _append_fragment(url_for("main.read", **params), fragment)


def _mobile_gallery_href(parsed, query):
    host = (parsed.netloc or "").lower()
    if host and host != MOBILE_GALLERY_HOST:
        return None

    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    if len(segments) not in {2, 3} or segments[0] not in {"board", "mini"}:
        return None

    kind = "mini" if segments[0] == "mini" else None
    source_pattern = "mobile_mini" if kind == "mini" else "mobile"
    board_id = segments[1]
    if len(segments) == 2:
        return _board_href(
            board_id,
            query,
            kind,
            parsed.fragment,
            source_pattern=source_pattern,
        )
    return _read_href(
        board_id,
        segments[2],
        query,
        kind,
        parsed.fragment,
        source_pattern=source_pattern,
    )


def _pc_gallery_kind_and_action(path):
    segments = [segment for segment in (path or "").split("/") if segment]
    if len(segments) < 2:
        return None, None

    if segments[0] == "board":
        action_index = 1
        kind = None
    elif segments[0] in GALLERY_KIND_BY_PC_PREFIX and len(segments) >= 3 and segments[1] == "board":
        action_index = 2
        kind = GALLERY_KIND_BY_PC_PREFIX[segments[0]]
    else:
        return None, None

    action = segments[action_index] if len(segments) > action_index else None
    return kind, action


def _pc_gallery_href(parsed, query):
    host = (parsed.netloc or "").lower()
    if host and host not in PC_GALLERY_HOSTS:
        return None

    kind, action = _pc_gallery_kind_and_action(parsed.path)
    source_pattern = kind or "normal"
    if action == "lists":
        return _board_href(
            _first_query_value(query, "id"),
            query,
            kind,
            parsed.fragment,
            source_pattern=source_pattern,
        )
    if action == "view":
        return _read_href(
            _first_query_value(query, "id"),
            _first_query_value(query, "no"),
            query,
            kind,
            parsed.fragment,
            source_pattern=source_pattern,
        )
    return None


def _pc_pretty_gallery_href(parsed, query):
    host = (parsed.netloc or "").lower()
    if host and host not in PC_GALLERY_HOSTS:
        return None

    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    if len(segments) == 2 and segments[0] == "board":
        return _board_href(
            segments[1],
            query,
            None,
            parsed.fragment,
            source_pattern="normal",
        )
    if len(segments) == 3 and segments[0] == "board":
        return _read_href(
            segments[1],
            segments[2],
            query,
            None,
            parsed.fragment,
            source_pattern="normal",
        )
    return None


def dcinside_internal_href(value):
    if not has_request_context():
        return None

    raw_url = (value or "").strip()
    if not raw_url:
        return None

    try:
        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query, keep_blank_values=False)
    except (TypeError, ValueError):
        return None
    host = (parsed.netloc or "").lower()

    if host == MOBILE_GALLERY_HOST:
        return _mobile_gallery_href(parsed, query)
    if host in PC_GALLERY_HOSTS:
        return _pc_gallery_href(parsed, query) or _pc_pretty_gallery_href(parsed, query)

    if not parsed.scheme and not host:
        return _pc_gallery_href(parsed, query) or _pc_pretty_gallery_href(parsed, query) or _mobile_gallery_href(parsed, query)

    return None
