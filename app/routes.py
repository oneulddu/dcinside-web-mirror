#-*- coding:utf-8 -*-
import asyncio
import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlparse
from flask import Blueprint, Response, jsonify, make_response, render_template, request, url_for
from bs4 import BeautifulSoup
import requests

from .services.core import async_index, async_read, async_related_by_position

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

bp = Blueprint("main", __name__)
ASYNC_FALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


HTTP_TIMEOUT = _env_int("MIRROR_HTTP_TIMEOUT", 20)
MEDIA_CACHE_MAX_AGE = _env_int("MIRROR_MEDIA_CACHE_MAX_AGE", 86400)
HEUNG_CACHE_TTL = _env_int("MIRROR_HEUNG_CACHE_TTL", 3600)
HEUNG_CACHE_FILE = os.getenv("MIRROR_HEUNG_CACHE_FILE", os.path.join(INSTANCE_DIR, "heung_gallery_cache.json"))
HEUNG_CACHE = {"updated_at": 0.0, "items": []}
HEUNG_CACHE_LOCK = threading.Lock()
RECENT_COOKIE_NAME = "recent_galleries"
RECENT_COOKIE_TTL = _env_int("MIRROR_RECENT_COOKIE_TTL", 60 * 60 * 24 * 30)
RECENT_MAX_ITEMS = _env_int("MIRROR_RECENT_MAX_ITEMS", 30)
RECENT_SERVER_CACHE = {}
RECENT_SERVER_CACHE_LOCK = threading.Lock()


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return ASYNC_FALLBACK_EXECUTOR.submit(lambda: asyncio.run(coro)).result()


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
            }
        )
    return rows


def _extract_board_id(href):
    if not href:
        return None
    query = parse_qs(urlparse(href).query)
    ids = query.get("id")
    return ids[0] if ids else None


