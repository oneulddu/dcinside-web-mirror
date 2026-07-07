#-*- coding:utf-8 -*-
import html
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request, url_for

from .services.async_bridge import run_async
from .services.core import (
    async_board_precise_times,
    async_index_with_head_categories,
    async_read,
    async_related_after_position,
    format_display_time,
)
from .services.heung import get_heung_galleries, search_galleries
from .services.html_sanitizer import prepare_read_html
from .services.media_proxy import build_media_response, build_movie_response, normalize_media_url_shape
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
SITE_NAME = "숨터"
SOCIAL_DESCRIPTION_MAX_LENGTH = 180
SOCIAL_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
SOCIAL_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".m3u8")


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _safe_author_role(value):
    role = str(value or "").strip().lower()
    if role in {"manager", "submanager"}:
        return role
    return None


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


def _normalize_head_id(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{1,8}", raw):
        abort(400)
    return raw


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


def board_url(board, recommend=0, page=1, kind=None, nav=None, search_type=None, search_keyword=None, head_id=None):
    params = {
        "board": board,
        "recommend": 1 if _safe_int(recommend, 0) == 1 else 0,
        "page": max(_safe_int(page, 1), 1),
    }
    _add_kind_param(params, kind)
    if nav:
        params["nav"] = nav
    normalized_head_id = _normalize_head_id(head_id)
    if normalized_head_id is not None:
        params["headid"] = normalized_head_id
    _add_search_params(params, search_type, search_keyword)
    return url_for("main.board", **params)


def read_url(board, pid, recommend=0, source_page=None, kind=None, search_type=None, search_keyword=None, head_id=None):
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
    normalized_head_id = _normalize_head_id(head_id)
    if normalized_head_id is not None:
        params["headid"] = normalized_head_id
    _add_search_params(params, search_type, search_keyword)
    return url_for("main.read", **params)


def _serialize_related_posts(posts):
    rows = []
    for item in posts or []:
        isimage = _safe_bool(item.get("isimage"))
        isvideo = _safe_bool(item.get("isvideo"))
        has_image = _safe_bool(item.get("has_image")) or isimage
        has_video = _safe_bool(item.get("has_video")) or isvideo
        rows.append(
            {
                "id": str(item.get("id", "")),
                "subject": item.get("subject"),
                "title": item.get("title", ""),
                "has_image": has_image,
                "has_video": has_video,
                "author": item.get("author", "익명"),
                "author_code": item.get("author_code"),
                "author_role": _safe_author_role(item.get("author_role")),
                "time": format_display_time(item.get("time_display") or item.get("time")),
                "comment_count": _safe_int(item.get("comment_count", 0), 0),
                "voteup_count": _safe_int(item.get("voteup_count", 0), 0),
                "source_page": _safe_int(item.get("source_page", 0), 0),
                "isimage": isimage,
                "isvideo": isvideo,
                "isrecommend": _safe_bool(item.get("isrecommend")),
            }
        )
    return rows


def _format_read_payload_times(data, comments):
    if data is not None:
        data["time"] = format_display_time(data.get("time"))
    for comment in comments or []:
        comment["time"] = format_display_time(comment.get("time"))


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


def _target_post_ids_arg(name="ids", limit=60):
    ids = []
    seen = set()
    for raw_id in (request.args.get(name) or "").split(","):
        post_id = raw_id.strip()
        if not post_id or post_id in seen:
            continue
        if not post_id.isdigit():
            abort(400)
        seen.add(post_id)
        ids.append(post_id)
        if len(ids) >= limit:
            break
    return ids


def _media_request_context(default_board="airforce"):
    board = _normalize_board_id(request.args.get("board") or default_board)
    pid = _safe_int(request.args.get("pid", 0), 0)
    if request.args.get("pid") not in (None, "") and pid <= 0:
        abort(400)
    kind = _normalize_gallery_kind(request.args.get("kind"))
    return board, pid, kind


def _search_call_kwargs(search_type, search_keyword):
    if not search_keyword:
        return {}
    return {
        "search_type": search_type,
        "search_keyword": search_keyword,
    }


def _public_base_url():
    base_url = (current_app.config.get("PUBLIC_BASE_URL") or "").strip()
    if not base_url:
        return None
    return base_url.rstrip("/") + "/"


def _external_url_for(endpoint, **values):
    path = url_for(endpoint, **values)
    base_url = _public_base_url()
    if base_url:
        return urljoin(base_url, path.lstrip("/"))
    return url_for(endpoint, _external=True, **values)


def _collapse_preview_text(value):
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= SOCIAL_DESCRIPTION_MAX_LENGTH:
        return text
    return text[:SOCIAL_DESCRIPTION_MAX_LENGTH].rstrip() + "..."


def _read_social_description(data):
    for key in ("contents", "html", "title"):
        description = _collapse_preview_text(data.get(key))
        if description:
            return description
    return SITE_NAME


def _read_canonical_url(board, pid, recommend, source_page, kind, search_type, search_keyword, head_id):
    params = {
        "board": board,
        "pid": pid,
    }
    if _safe_int(recommend, 0) == 1:
        params["recommend"] = 1
    if _safe_int(source_page, 0) > 0:
        params["source_page"] = _safe_int(source_page, 0)
    _add_kind_param(params, kind)
    normalized_head_id = _normalize_head_id(head_id)
    if normalized_head_id is not None:
        params["headid"] = normalized_head_id
    _add_search_params(params, search_type, search_keyword)
    return _external_url_for("main.read", **params)


def _is_social_preview_image_url(src):
    normalized_src = normalize_media_url_shape(src)
    if not normalized_src:
        return False
    parsed = urlparse(normalized_src)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    media_hint = path + "?" + query
    if "viewmovie" in path or any(path.endswith(ext) for ext in SOCIAL_VIDEO_EXTENSIONS):
        return False
    if "type=mp4" in query or "type=webm" in query:
        return False
    if any(path.endswith(ext) for ext in SOCIAL_IMAGE_EXTENSIONS):
        return True
    return "viewimage" in path or "dccon" in media_hint


def _first_social_preview_image(images):
    for src in images or []:
        normalized_src = normalize_media_url_shape(src)
        if _is_social_preview_image_url(normalized_src):
            return normalized_src
    return None


def _read_social_meta(data, images, board, pid, kind, recommend, source_page, search_type, search_keyword, head_id):
    title = _collapse_preview_text(data.get("title")) or SITE_NAME
    preview_image = _first_social_preview_image(images)
    media_params = {
        "src": preview_image,
        "board": board,
        "pid": pid,
    } if preview_image else None
    if media_params is not None:
        _add_kind_param(media_params, kind)

    image_url = _external_url_for("main.media", **media_params) if media_params else None
    return {
        "site_name": SITE_NAME,
        "title": title,
        "description": _read_social_description(data),
        "url": _read_canonical_url(
            board,
            pid,
            recommend,
            source_page,
            kind,
            search_type,
            search_keyword,
            head_id,
        ),
        "type": "article",
        "image": image_url,
        "image_alt": title,
        "twitter_card": "summary_large_image" if image_url else "summary",
    }


async def _load_board_payload(page, board, recommend, kind=None, search_type=None, search_keyword=None, head_id=None):
    return await async_index_with_head_categories(
        page,
        board,
        recommend,
        kind=kind,
        max_scan_pages=1,
        search_type=search_type,
        search_keyword=search_keyword,
        head_id=head_id,
    )


def _nav_tab_for_gallery(board, recommend=0, nav_mode=None):
    if nav_mode == "ai":
        return "ai"
    if board == "dcbest" or _safe_int(recommend, 0) == 1:
        return "best"
    return "all"


@bp.route("/legacy/")
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
            current_app.logger.exception("Failed to search DCinside galleries")
            heung_error = "갤러리 검색 결과를 가져오지 못했습니다."
    else:
        try:
            heung_items, heung_updated_at = get_heung_galleries()
        except Exception:
            current_app.logger.exception("Failed to load heung galleries")
            heung_error = "흥한 갤러리 목록을 가져오지 못했습니다."

    total_items = len(heung_items)
    total_pages = max(1, (total_items + 19) // 20)
    page = max(1, min(page, total_pages))
    start = (page - 1) * 20
    end = min(start + 20, total_items)
    page_items = heung_items[start:end]

    return render_template(
        "legacy/index.html",
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


@bp.route("/legacy/recent")
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
    return render_template("legacy/recent.html", nav_tab="recent", recent_items=recent_items)


@bp.route("/healthz")
def healthz():
    remote_addr = request.remote_addr or ""
    if remote_addr not in {"127.0.0.1", "::1"}:
        abort(404)
    return jsonify({"ok": True})


@bp.route("/favicon.ico")
def favicon():
    return current_app.send_static_file("v2/favicon.svg")


# 공군갤 airforce
# 야갤 baseball_new10
# 싱벙갤 singlebungle1472
# 그림갤 drawing
@bp.route("/legacy/board")
def board():
    page = _positive_int_arg("page", 1)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    recommend = _normalize_recommend()
    kind = _normalize_gallery_kind(request.args.get("kind"))
    nav_mode = _normalize_nav_mode(request.args.get("nav"))
    head_id = _normalize_head_id(request.args.get("headid"))
    search_type, search_keyword = _current_search_context()
    ret, head_categories = run_async(
        _load_board_payload(
            page,
            board,
            recommend,
            kind=kind,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
        )
    )

    response = make_response(
        render_template(
            "legacy/board.html",
            datas=ret,
            page=page,
            board=board,
            recommend=recommend,
            kind=kind,
            nav_tab=_nav_tab_for_gallery(board, recommend, nav_mode),
            nav_mode=nav_mode,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
            head_categories=head_categories,
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend)
    return response


@bp.route("/board/times")
def board_times():
    page = _positive_int_arg("page", 1)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    recommend = _normalize_recommend()
    kind = _normalize_gallery_kind(request.args.get("kind"))
    head_id = _normalize_head_id(request.args.get("headid"))
    search_type, search_keyword = _current_search_context()
    target_ids = _target_post_ids_arg()

    try:
        times = run_async(
            async_board_precise_times(
                page,
                board,
                recommend,
                kind=kind,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
                target_ids=target_ids,
            )
        )
    except Exception:
        current_app.logger.exception("Failed to fetch board precise times")
        return jsonify({"ok": False, "times": {}, "error": "board_time_fetch_failed"}), 502

    return jsonify({"ok": True, "times": {str(key): format_display_time(value) for key, value in (times or {}).items()}})


@bp.route("/media")
def media():
    src = (request.args.get("src") or "").strip()
    normalized_src = normalize_media_url_shape(src)
    if not normalized_src:
        abort(400)
    board, pid, kind = _media_request_context()
    return build_media_response(normalized_src, board, pid, kind=kind, range_header=request.headers.get("Range"))


@bp.route("/movie")
def movie():
    movie_no = (request.args.get("no") or "").strip()
    if not movie_no.isdigit():
        abort(400)
    board, pid, kind = _media_request_context()
    return build_movie_response(movie_no, board, pid, kind=kind)


@bp.route("/legacy/read")
def read():
    pid = _safe_int(request.args.get("pid", 0), 0)
    if pid <= 0:
        abort(404)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    kind = _normalize_gallery_kind(request.args.get("kind"))
    recommend = _normalize_recommend()
    source_page = max(_safe_int(request.args.get("source_page", 0), 0), 0)
    head_id = _normalize_head_id(request.args.get("headid"))
    search_type, search_keyword = _current_search_context()
    data, comments, images = run_async(
        async_read(
            pid,
            board,
            kind=kind,
            recommend=recommend,
            head_id=head_id,
            **_search_call_kwargs(search_type, search_keyword),
        )
    )
    _format_read_payload_times(data, comments)
    embedded_related_posts = _serialize_related_posts(data.pop("related_posts", []))

    for comment in comments:
        if comment.get("dccon"):
            comment["dccon"] = url_for("main.media", src=comment["dccon"], board=board, pid=pid, kind=kind)

    data["html"] = prepare_read_html(data.get("html"), images, board, pid, kind, search_keyword=search_keyword)
    response = make_response(
        render_template(
            "legacy/read.html",
            data=data,
            comments=comments,
            images=images,
            board=board,
            pid=pid,
            kind=kind,
            recommend=recommend,
            source_page=source_page,
            head_id=head_id,
            search_type=search_type,
            search_keyword=search_keyword,
            embedded_related_posts=embedded_related_posts,
            social_meta=_read_social_meta(
                data,
                images,
                board,
                pid,
                kind,
                recommend,
                source_page,
                search_type,
                search_keyword,
                head_id,
            ),
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
    head_id = _normalize_head_id(request.args.get("headid"))
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
                    head_id=head_id,
                    **_search_call_kwargs(search_type, search_keyword),
                )
            )
        except Exception:
            current_app.logger.exception("Failed to fetch related posts")
            return jsonify({"ok": False, "items": [], "error": "related_fetch_failed"}), 502

    return jsonify(
        {
            "ok": True,
            "items": _serialize_related_posts(posts),
            "has_more": has_more,
        }
    )

def register_routes(app):
    from .routes_v2 import bp_v2

    app.add_template_global(board_url, "board_url")
    app.add_template_global(read_url, "read_url")
    app.register_blueprint(bp)
    app.register_blueprint(bp_v2)
