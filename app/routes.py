#-*- coding:utf-8 -*-
import html
import ipaddress
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from flask import Blueprint, abort, current_app, jsonify, make_response, redirect, render_template, request, url_for

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
from .services import link_preview, youtube_meta
from .services.recent import (
    RECENT_MAX_ITEMS,
    clear_recent_galleries,
    format_recent_time,
    load_recent_entries,
    remove_recent_gallery,
    touch_recent_gallery,
)

bp = Blueprint("main", __name__)

BOARD_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
ALLOWED_GALLERY_KINDS = {"normal", "minor", "mini", "person"}
ALLOWED_NAV_MODES = {"ai"}
DEFAULT_SEARCH_TYPE = "subject_m"
SITE_NAME = "숨터"
SOCIAL_DESCRIPTION_MAX_LENGTH = 180
SOCIAL_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
SOCIAL_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".m3u8")
GALLERY_KIND_LABELS = {
    None: "일반",
    "normal": "일반",
    "minor": "마이너",
    "mini": "미니",
    "person": "인물",
}


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


def _clean_gallery_name(value):
    name = " ".join(str(value or "").split())
    return name[:80] or None


def _stored_gallery_name(row):
    board = (row.get("board") or "").strip()
    name = _clean_gallery_name(row.get("name"))
    if not name or name == board:
        return None
    return name


def _gallery_display_name(board, gallery_name=None):
    name = _clean_gallery_name(gallery_name)
    board_id = (board or "").strip()
    if name and name != board_id:
        return name
    return board_id


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


def _board_link_params(
    board,
    recommend=0,
    page=1,
    kind=None,
    nav=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    refresh=False,
):
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
    if _safe_bool(refresh):
        params["refresh"] = 1
    return params


def board_url(
    board,
    recommend=0,
    page=1,
    kind=None,
    nav=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    gallery_name=None,
    refresh=False,
):
    params = _board_link_params(
        board,
        recommend,
        page,
        kind,
        nav,
        search_type,
        search_keyword,
        head_id,
        refresh,
    )
    clean_name = _clean_gallery_name(gallery_name)
    if clean_name:
        params["gallery_name"] = clean_name
    return url_for("main.board", **params)


def _read_link_params(board, pid, recommend=0, source_page=None, kind=None, search_type=None, search_keyword=None, head_id=None):
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
    return params


def read_url(
    board,
    pid,
    recommend=0,
    source_page=None,
    kind=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    gallery_name=None,
):
    params = _read_link_params(board, pid, recommend, source_page, kind, search_type, search_keyword, head_id)
    clean_name = _clean_gallery_name(gallery_name)
    if clean_name:
        params["gallery_name"] = clean_name
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


def _redirect_compat(endpoint):
    target = url_for(endpoint)
    if request.query_string:
        target = f"{target}?{request.query_string.decode('latin-1')}"
    return redirect(target, code=308)


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
    params = _read_link_params(board, pid, recommend, source_page, kind, search_type, search_keyword, head_id)
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


async def _load_board_payload(
    page,
    board,
    recommend,
    kind=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    pagination_collector=None,
    force_refresh=False,
):
    kwargs = {
        "kind": kind,
        "max_scan_pages": 1,
        "search_type": search_type,
        "search_keyword": search_keyword,
        "head_id": head_id,
        "pagination_collector": pagination_collector,
    }
    if force_refresh:
        kwargs["force_refresh"] = True
    return await async_index_with_head_categories(page, board, recommend, **kwargs)


def _nav_tab_for_gallery(board, recommend=0, nav_mode=None):
    if nav_mode == "ai":
        return "ai"
    if board == "dcbest" or _safe_int(recommend, 0) == 1:
        return "best"
    return "all"


