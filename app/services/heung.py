import json
import logging
import os
import re
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


HTTP_TIMEOUT = _env_int("MIRROR_HTTP_TIMEOUT", 20)
HEUNG_CACHE_TTL = _env_int("MIRROR_HEUNG_CACHE_TTL", 3600)
HEUNG_CACHE_FILE = os.getenv("MIRROR_HEUNG_CACHE_FILE", os.path.join(INSTANCE_DIR, "heung_gallery_cache.json"))
SEARCH_CACHE_TTL = max(_env_int("MIRROR_HEUNG_SEARCH_CACHE_TTL", 60), 0)
SEARCH_CACHE_MAX_ITEMS = max(_env_int("MIRROR_HEUNG_SEARCH_CACHE_MAX_ITEMS", 256), 0)
HEUNG_CACHE = {"updated_at": 0.0, "items": []}
SEARCH_CACHE = {}
HEUNG_CACHE_LOCK = threading.Lock()
SEARCH_CACHE_LOCK = threading.Lock()
HEUNG_REFRESH_LOCK = threading.Lock()


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
    soup = BeautifulSoup(res.text, "lxml")
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
    cache_dir = os.path.dirname(HEUNG_CACHE_FILE) or "."
    cache_name = os.path.basename(HEUNG_CACHE_FILE)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=cache_dir,
            prefix=f".{cache_name}.",
            suffix=".tmp",
            delete=False,
        ) as file_obj:
            tmp = file_obj.name
            json.dump(payload, file_obj, ensure_ascii=False)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp, HEUNG_CACHE_FILE)
        tmp = None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Failed to clean up heung gallery cache temp file", exc_info=True)


def _heung_cache_snapshot():
    with HEUNG_CACHE_LOCK:
        return list(HEUNG_CACHE["items"]), float(HEUNG_CACHE["updated_at"] or 0.0)


def _replace_heung_cache(updated_at, items):
    cache_items = list(items or [])
    with HEUNG_CACHE_LOCK:
        HEUNG_CACHE["updated_at"] = float(updated_at)
        HEUNG_CACHE["items"] = cache_items
    return list(cache_items), float(updated_at)


def _load_heung_file_cache_if_empty():
    with HEUNG_CACHE_LOCK:
        needs_file_cache = not HEUNG_CACHE["items"]
    if not needs_file_cache:
        return

    cached = _read_heung_cache_file()
    if not cached:
        return

    cached_updated_at = float(cached["updated_at"])
    cached_items = list(cached["items"])
    with HEUNG_CACHE_LOCK:
        if not HEUNG_CACHE["items"] or cached_updated_at > float(HEUNG_CACHE["updated_at"] or 0.0):
            HEUNG_CACHE["updated_at"] = cached_updated_at
            HEUNG_CACHE["items"] = cached_items


def _is_heung_cache_fresh(items, updated_at, now=None):
    now = time.time() if now is None else now
    return bool(items) and (now - float(updated_at or 0.0)) < HEUNG_CACHE_TTL


def _refresh_heung_galleries():
    fresh_items = _fetch_heung_galleries()
    if not fresh_items:
        raise RuntimeError("empty heung gallery result")
    fetched_at = time.time()
    fresh_items, fetched_at = _replace_heung_cache(fetched_at, fresh_items)
    try:
        _write_heung_cache_file(fetched_at, fresh_items)
    except Exception:
        logger.warning("Failed to write heung gallery cache file", exc_info=True)
    return fresh_items, fetched_at


def _refresh_heung_galleries_in_background():
    try:
        _refresh_heung_galleries()
    except Exception:
        logger.warning("Failed to refresh heung galleries in background", exc_info=True)
    finally:
        HEUNG_REFRESH_LOCK.release()


def _start_heung_refresh_background():
    acquired = HEUNG_REFRESH_LOCK.acquire(blocking=False)
    if not acquired:
        return False
    try:
        thread = threading.Thread(target=_refresh_heung_galleries_in_background, daemon=True)
        thread.start()
        return True
    except Exception:
        HEUNG_REFRESH_LOCK.release()
        raise


