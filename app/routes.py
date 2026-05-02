#-*- coding:utf-8 -*-
import os
import re
import time
from datetime import datetime, timedelta, timezone
from flask import Blueprint, abort, jsonify, make_response, render_template, request, url_for
from bs4 import BeautifulSoup, NavigableString

from .services.async_bridge import run_async
from .services.core import async_index, async_read, async_related_after_position
from .services.heung import get_heung_galleries, search_galleries
from .services.html_sanitizer import rewrite_content_images, sanitize_html_fragment
from .services.media_proxy import build_media_response, normalize_media_url_shape
from .services.recent import (
    RECENT_MAX_ITEMS,
    format_recent_time,
    load_recent_entries,
    touch_recent_gallery,
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

bp = Blueprint("main", __name__)

BOARD_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
ALLOWED_GALLERY_KINDS = {"normal", "minor", "mini", "person"}
ALLOWED_NAV_MODES = {"ai"}
DEFAULT_SEARCH_TYPE = "subject_m"


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int_arg(name, default=1):
    return max(_safe_int(request.args.get(name, default), default), 1)


def _normalize_recommend(value=None):
    if value is None:
        value = request.args.get("recommend", 0)
    return 1 if _safe_int(value, 0) == 1 else 0


def _normalize_board_id(value, default="airforce"):
    board = str(default if value is None else value).strip() or default
    if not BOARD_ID_RE.fullmatch(board):
        abort(400)
    return board


def _normalize_gallery_kind(value, *, abort_on_invalid=True):
    raw = (value or "").strip().lower()
    if not raw or raw == "normal":
        return None
    if raw in ALLOWED_GALLERY_KINDS:
        return raw
    if abort_on_invalid:
        abort(400)
    return None


def _normalize_nav_mode(value):
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if raw in ALLOWED_NAV_MODES:
        return raw
    abort(400)


def _query_kind_for_url(kind):
    return _normalize_gallery_kind(kind, abort_on_invalid=False)


def _add_kind_param(params, kind):
    query_kind = _query_kind_for_url(kind)
    if query_kind:
        params["kind"] = query_kind


def _add_search_params(params, search_type=None, search_keyword=None):
    keyword = (search_keyword or "").strip()
    if not keyword:
        return
    params["s_type"] = _normalize_board_search_type(search_type)
    params["serval"] = keyword


def board_url(board, recommend=0, page=1, kind=None, nav=None, search_type=None, search_keyword=None):
    params = {
        "board": board,
        "recommend": 1 if _safe_int(recommend, 0) == 1 else 0,
        "page": max(_safe_int(page, 1), 1),
    }
    _add_kind_param(params, kind)
    if nav:
        params["nav"] = nav
    _add_search_params(params, search_type, search_keyword)
    return url_for("main.board", **params)


def read_url(board, pid, recommend=0, source_page=None, kind=None, search_type=None, search_keyword=None):
    params = {
        "board": board,
        "pid": pid,
    }
    if _safe_int(recommend, 0) == 1:
        params["recommend"] = 1
    source_page_int = _safe_int(source_page, 0)
    if source_page_int > 0:
        params["source_page"] = source_page_int
    _add_kind_param(params, kind)
    _add_search_params(params, search_type, search_keyword)
    return url_for("main.read", **params)


def _serialize_related_posts(posts):
    rows = []
    for item in posts or []:
        rows.append(
            {
                "id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "author": item.get("author", "익명"),
                "author_code": item.get("author_code"),
                "time": str(item.get("time", "")),
                "comment_count": _safe_int(item.get("comment_count", 0), 0),
                "voteup_count": _safe_int(item.get("voteup_count", 0), 0),
                "source_page": _safe_int(item.get("source_page", 0), 0),
            }
        )
    return rows


def _format_cache_time(ts):
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(ts, tz=kst).strftime("%Y-%m-%d %H:%M:%S KST")


def _normalize_board_search_type(value):
    value = (value or "").strip()
    pc_type_map = {
        "search_subject_memo": "subject_m",
        "search_subject": "subject",
        "search_memo": "memo",
        "search_name": "name",
        "search_comment": "comment",
    }
    if value in pc_type_map:
        return pc_type_map[value]
    if value in {"subject_m", "subject", "memo", "name", "comment"}:
        return value
    return DEFAULT_SEARCH_TYPE


def _board_search_keyword():
    return ((request.args.get("serval") or request.args.get("s_keyword") or "")).strip()


def _current_search_context():
    keyword = _board_search_keyword()
    search_type = _normalize_board_search_type(request.args.get("s_type")) if keyword else DEFAULT_SEARCH_TYPE
    return search_type, keyword


def _search_call_kwargs(search_type, search_keyword):
    if not search_keyword:
        return {}
    return {
        "search_type": search_type,
        "search_keyword": search_keyword,
    }


def _nav_tab_for_gallery(board, recommend=0, nav_mode=None):
    if nav_mode == "ai":
        return "ai"
    if board == "dcbest" or _safe_int(recommend, 0) == 1:
        return "best"
    return "all"


def _highlight_html_text(html, keyword):
    term = (keyword or "").strip()
    if not term:
        return html
    soup = BeautifulSoup(html or "", "html.parser")
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    ignored_parents = {"script", "style", "textarea", "code", "pre", "mark"}

    for node in list(soup.find_all(string=pattern)):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and parent.name in ignored_parents:
            continue
        text = str(node)
        parts = []
        last = 0
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last:
                parts.append(NavigableString(text[last:start]))
            mark = soup.new_tag("mark")
            mark["class"] = "search-highlight"
            mark.string = text[start:end]
            parts.append(mark)
            last = end
        if last < len(text):
            parts.append(NavigableString(text[last:]))
        if parts:
            node.replace_with(*parts)
    return str(soup)


@bp.route("/")
def index():
    page = _safe_int(request.args.get("heung_page", 1), 1)
    heung_q = (request.args.get("heung_q") or "").strip()

    heung_items = []
    heung_updated_at = None
    heung_error = None
    if heung_q:
        try:
            heung_items = search_galleries(heung_q)
            heung_updated_at = time.time()
        except Exception:
            heung_error = "갤러리 검색 결과를 가져오지 못했습니다."
    else:
        try:
            heung_items, heung_updated_at = get_heung_galleries()
        except Exception:
            heung_error = "흥한 갤러리 목록을 가져오지 못했습니다."

    total_items = len(heung_items)
    total_pages = max(1, (total_items + 19) // 20)
    page = max(1, min(page, total_pages))
    start = (page - 1) * 20
    end = min(start + 20, total_items)
    page_items = heung_items[start:end]

    return render_template(
        "index.html",
        nav_tab="all",
        heung_items=page_items,
        heung_page=page,
        heung_total_pages=total_pages,
        heung_total_items=total_items,
        heung_start_rank=(start + 1) if total_items else 0,
        heung_end_rank=end,
        heung_error=heung_error,
        heung_q=heung_q,
        heung_updated_at_str=_format_cache_time(heung_updated_at) if heung_updated_at else "-",
    )


@bp.route("/recent")
def recent():
    rows = load_recent_entries()
    recent_items = []
    for row in rows[:RECENT_MAX_ITEMS]:
        recent_items.append(
            {
                "board": row["board"],
                "kind": row.get("kind"),
                "recommend": 1 if _safe_int(row.get("recommend", 0), 0) == 1 else 0,
                "visited_at_str": format_recent_time(row.get("visited_at")),
            }
        )
    return render_template("recent.html", nav_tab="recent", recent_items=recent_items)

# 공군갤 airforce
# 야갤 baseball_new10
# 싱벙갤 singlebungle1472
# 그림갤 drawing
@bp.route('/board')
def board():
    page = _positive_int_arg("page", 1)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    recommend = _normalize_recommend()
    kind = _normalize_gallery_kind(request.args.get("kind"))
    nav_mode = _normalize_nav_mode(request.args.get("nav"))
    search_type, search_keyword = _current_search_context()
    ret = run_async(
        async_index(
            page,
            board,
            recommend,
            kind=kind,
            **_search_call_kwargs(search_type, search_keyword),
        )
    )

    response = make_response(
        render_template(
            "board.html",
            datas=ret,
            page=page,
            board=board,
            recommend=recommend,
            kind=kind,
            nav_tab=_nav_tab_for_gallery(board, recommend, nav_mode),
            nav_mode=nav_mode,
            search_type=search_type,
            search_keyword=search_keyword,
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend)
    return response


@bp.route("/media")
def media():
    src = (request.args.get("src") or "").strip()
    normalized_src = normalize_media_url_shape(src)
    if not normalized_src:
        abort(400)
    board = _normalize_board_id(request.args.get("board") or "airforce")
    pid = _safe_int(request.args.get("pid", 0), 0)
    if request.args.get("pid") not in (None, "") and pid <= 0:
        abort(400)
    kind = _normalize_gallery_kind(request.args.get("kind"))
    return build_media_response(normalized_src, board, pid, kind=kind)


@bp.route("/read")
def read():
    pid = _safe_int(request.args.get("pid", 0), 0)
    if pid <= 0:
        abort(404)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    kind = _normalize_gallery_kind(request.args.get("kind"))
    recommend = _normalize_recommend()
    source_page = max(_safe_int(request.args.get("source_page", 0), 0), 0)
    search_type, search_keyword = _current_search_context()
    data, comments, images = run_async(
        async_read(
            pid,
            board,
            kind=kind,
            recommend=recommend,
            **_search_call_kwargs(search_type, search_keyword),
        )
    )
    embedded_related_posts = _serialize_related_posts(data.pop("related_posts", []))
    soup = BeautifulSoup(data.get("html") or "", "html.parser")
    rewrite_content_images(soup, images, board, pid, kind)

    for comment in comments:
        if comment.get("dccon"):
            comment["dccon"] = url_for("main.media", src=comment["dccon"], board=board, pid=pid, kind=kind)

    safe_html = sanitize_html_fragment(str(soup))
    if search_keyword:
        safe_html = _highlight_html_text(safe_html, search_keyword)
    data["html"] = safe_html
    response = make_response(
        render_template(
            "read.html",
            data=data,
            comments=comments,
            images=images,
            board=board,
            pid=pid,
            kind=kind,
            recommend=recommend,
            source_page=source_page,
            search_type=search_type,
            search_keyword=search_keyword,
            embedded_related_posts=embedded_related_posts,
            nav_tab=_nav_tab_for_gallery(board, recommend),
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend)
    return response


@bp.route("/read/related")
def read_related():
    pid = _safe_int(request.args.get("pid", 0), 0)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    kind = _normalize_gallery_kind(request.args.get("kind"))
    recommend = _normalize_recommend()
    limit = _safe_int(request.args.get("limit", 12), 12)
    limit = max(1, min(limit, 30))
    source_page = max(_safe_int(request.args.get("source_page", 0), 0), 0)
    after_pid = max(_safe_int(request.args.get("after_pid", 0), 0), 0)
    search_type, search_keyword = _current_search_context()

    posts = []
    has_more = False
    if pid > 0:
        try:
            posts, has_more = run_async(
                async_related_after_position(
                    pid,
                    after_pid,
                    board,
                    kind=kind,
                    limit=limit,
                    source_page=source_page,
                    recommend=recommend,
                    **_search_call_kwargs(search_type, search_keyword),
                )
            )
        except Exception:
            return jsonify({"ok": False, "items": [], "error": "related_fetch_failed"}), 502

    return jsonify(
        {
            "ok": True,
            "items": _serialize_related_posts(posts),
            "has_more": has_more,
        }
    )

def register_routes(app):
    app.add_template_global(board_url, "board_url")
    app.add_template_global(read_url, "read_url")
    app.register_blueprint(bp)