def _heung_index_context(page, heung_q, get_heung_func=None, search_func=None, now_func=None):
    get_heung_func = get_heung_galleries if get_heung_func is None else get_heung_func
    search_func = search_galleries if search_func is None else search_func
    now_func = time.time if now_func is None else now_func
    heung_items = []
    heung_updated_at = None
    heung_error = None
    if heung_q:
        try:
            heung_items = search_func(heung_q)
            heung_updated_at = now_func()
        except Exception:
            current_app.logger.exception("Failed to search DCinside galleries")
            heung_error = "갤러리 검색 결과를 가져오지 못했습니다."
    else:
        try:
            heung_items, heung_updated_at = get_heung_func()
        except Exception:
            current_app.logger.exception("Failed to load heung galleries")
            heung_error = "흥한 갤러리 목록을 가져오지 못했습니다."

    total_items = len(heung_items)
    total_pages = max(1, (total_items + 19) // 20)
    page = max(1, min(page, total_pages))
    start = (page - 1) * 20
    end = min(start + 20, total_items)
    page_items = heung_items[start:end]

    return {
        "nav_tab": "all",
        "heung_items": page_items,
        "heung_page": page,
        "heung_total_pages": total_pages,
        "heung_total_items": total_items,
        "heung_start_rank": (start + 1) if total_items else 0,
        "heung_end_rank": end,
        "heung_error": heung_error,
        "heung_q": heung_q,
        "heung_updated_at_str": _format_cache_time(heung_updated_at) if heung_updated_at else "-",
    }


def _recent_gallery_name_lookup(rows):
    need_lookup = {
        ((row.get("board") or "").strip(), row.get("kind"))
        for row in rows or []
        if (row.get("board") or "").strip() and not _stored_gallery_name(row)
    }
    if not need_lookup:
        return {}

    try:
        heung_items, _updated_at = get_heung_galleries()
    except Exception:
        current_app.logger.exception("Failed to look up recent gallery names")
        return {}

    names = {}
    for item in heung_items or []:
        board = (item.get("board_id") or "").strip()
        name = _clean_gallery_name(item.get("name"))
        if not board or not name:
            continue
        kind = _normalize_gallery_kind(item.get("board_kind"), abort_on_invalid=False)
        names.setdefault((board, kind), name)
        names.setdefault((board, None), name)

    return {
        key: value
        for key, value in names.items()
        if key in need_lookup or (key[0], None) in need_lookup
    }


@bp.route("/")
def index():
    page = _safe_int(request.args.get("heung_page", 1), 1)
    heung_q = (request.args.get("heung_q") or "").strip()
    context = _heung_index_context(page, heung_q)

    return render_template(
        "index.html",
        title=("%s 갤러리 검색 - 숨터" % heung_q) if heung_q else "숨터 - 가볍게 읽는 공간",
        **context,
    )


@bp.route("/v2/")
@bp.route("/legacy/")
def index_compat_redirect():
    return _redirect_compat("main.index")


@bp.route("/recent")
def recent():
    rows = load_recent_entries()
    recent_items = []
    recent_rows = rows[:RECENT_MAX_ITEMS]
    stored_names = {}
    for row in recent_rows:
        board = (row.get("board") or "").strip()
        if not board:
            continue
        kind = row.get("kind")
        name = _stored_gallery_name(row)
        if name:
            stored_names.setdefault((board, kind), name)
            stored_names.setdefault((board, None), name)

    gallery_names = _recent_gallery_name_lookup(recent_rows)
    for row in recent_rows:
        board = row["board"]
        kind = row.get("kind")
        saved_name = _stored_gallery_name(row)
        looked_up_name = (
            stored_names.get((board, kind))
            or stored_names.get((board, None))
            or gallery_names.get((board, kind))
            or gallery_names.get((board, None))
        )
        recent_items.append(
            {
                "board": board,
                "display_name": saved_name or looked_up_name or board,
                "gallery_name": saved_name or looked_up_name,
                "kind": kind,
                "kind_label": GALLERY_KIND_LABELS.get(kind, kind or "일반"),
                "recommend": 1 if _safe_int(row.get("recommend", 0), 0) == 1 else 0,
                "visited_at_str": format_recent_time(row.get("visited_at")),
            }
        )
    return render_template(
        "recent.html",
        title="최근 방문 갤러리 - 숨터",
        nav_tab="recent",
        recent_items=recent_items,
    )


@bp.route("/v2/recent")
@bp.route("/legacy/recent")
def recent_compat_redirect():
    return _redirect_compat("main.recent")


@bp.route("/recent/remove", methods=["POST"])
def recent_remove():
    _reject_cross_origin_post()
    response = redirect(url_for("main.recent"))
    remove_recent_gallery(
        response,
        request.form.get("board"),
        request.form.get("kind"),
        recommend=_safe_int(request.form.get("recommend", 0), 0),
    )
    return response


@bp.route("/recent/clear", methods=["POST"])
def recent_clear():
    _reject_cross_origin_post()
    response = redirect(url_for("main.recent"))
    clear_recent_galleries(response)
    return response


def _reject_cross_origin_post():
    # 프록시 뒤에서는 request.scheme을 신뢰하기 어려워 오탐할 수 있으므로 netloc만 비교한다.
    origin = request.headers.get("Origin")
    if origin:
        if urlparse(origin).netloc != request.host:
            abort(403)
        return

    referer = request.headers.get("Referer")
    if not referer or urlparse(referer).netloc != request.host:
        abort(403)


@bp.route("/healthz")
def healthz():
    if not _is_loopback_addr(request.remote_addr):
        abort(404)
    return jsonify({"ok": True})


def _is_loopback_addr(raw):
    try:
        addr = ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_loopback


@bp.route("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.svg")


# 공군갤 airforce
# 야갤 baseball_new10
# 싱벙갤 singlebungle1472
# 그림갤 drawing
@bp.route("/board")
def board():
    page = _positive_int_arg("page", 1)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    recommend = _normalize_recommend()
    kind = _normalize_gallery_kind(request.args.get("kind"))
    gallery_name = _clean_gallery_name(request.args.get("gallery_name"))
    gallery_display_name = _gallery_display_name(board, gallery_name)
    nav_mode = _normalize_nav_mode(request.args.get("nav"))
    head_id = _normalize_head_id(request.args.get("headid"))
    search_type, search_keyword = _current_search_context()
    force_refresh = _safe_bool(request.args.get("refresh"))
    pagination = {}
    board_payload_kwargs = {
        "kind": kind,
        "search_type": search_type,
        "search_keyword": search_keyword,
        "head_id": head_id,
        "pagination_collector": pagination,
    }
    if force_refresh:
        board_payload_kwargs["force_refresh"] = True
    ret, head_categories = run_async(
        _load_board_payload(page, board, recommend, **board_payload_kwargs)
    )

    current_page = _safe_int(pagination.get("current_page"), 0)
    if current_page > 0 and current_page < page and pagination.get("has_next") is False:
        return redirect(
            board_url(
                board,
                recommend=recommend,
                page=current_page,
                kind=kind,
                nav=nav_mode,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
                gallery_name=gallery_name,
                refresh=force_refresh,
            ),
            code=302,
        )

    response = make_response(
        render_template(
            "board.html",
            title="%s 게시판 - 숨터" % gallery_display_name,
            datas=ret,
            page=page,
            board=board,
            recommend=recommend,
            kind=kind,
            gallery_name=gallery_name,
            gallery_display_name=gallery_display_name,
            nav_tab=_nav_tab_for_gallery(board, recommend, nav_mode),
            nav_mode=nav_mode,
            search_type=search_type,
            search_keyword=search_keyword,
            head_id=head_id,
            head_categories=head_categories,
            board_has_next=pagination.get("has_next"),
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend, name=gallery_name)
    return response


@bp.route("/v2/board")
@bp.route("/legacy/board")
def board_compat_redirect():
    return _redirect_compat("main.board")


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
    return build_media_response(
        normalized_src,
        board,
        pid,
        kind=kind,
        range_header=request.headers.get("Range"),
        head_only=request.method == "HEAD",
    )


@bp.route("/movie")
def movie():
    movie_no = (request.args.get("no") or "").strip()
    if not movie_no.isdigit():
        abort(400)
    board, pid, kind = _media_request_context()
    return build_movie_response(movie_no, board, pid, kind=kind)


@bp.route("/embed/youtube-size")
def youtube_size():
    raw_ids = (request.args.get("ids") or "").split(",")
    sizes = youtube_meta.sizes_for_ids(raw_ids)
    if not sizes:
        abort(400)
    response = jsonify(sizes)
    # 실패(null)가 섞인 응답이 브라우저에 하루 동안 남으면 짧은 unknown TTL이 무의미해진다.
    max_age = 86400 if all(sizes.values()) else 300
    response.headers["Cache-Control"] = f"public, max-age={max_age}"
    return response


@bp.route("/embed/link-preview")
def embed_link_preview():
    url = (request.args.get("url") or "").strip()
    if not link_preview.is_valid_preview_url(url):
        abort(400)
    preview = link_preview.fetch_preview(url)
    if preview is link_preview.RATE_LIMITED:
        response = jsonify({"ok": False})
        response.status_code = 503
        response.headers["Retry-After"] = "10"
        response.headers["Cache-Control"] = "no-store"
        return response
    if preview is None:
        response = jsonify({"ok": False})
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    response = jsonify({"ok": True, **preview})
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.route("/read")
def read():
    pid = _safe_int(request.args.get("pid", 0), 0)
    if pid <= 0:
        abort(404)
    board = _normalize_board_id(request.args.get("board", "airforce"))
    kind = _normalize_gallery_kind(request.args.get("kind"))
    gallery_name = _clean_gallery_name(request.args.get("gallery_name"))
    gallery_display_name = _gallery_display_name(board, gallery_name)
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
            "read.html",
            title="%s - 숨터" % (data.get("title") or board),
            data=data,
            comments=comments,
            images=images,
            board=board,
            pid=pid,
            kind=kind,
            gallery_name=gallery_name,
            gallery_display_name=gallery_display_name,
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
    touch_recent_gallery(response, board, kind, recommend=recommend, name=gallery_name)
    return response


@bp.route("/v2/read")
@bp.route("/legacy/read")
def read_compat_redirect():
    return _redirect_compat("main.read")


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
    app.add_template_global(board_url, "board_url")
    app.add_template_global(read_url, "read_url")
    app.register_blueprint(bp)