def _fetch_heung_galleries():
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get("https://gall.dcinside.com/", headers=headers, timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    layer = soup.select_one("#heung_gall_all_lyr")
    if layer is None:
        raise RuntimeError("heung_gall_all_lyr not found")

    items = []
    for anchor in layer.select("ul.pop_hotmgall_listbox li > a[href]"):
        rank_el = anchor.select_one("span.num")
        if rank_el is None:
            continue
        match = re.search(r"\d+", rank_el.get_text(strip=True))
        if not match:
            continue

        rank = int(match.group(0))
        board_id = _extract_board_id(anchor.get("href"))
        if not board_id:
            continue

        title = anchor.get_text(" ", strip=True)
        title = re.sub(r"^\d+\.\s*", "", title).strip()
        items.append({
            "rank": rank,
            "name": title,
            "board_id": board_id,
        })

    items.sort(key=lambda row: row["rank"])
    rank_map = {}
    for row in items:
        rank_map[row["rank"]] = row
    return [rank_map[key] for key in sorted(rank_map.keys())][:300]


def _read_heung_cache_file():
    if not os.path.exists(HEUNG_CACHE_FILE):
        return None
    try:
        with open(HEUNG_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        updated_at = float(payload.get("updated_at", 0.0))
        items = payload.get("items", [])
        if updated_at <= 0 or not isinstance(items, list):
            return None
        return {"updated_at": updated_at, "items": items}
    except Exception:
        return None


def _write_heung_cache_file(updated_at, items):
    payload = {"updated_at": float(updated_at), "items": items}
    tmp = HEUNG_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, HEUNG_CACHE_FILE)


def _format_cache_time(ts):
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(ts, tz=kst).strftime("%Y-%m-%d %H:%M:%S KST")


def _format_recent_time(ts):
    if not ts:
        return "-"
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(float(ts), tz=kst).strftime("%Y-%m-%d %H:%M")


def _recent_cache_key():
    remote_addr = (request.remote_addr or "").strip()
    user_agent = (request.headers.get("User-Agent") or "").strip()
    return f"{remote_addr}|{user_agent}"


def _load_recent_entries():
    raw = request.cookies.get(RECENT_COOKIE_NAME, "")
    rows = []
    if raw:
        raw = raw.strip().strip('"')
        decoded = raw
        try:
            padded = raw + "=" * (-len(raw) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except Exception:
            decoded = raw.replace("\\054", ",")
        try:
            parsed = json.loads(decoded)
        except Exception:
            parsed = []

        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                board = (item.get("board") or "").strip()
                if not board:
                    continue
                kind = (item.get("kind") or "").strip().lower() or None
                visited_at = float(item.get("visited_at", 0) or 0)
                rows.append({"board": board, "kind": kind, "visited_at": visited_at})

    if rows:
        return rows

    key = _recent_cache_key()
    with RECENT_SERVER_CACHE_LOCK:
        cached = RECENT_SERVER_CACHE.get(key, [])
        return list(cached)


def _save_recent_cookie(response, entries):
    payload = json.dumps(entries[:RECENT_MAX_ITEMS], ensure_ascii=False, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    response.set_cookie(
        RECENT_COOKIE_NAME,
        encoded,
        max_age=RECENT_COOKIE_TTL,
        path="/",
        samesite="Lax",
    )


def _touch_recent_gallery(response, board, kind):
    board_id = (board or "").strip()
    if not board_id:
        return

    rows = _load_recent_entries()
    new_row = {
        "board": board_id,
        "kind": (kind or "").strip().lower() or None,
        "visited_at": time.time(),
    }

    deduped = [new_row]
    for row in rows:
        if row.get("board") == new_row["board"] and row.get("kind") == new_row["kind"]:
            continue
        deduped.append(row)

    deduped = deduped[:RECENT_MAX_ITEMS]
    with RECENT_SERVER_CACHE_LOCK:
        RECENT_SERVER_CACHE[_recent_cache_key()] = list(deduped)

    _save_recent_cookie(response, deduped)


def _get_heung_galleries():
    now = time.time()
    with HEUNG_CACHE_LOCK:
        if not HEUNG_CACHE["items"]:
            cached = _read_heung_cache_file()
            if cached:
                HEUNG_CACHE["updated_at"] = cached["updated_at"]
                HEUNG_CACHE["items"] = cached["items"]

        cached_items = HEUNG_CACHE["items"]
        cached_updated_at = HEUNG_CACHE["updated_at"]
        if cached_items and (now - cached_updated_at) < HEUNG_CACHE_TTL:
            return cached_items, cached_updated_at

        try:
            fresh_items = _fetch_heung_galleries()
            fetched_at = time.time()
            HEUNG_CACHE["updated_at"] = fetched_at
            HEUNG_CACHE["items"] = fresh_items
            _write_heung_cache_file(fetched_at, fresh_items)
            return fresh_items, fetched_at
        except Exception:
            if cached_items:
                return cached_items, cached_updated_at
            raise


def _search_galleries(query):
    headers = {"User-Agent": "Mozilla/5.0"}
    encoded = quote(query, safe="")
    url = f"https://search.dcinside.com/gallery/q/{encoded}"
    res = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    container = soup.select_one("div.integrate_cont.gallsch_result_all")
    if container is None:
        return []

    def infer_kind(href):
        if "/mgallery/" in href:
            return "minor"
        if "/mini/" in href:
            return "mini"
        if "/person/" in href:
            return "person"
        return "normal"

    items = []
    seen = set()
    for li in container.select("ul.integrate_cont_list > li"):
        anchor = li.select_one("a.gallname_txt[href]")
        if anchor is None:
            continue
        href = anchor.get("href", "")
        board_id = _extract_board_id(href)
        if not board_id or board_id in seen:
            continue

        name = " ".join(anchor.stripped_strings)
        name = re.sub(r"\s*[ⓜⓝⓟ]$", "", name).strip()

        ranking_el = li.select_one("span.info.ranking")
        count_el = li.select_one("span.info.txtnum")
        details = []
        if ranking_el:
            details.append(ranking_el.get_text(" ", strip=True))
        if count_el:
            details.append(count_el.get_text(" ", strip=True))

        board_kind = infer_kind(href)
        kind_label = {
            "normal": "일반",
            "minor": "마이너",
            "mini": "미니",
            "person": "인물",
        }.get(board_kind, "일반")
        items.append({
            "rank": None,
            "name": name,
            "board_id": board_id,
            "kind": kind_label,
            "board_kind": board_kind,
            "extra": " | ".join(details),
            "source_url": href,
            "internal_supported": board_kind in {"normal", "minor", "mini"},
        })
        seen.add(board_id)
    return items

@bp.route("/")
def index():
    page = _safe_int(request.args.get("heung_page", 1), 1)
    heung_q = (request.args.get("heung_q") or "").strip()

    heung_items = []
    heung_updated_at = None
    heung_error = None
    if heung_q:
        try:
            heung_items = _search_galleries(heung_q)
            heung_updated_at = time.time()
        except Exception:
            heung_error = "갤러리 검색 결과를 가져오지 못했습니다."
    else:
        try:
            heung_items, heung_updated_at = _get_heung_galleries()
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
    rows = _load_recent_entries()
    recent_items = []
    for row in rows[:RECENT_MAX_ITEMS]:
        recent_items.append(
            {
                "board": row["board"],
                "kind": row.get("kind"),
                "visited_at_str": _format_recent_time(row.get("visited_at")),
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
    ret = _run_async(async_index(page, board, recommend, kind=kind))
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
    _touch_recent_gallery(response, board, kind)
    return response


@bp.route("/media")
def media():
    src = (request.args.get("src") or "").strip()
    board = (request.args.get("board") or "").strip()
    pid = _safe_int(request.args.get("pid", 0), 0)
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith(("http://", "https://")):
        return ("", 400)

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G960N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Mobile Safari/537.36",
        "Referer": f"https://m.dcinside.com/board/{board}/{pid}" if board and pid else "https://m.dcinside.com/",
    }
    cookies = {"__gat_mobile_search": "1", "list_count": "200"}
    try:
        upstream = requests.get(src, headers=headers, cookies=cookies, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return ("", 502)

    response = Response(upstream.content, status=upstream.status_code)
    response.headers["Content-Type"] = upstream.headers.get("Content-Type", "application/octet-stream")
    response.headers["Cache-Control"] = f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    return response


@bp.route("/read")
def read():
    pid = _safe_int(request.args.get("pid", 0), 0)
    board = str(request.args.get("board", "airforce")).strip() or "airforce"
    kind = (request.args.get("kind") or "").strip().lower() or None
    data, comments, images = _run_async(async_read(pid, board, kind=kind))
    # Parse soup, for image replation
    soup=BeautifulSoup(data["html"],'html.parser')
    idx = 0
    for i in soup.find_all("img", "lazy"):
        if idx >= len(images):
            break
        i["src"] = url_for("main.media", src=images[idx], board=board, pid=pid)
        i["loading"] = "lazy"
        i["decoding"] = "async"
        idx += 1

    for comment in comments:
        if comment.get("dccon"):
            comment["dccon"] = url_for("main.media", src=comment["dccon"], board=board, pid=pid)

    data["html"] = str(soup)
    read_nav_tab = "best" if board == "dcbest" else "all"
    response = make_response(
        render_template("read.html", data=data, comments=comments, images=images, board=board, pid=pid, kind=kind, nav_tab=read_nav_tab)
    )
    _touch_recent_gallery(response, board, kind)
    return response


@bp.route("/read/related")
def read_related():
    pid = _safe_int(request.args.get("pid", 0), 0)
    board = str(request.args.get("board", "airforce")).strip() or "airforce"
    kind = (request.args.get("kind") or "").strip().lower() or None
    limit = _safe_int(request.args.get("limit", 12), 12)
    limit = max(1, min(limit, 30))

    posts = []
    if pid > 0:
        try:
            posts = _run_async(async_related_by_position(pid, board, kind=kind, limit=limit))
        except Exception:
            posts = []

    return jsonify(
        {
            "ok": True,
            "items": _serialize_related_posts(posts),
        }
    )

def register_routes(app):
    app.register_blueprint(bp)
