#-*- coding:utf-8 -*-
import os
import time
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, make_response, render_template, request, url_for
from bs4 import BeautifulSoup

from .services.async_bridge import run_async
from .services.core import async_index, async_read, async_related_after_position
from .services.heung import get_heung_galleries, search_galleries
from .services.html_sanitizer import rewrite_content_images, sanitize_html_fragment
from .services.media_proxy import build_media_response
from .services.recent import (
    RECENT_MAX_ITEMS,
    format_recent_time,
    load_recent_entries,
    touch_recent_gallery,
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

bp = Blueprint("main", __name__)


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    page = _safe_int(request.args.get("page", 1), 1)
    board = str(request.args.get("board", "airforce")).strip() or "airforce"
    recommend = _safe_int(request.args.get("recommend", 0), 0)
    kind = (request.args.get("kind") or "").strip().lower() or None
    nav_mode = (request.args.get("nav") or "").strip().lower() or None
    ret = run_async(async_index(page, board, recommend, kind=kind))
    if nav_mode == "ai":
        nav_tab = "ai"
    elif board == "dcbest" or recommend == 1:
        nav_tab = "best"
    else:
        nav_tab = "all"
    response = make_response(
        render_template(
            "board.html",
            datas=ret,
            page=page,
            board=board,
            recommend=recommend,
            kind=kind,
            nav_tab=nav_tab,
            nav_mode=nav_mode,
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend)
    return response


@bp.route("/media")
def media():
    src = (request.args.get("src") or "").strip()
    board = (request.args.get("board") or "").strip()
    pid = _safe_int(request.args.get("pid", 0), 0)
    kind = (request.args.get("kind") or "").strip().lower() or None
    return build_media_response(src, board, pid, kind=kind)


@bp.route("/read")
def read():
    pid = _safe_int(request.args.get("pid", 0), 0)
    board = str(request.args.get("board", "airforce")).strip() or "airforce"
    kind = (request.args.get("kind") or "").strip().lower() or None
    recommend = 1 if _safe_int(request.args.get("recommend", 0), 0) == 1 else 0
    source_page = max(_safe_int(request.args.get("source_page", 0), 0), 0)
    data, comments, images = run_async(async_read(pid, board, kind=kind, recommend=recommend))
    embedded_related_posts = _serialize_related_posts(data.pop("related_posts", []))
    soup = BeautifulSoup(data.get("html") or "", "html.parser")
    rewrite_content_images(soup, images, board, pid, kind)

    for comment in comments:
        if comment.get("dccon"):
            comment["dccon"] = url_for("main.media", src=comment["dccon"], board=board, pid=pid, kind=kind)

    data["html"] = sanitize_html_fragment(str(soup))
    read_nav_tab = "best" if board == "dcbest" or recommend == 1 else "all"
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
            embedded_related_posts=embedded_related_posts,
            nav_tab=read_nav_tab,
        )
    )
    touch_recent_gallery(response, board, kind, recommend=recommend)
    return response


@bp.route("/read/related")
def read_related():
    pid = _safe_int(request.args.get("pid", 0), 0)
    board = str(request.args.get("board", "airforce")).strip() or "airforce"
    kind = (request.args.get("kind") or "").strip().lower() or None
    recommend = 1 if _safe_int(request.args.get("recommend", 0), 0) == 1 else 0
    limit = _safe_int(request.args.get("limit", 12), 12)
    limit = max(1, min(limit, 30))
    source_page = max(_safe_int(request.args.get("source_page", 0), 0), 0)
    after_pid = max(_safe_int(request.args.get("after_pid", 0), 0), 0)

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
    app.register_blueprint(bp)