def get_heung_galleries():
    _load_heung_file_cache_if_empty()

    cached_items, cached_updated_at = _heung_cache_snapshot()
    if _is_heung_cache_fresh(cached_items, cached_updated_at):
        return cached_items, cached_updated_at
    if cached_items:
        _start_heung_refresh_background()
        return cached_items, cached_updated_at

    acquired = HEUNG_REFRESH_LOCK.acquire(blocking=False)
    if not acquired:
        with HEUNG_REFRESH_LOCK:
            refreshed_items, refreshed_updated_at = _heung_cache_snapshot()
            if refreshed_items:
                return refreshed_items, refreshed_updated_at

        acquired = HEUNG_REFRESH_LOCK.acquire(blocking=False)
        if not acquired:
            refreshed_items, refreshed_updated_at = _heung_cache_snapshot()
            if refreshed_items:
                return refreshed_items, refreshed_updated_at
            raise RuntimeError("heung gallery refresh is unavailable")

    try:
        cached_items, cached_updated_at = _heung_cache_snapshot()
        if _is_heung_cache_fresh(cached_items, cached_updated_at):
            return cached_items, cached_updated_at

        try:
            return _refresh_heung_galleries()
        except Exception:
            fallback_items, fallback_updated_at = _heung_cache_snapshot()
            if fallback_items:
                return fallback_items, fallback_updated_at
            raise
    finally:
        HEUNG_REFRESH_LOCK.release()


def _copy_search_items(items):
    return [dict(item) for item in (items or [])]


def _search_cache_get(key):
    if SEARCH_CACHE_TTL <= 0 or SEARCH_CACHE_MAX_ITEMS <= 0:
        return None
    now = time.time()
    with SEARCH_CACHE_LOCK:
        entry = SEARCH_CACHE.get(key)
        if not entry:
            return None
        if entry["expires_at"] <= now:
            SEARCH_CACHE.pop(key, None)
            return None
        return _copy_search_items(entry["items"])


def _prune_search_cache_locked(now):
    expired_keys = [key for key, entry in SEARCH_CACHE.items() if entry["expires_at"] <= now]
    for key in expired_keys:
        SEARCH_CACHE.pop(key, None)
    overflow = len(SEARCH_CACHE) - SEARCH_CACHE_MAX_ITEMS
    if overflow <= 0:
        return
    oldest_keys = sorted(SEARCH_CACHE, key=lambda key: SEARCH_CACHE[key]["expires_at"])[:overflow]
    for key in oldest_keys:
        SEARCH_CACHE.pop(key, None)


def _search_cache_set(key, items):
    if SEARCH_CACHE_TTL <= 0 or SEARCH_CACHE_MAX_ITEMS <= 0:
        return
    now = time.time()
    with SEARCH_CACHE_LOCK:
        _prune_search_cache_locked(now)
        SEARCH_CACHE[key] = {
            "items": _copy_search_items(items),
            "expires_at": now + SEARCH_CACHE_TTL,
        }
        _prune_search_cache_locked(now)


def search_galleries(query):
    query_text = (query or "").strip()
    cache_key = query_text.lower()
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": "Mozilla/5.0"}
    encoded = quote(query_text, safe="")
    url = f"https://search.dcinside.com/gallery/q/{encoded}"
    res = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    container = soup.select_one("div.integrate_cont.gallsch_result_all")
    if container is None:
        _search_cache_set(cache_key, [])
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
    seen = {}

    def kind_priority(kind):
        if kind == "normal":
            return 0
        if kind in {"minor", "mini", "person"}:
            return 1
        return -1

    for li in container.select("ul.integrate_cont_list > li"):
        anchor = li.select_one("a.gallname_txt[href]")
        if anchor is None:
            continue
        href = anchor.get("href", "")
        board_id = _extract_board_id(href)
        if not board_id:
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
            "internal_supported": board_kind in {"normal", "minor", "mini", "person"},
        })
        existing_index = seen.get(board_id)
        if existing_index is None:
            seen[board_id] = len(items) - 1
            continue
        existing = items[existing_index]
        if kind_priority(board_kind) > kind_priority(existing.get("board_kind")):
            items[existing_index] = items.pop()
        else:
            items.pop()
    _search_cache_set(cache_key, items)
    return items
