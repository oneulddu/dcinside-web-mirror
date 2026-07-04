#-*- coding:utf-8 -*-
"""v2 프론트엔드 블루프린트.

기존 v1 라우트(`routes.py`)는 건드리지 않고 `/v2` 경로에 새 템플릿/정적 자원으로
같은 서비스 계층을 렌더링한다. `/media`, `/movie`, `/read/related`, `/board/times`
등 JSON/프록시 엔드포인트는 main 블루프린트 것을 그대로 사용한다.
"""
import time

from flask import Blueprint, abort, current_app, make_response, render_template, request, url_for

from .routes import (
    _add_kind_param,
    _add_search_params,
    _current_search_context,
    _external_url_for,
    _format_read_payload_times,
    _format_cache_time,
    _load_board_payload,
    _nav_tab_for_gallery,
    _normalize_board_id,
    _normalize_gallery_kind,
    _normalize_head_id,
    _normalize_nav_mode,
    _normalize_recommend,
    _positive_int_arg,
    _read_social_meta,
    _safe_int,
    _search_call_kwargs,
    _serialize_related_posts,
)
from .services.async_bridge import run_async
from .services.core import async_read
from .services.heung import get_heung_galleries, search_galleries
from .services.html_sanitizer import prepare_read_html
from .services.recent import (
    RECENT_MAX_ITEMS,
    format_recent_time,
    load_recent_entries,
    touch_recent_gallery,
)

bp_v2 = Blueprint("v2", __name__, url_prefix="/v2")

GALLERY_KIND_LABELS = {
    None: "일반",
    "normal": "일반",
    "minor": "마이너",
    "mini": "미니",
    "person": "인물",
}


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


def _recent_gallery_name_lookup(rows):
    return {}


def board_url_v2(
    board,
    recommend=0,
    page=1,
    kind=None,
    nav=None,
    search_type=None,
    search_keyword=None,
    head_id=None,
    gallery_name=None,
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
    clean_name = _clean_gallery_name(gallery_name)
    if clean_name:
        params["gallery_name"] = clean_name
    return url_for("v2.board", **params)


def read_url_v2(
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
    clean_name = _clean_gallery_name(gallery_name)
    if clean_name:
        params["gallery_name"] = clean_name
    return url_for("v2.read", **params)


def _read_canonical_url_v2(board, pid, recommend, source_page, kind, search_type, search_keyword, head_id):
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
    return _external_url_for("v2.read", **params)


def _read_social_meta_v2(data, images, board, pid, kind, recommend, source_page, search_type, search_keyword, head_id):
    social_meta = _read_social_meta(
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
    )
    social_meta["url"] = _read_canonical_url_v2(
        board,
        pid,
        recommend,
        source_page,
        kind,
        search_type,
        search_keyword,
        head_id,
    )
    return social_meta


@bp_v2.context_processor
def _inject_v2_url_helpers():
    # v2 템플릿에서는 board_url/read_url이 v2 엔드포인트를 가리키도록 전역을 가린다.
    return {"board_url": board_url_v2, "read_url": read_url_v2}


@bp_v2.route("/")
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
        "v2/index.html",
        title=("%s 갤러리 검색 - 숨터" % heung_q) if heung_q else "숨터 - 가볍게 읽는 공간",
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


@bp_v2.route("/recent")
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
        display_name = saved_name or looked_up_name or board
        recent_items.append(
            {
                "board": board,
                "display_name": display_name,
                "gallery_name": saved_name or looked_up_name,
                "kind": kind,
                "kind_label": GALLERY_KIND_LABELS.get(kind, kind or "일반"),
                "recommend": 1 if _safe_int(row.get("recommend", 0), 0) == 1 else 0,
                "visited_at_str": format_recent_time(row.get("visited_at")),
            }
        )
    return render_template("v2/recent.html", title="최근 방문 갤러리 - 숨터", nav_tab="recent", recent_items=recent_items)


@bp_v2.route("/board")
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
            "v2/board.html",
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
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend, name=gallery_name)
    return response


@bp_v2.route("/read")
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
            "v2/read.html",
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
            social_meta=_read_social_meta_v2(
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
