import asyncio
import json
import logging
import re
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse

import aiohttp
import lxml.html

from app.services.cache_utils import cache_delete as _shared_cache_delete
from app.services.cache_utils import cache_get as _shared_cache_get
from app.services.cache_utils import cache_prune as _shared_cache_prune
from app.services.cache_utils import cache_set_after_insert
from app.services.cache_utils import env_int

logger = logging.getLogger(__name__)


def to_int(value, default=0):
    if value is None:
        return default
    digits = re.sub(r"[^0-9-]", "", str(value))
    if not digits:
        return default
    try:
        return int(digits)
    except ValueError:
        return default


def to_optional_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def cache_get(cache, lock, key):
    return _shared_cache_get(cache, lock, key)


def cache_prune(cache, now, max_items):
    _shared_cache_prune(cache, now, max_items)


def cache_set(cache, lock, key, value, ttl, max_items):
    cache_set_after_insert(cache, lock, key, value, ttl, max_items, prune_func=cache_prune)


def cache_delete(cache, lock, key):
    _shared_cache_delete(cache, lock, key)


DOCS_PER_PAGE = 200
BOARD_LIST_PAGE_SIZE = 30
BOARD_TIME_LOOKAHEAD_PAGES = 1
HTTP_TIMEOUT = env_int("MIRROR_HTTP_TIMEOUT", 20)
BOARD_KIND_CACHE_TTL = max(env_int("MIRROR_BOARD_KIND_CACHE_TTL", 21600), 0)
BOARD_KIND_CACHE_MAX_ITEMS = 2048
DC_CONN_LIMIT = max(env_int("MIRROR_DC_CONN_LIMIT", 20), 1)
DC_DNS_CACHE_TTL = max(env_int("MIRROR_DC_DNS_CACHE_TTL", 60), 0)
DC_SESSION_COOKIE_ALLOWLIST = frozenset({"_ga", "ci_c"})
MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
PC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

GET_HEADERS = {
    "User-Agent": MOBILE_USER_AGENT,
}
XML_HTTP_REQ_HEADERS = {
    "Accept": "*/*",
    "Connection": "keep-alive",
    "User-Agent": MOBILE_USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.5",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

POST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": MOBILE_USER_AGENT,
}

GALLERY_POSTS_COOKIES = {
    "__gat_mobile_search": "1",
    "list_count": str(DOCS_PER_PAGE),
}

_BOARD_KIND_CACHE = {}
_BOARD_KIND_CACHE_LOCK = threading.Lock()


def unquote(encoded):
    return re.sub(r'\\u([a-fA-F0-9]{4}|[a-fA-F0-9]{2})', lambda m: chr(int(m.group(1), 16)), encoded)
def quote(decoded):
    arr = []
    for c in decoded:
        t = hex(ord(c))[2:].upper() 
        if len(t) >= 4:
            arr.append("%u" + t)
        else:
            arr.append("%" + t)
    return "".join(arr)


from .models import Comment, Document, DocumentIndex, Image
from .parsers import ParserMixin, has_gallery_image_icon, has_gallery_video_icon


class API(ParserMixin):
    __parse_mobile_headtext_tabs = ParserMixin._ParserMixin__parse_mobile_headtext_tabs
    __is_usable_board_page = ParserMixin._ParserMixin__is_usable_board_page
    __is_usable_document_page = ParserMixin._ParserMixin__is_usable_document_page
    __compact_text = ParserMixin._ParserMixin__compact_text
    __mobile_document_id_from_href = ParserMixin._ParserMixin__mobile_document_id_from_href
    __extract_gallog_author_id = ParserMixin._ParserMixin__extract_gallog_author_id
    __extract_mobile_author_id = ParserMixin._ParserMixin__extract_mobile_author_id
    __find_mobile_list_link = ParserMixin._ParserMixin__find_mobile_list_link
    __extract_mobile_title_subject = ParserMixin._ParserMixin__extract_mobile_title_subject
    __extract_mobile_ginfo = ParserMixin._ParserMixin__extract_mobile_ginfo
    __extract_mobile_comment_count = ParserMixin._ParserMixin__extract_mobile_comment_count
    __mobile_icon_flags = ParserMixin._ParserMixin__mobile_icon_flags
    __gallery_flags = ParserMixin._ParserMixin__gallery_flags
    __make_board_index = ParserMixin._ParserMixin__make_board_index
    __parse_mobile_list_item = ParserMixin._ParserMixin__parse_mobile_list_item
    __parse_embedded_mobile_posts = ParserMixin._ParserMixin__parse_embedded_mobile_posts
    __parse_mobile_comment_li = ParserMixin._ParserMixin__parse_mobile_comment_li
    __mobile_comment_rows = ParserMixin._ParserMixin__mobile_comment_rows
    __parse_embedded_mobile_comments = ParserMixin._ParserMixin__parse_embedded_mobile_comments
    __extract_top_level_redirect_url = ParserMixin._ParserMixin__extract_top_level_redirect_url
    __extract_meta_refresh_url = ParserMixin._ParserMixin__extract_meta_refresh_url
    __extract_script_redirect_url = ParserMixin._ParserMixin__extract_script_redirect_url
    __upsert_gallery = ParserMixin._ParserMixin__upsert_gallery
    __parse_legacy_mobile_board_row = ParserMixin._ParserMixin__parse_legacy_mobile_board_row
    __extract_pc_board_author = ParserMixin._ParserMixin__extract_pc_board_author
    __extract_pc_board_counts = ParserMixin._ParserMixin__extract_pc_board_counts
    __pc_board_flags = ParserMixin._ParserMixin__pc_board_flags
    __parse_pc_board_row = ParserMixin._ParserMixin__parse_pc_board_row
    __first_text = ParserMixin._ParserMixin__first_text
    __parse_document_header = ParserMixin._ParserMixin__parse_document_header
    __parse_document_counts = ParserMixin._ParserMixin__parse_document_counts
    __prepare_document_content = ParserMixin._ParserMixin__prepare_document_content
    __pick_document_image_src = ParserMixin._ParserMixin__pick_document_image_src
    __pick_document_video_src = ParserMixin._ParserMixin__pick_document_video_src
    __pick_change_gif_fallback_image_src = ParserMixin._ParserMixin__pick_change_gif_fallback_image_src
    __is_placeholder_document_image_src = ParserMixin._ParserMixin__is_placeholder_document_image_src
    __document_image_elements = ParserMixin._ParserMixin__document_image_elements
    __real_document_image_sources = ParserMixin._ParserMixin__real_document_image_sources
    __real_document_video_sources = ParserMixin._ParserMixin__real_document_video_sources
    __real_document_video_poster_sources = ParserMixin._ParserMixin__real_document_video_poster_sources
    __real_document_media_sources = ParserMixin._ParserMixin__real_document_media_sources
    __has_placeholder_document_images = ParserMixin._ParserMixin__has_placeholder_document_images
    __document_video_element = ParserMixin._ParserMixin__document_video_element
    __document_contents_text = ParserMixin._ParserMixin__document_contents_text
    __document_images = ParserMixin._ParserMixin__document_images
    __normalize_poll_url = ParserMixin._ParserMixin__normalize_poll_url
    __is_poll_url = ParserMixin._ParserMixin__is_poll_url
    __poll_card_element = ParserMixin._ParserMixin__poll_card_element
    __poll_preview_url = ParserMixin._ParserMixin__poll_preview_url
    __parse_poll_summary = ParserMixin._ParserMixin__parse_poll_summary
    __parse_pc_comment = ParserMixin._ParserMixin__parse_pc_comment
    __parse_time = ParserMixin._ParserMixin__parse_time

    def __init__(self):
        timeout = aiohttp.ClientTimeout(
            total=HTTP_TIMEOUT,
            connect=min(10, HTTP_TIMEOUT),
            sock_read=HTTP_TIMEOUT,
        )
        connector = aiohttp.TCPConnector(
            limit=DC_CONN_LIMIT,
            ttl_dns_cache=DC_DNS_CACHE_TTL,
        )
        self.session = aiohttp.ClientSession(
            headers=GET_HEADERS,
            cookies={"_ga": "GA1.2.693521455.1588839880"},
            timeout=timeout,
            connector=connector,
        )
        self.last_board_headtexts = []
    async def close(self):
        await self.session.close()
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args, **kwargs):
        await self.close()
    async def watch(self, board_id):
        pass
    def __extract_board_id_from_href(self, href):
        if not href:
            return None
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if "id" in query and query["id"]:
            return query["id"][0]
        path = (parsed.path or "").rstrip("/")
        if not path:
            return None
        return path.split("/")[-1]

    def __dedupe_urls(self, urls):
        seen = set()
        unique = []
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(url)
        return unique

    def __board_kind_cache_key(self, board_id, kind=None, recommend=False, search_keyword=None):
        return (
            board_id,
            (kind or "").lower(),
            1 if recommend else 0,
            bool((search_keyword or "").strip()),
        )

    def __list_url_pattern(self, url):
        parsed = urlparse(url or "")
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host == "m.dcinside.com":
            if path.startswith("/mini/"):
                return "mobile_mini"
            if path.startswith("/board/"):
                return "mobile"
            return None
        if host != "gall.dcinside.com":
            return None
        if path.startswith("/mgallery/board/lists/"):
            return "minor"
        if path.startswith("/mini/board/lists/"):
            return "mini"
        if path.startswith("/person/board/lists/"):
            return "person"
        if path.startswith("/board/lists/"):
            return "normal"
        return None

    def __get_cached_list_url(self, urls, cache_key):
        pattern = cache_get(_BOARD_KIND_CACHE, _BOARD_KIND_CACHE_LOCK, cache_key)
        if not pattern:
            return None, None
        for url in urls:
            if self.__list_url_pattern(url) == pattern:
                return url, pattern
        cache_delete(_BOARD_KIND_CACHE, _BOARD_KIND_CACHE_LOCK, cache_key)
        return None, None

    def __cache_list_url_pattern(self, cache_key, used_url):
        pattern = self.__list_url_pattern(used_url)
        if not pattern:
            return
        cache_set(
            _BOARD_KIND_CACHE,
            _BOARD_KIND_CACHE_LOCK,
            cache_key,
            pattern,
            BOARD_KIND_CACHE_TTL,
            BOARD_KIND_CACHE_MAX_ITEMS,
        )

    def __invalidate_list_url_pattern(self, cache_key):
        cache_delete(_BOARD_KIND_CACHE, _BOARD_KIND_CACHE_LOCK, cache_key)

    def __prepare_headers(self, url, headers=None):
        prepared = dict(headers or {})
        host = (urlparse(url).netloc or "").lower()
        if host in {"gall.dcinside.com", "search.dcinside.com"}:
            prepared["User-Agent"] = PC_USER_AGENT
        else:
            prepared.setdefault("User-Agent", MOBILE_USER_AGENT)
        return prepared

    def __is_pc_request(self, url):
        host = (urlparse(url).netloc or "").lower()
        return host in {"gall.dcinside.com", "search.dcinside.com"}

    def __is_mobile_request(self, url):
        host = (urlparse(url).netloc or "").lower()
        return host == "m.dcinside.com"

    def __is_rate_limited_response(self, status, text):
        if status == 429:
            return True
        if text and any(
            phrase in text
            for phrase in [
                "Too Many Requests",
                "Too Many Attempts",
                "너무 많은 요청",
                "penalty-box",
            ]
        ):
            return True
        return False

    def __prune_session_cookies(self):
        cookie_jar = getattr(self.session, "cookie_jar", None)
        clear = getattr(cookie_jar, "clear", None)
        if not callable(clear):
            return
        clear(lambda morsel: morsel.key not in DC_SESSION_COOKIE_ALLOWLIST)

    def __required_attr(self, parsed, xpath, attr, field_name):
        nodes = parsed.xpath(xpath)
        if not nodes:
            raise RuntimeError(f"required field not found: {field_name}")
        value = nodes[0].get(attr)
        if value is None:
            raise RuntimeError(f"required field is empty: {field_name}")
        return value

    def __optional_attr(self, parsed, xpath, attr, default=None):
        nodes = parsed.xpath(xpath)
        if not nodes:
            return default
        value = nodes[0].get(attr)
        return default if value is None else value

    def __required_text(self, parsed, xpath, field_name):
        nodes = parsed.xpath(xpath)
        if not nodes:
            raise RuntimeError(f"required field not found: {field_name}")
        text = nodes[0].text_content() if hasattr(nodes[0], "text_content") else str(nodes[0])
        text = (text or "").strip()
        if not text:
            raise RuntimeError(f"required field is empty: {field_name}")
        return text

    async def __request_text(self, method, url, headers=None, data=None, cookies=None):
        request_headers = self.__prepare_headers(url, headers)

        async with self.session.request(
            method,
            url,
            headers=request_headers,
            data=data,
            cookies=cookies,
        ) as res:
            text = await res.text()
            status = res.status
            response_headers = dict(res.headers)

        self.__prune_session_cookies()

        if self.__is_rate_limited_response(status, text[:1000]):
            logger.warning("rate limited: status=%s url=%s", status, url)
            raise RuntimeError(f"rate limited: {status}")

        return status, response_headers, text

    def __normalize_head_id(self, head_id):
        if head_id is None:
            return None
        text = str(head_id).strip()
        if not text:
            return None
        if not re.fullmatch(r"\d{1,8}", text):
            return None
        return text

    def __build_head_id_suffix(self, head_id):
        normalized = self.__normalize_head_id(head_id)
        if normalized is None:
            return ""
        return "&" + urlencode({"headid": normalized})

    def __build_pc_head_id_suffix(self, head_id):
        normalized = self.__normalize_head_id(head_id)
        if normalized is None:
            return ""
        return "&" + urlencode({"search_head": normalized})

    def __with_pc_list_page_size(self, url):
        separator = "&" if "?" in url else "?"
        return url + separator + urlencode({"list_num": BOARD_LIST_PAGE_SIZE})

    def __build_list_urls(self, board_id, page, recommend=False, kind=None, search_type=None, search_keyword=None, head_id=None):
        kind = (kind or "").lower()
        urls = []
        mobile_recommend_suffix = "&recommend=1" if recommend else ""
        pc_recommend_suffix = "&exception_mode=recommend" if recommend else ""
        head_id_suffix = self.__build_head_id_suffix(head_id)
        pc_head_id_suffix = self.__build_pc_head_id_suffix(head_id)
        mobile_search_suffix = self.__build_mobile_search_suffix(search_type, search_keyword)
        pc_search_suffix = self.__build_pc_search_suffix(search_type, search_keyword)

        if kind == "mini":
            urls.append("https://m.dcinside.com/mini/{}?page={}{}{}{}".format(board_id, page, mobile_recommend_suffix, head_id_suffix, mobile_search_suffix))
        elif recommend:
            urls.append("https://m.dcinside.com/board/{}?recommend=1&page={}{}{}".format(board_id, page, head_id_suffix, mobile_search_suffix))
        else:
            urls.append("https://m.dcinside.com/board/{}?page={}{}{}".format(board_id, page, head_id_suffix, mobile_search_suffix))

        kind_urls = {
            "normal": "https://gall.dcinside.com/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "minor": "https://gall.dcinside.com/mgallery/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "mini": "https://gall.dcinside.com/mini/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "person": "https://gall.dcinside.com/person/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
        }
        if kind in kind_urls:
            urls.append(kind_urls[kind])

        urls.extend([
            "https://gall.dcinside.com/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "https://gall.dcinside.com/mgallery/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "https://gall.dcinside.com/mini/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
            "https://gall.dcinside.com/person/board/lists/?id={}&page={}{}{}{}".format(board_id, page, pc_recommend_suffix, pc_head_id_suffix, pc_search_suffix),
        ])
        return self.__dedupe_urls(urls)
    def __normalize_search_type(self, search_type):
        value = (search_type or "").strip()
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
        return "subject_m"

    def __build_mobile_search_suffix(self, search_type=None, search_keyword=None):
        keyword = (search_keyword or "").strip()
        if not keyword:
            return ""
        return "&" + urlencode(
            {
                "s_type": self.__normalize_search_type(search_type),
                "serval": keyword,
            }
        )

    def __build_pc_search_suffix(self, search_type=None, search_keyword=None):
        keyword = (search_keyword or "").strip()
        if not keyword:
            return ""
        pc_type_map = {
            "subject_m": "search_subject_memo",
            "subject": "search_subject",
            "memo": "search_memo",
            "name": "search_name",
            "comment": "search_comment",
        }
        return "&" + urlencode(
            {
                "s_type": pc_type_map.get(self.__normalize_search_type(search_type), "search_subject_memo"),
                "s_keyword": keyword,
            }
        )

    def __build_mobile_view_suffix(self, recommend=False, search_type=None, search_keyword=None, head_id=None):
        params = []
        if recommend:
            params.append(("recommend", "1"))
        normalized_head_id = self.__normalize_head_id(head_id)
        if normalized_head_id is not None:
            params.append(("headid", normalized_head_id))
        keyword = (search_keyword or "").strip()
        if keyword:
            params.append(("s_type", self.__normalize_search_type(search_type)))
            params.append(("serval", keyword))
        return ("?" + urlencode(params)) if params else ""

    def __build_pc_view_suffix(self, recommend=False, search_type=None, search_keyword=None, head_id=None):
        params = []
        if recommend:
            params.append(("recommend", "1"))
        normalized_head_id = self.__normalize_head_id(head_id)
        if normalized_head_id is not None:
            params.append(("search_head", normalized_head_id))
        keyword = (search_keyword or "").strip()
        if keyword:
            pc_type_map = {
                "subject_m": "search_subject_memo",
                "subject": "search_subject",
                "memo": "search_memo",
                "name": "search_name",
                "comment": "search_comment",
            }
            params.append(("s_type", pc_type_map.get(self.__normalize_search_type(search_type), "search_subject_memo")))
            params.append(("s_keyword", keyword))
        return ("&" + urlencode(params)) if params else ""

    def __build_view_urls(self, board_id, document_id, kind=None, recommend=False, search_type=None, search_keyword=None, head_id=None):
        kind = (kind or "").lower()
        urls = []
        mobile_suffix = self.__build_mobile_view_suffix(recommend, search_type, search_keyword, head_id=head_id)
        pc_suffix = self.__build_pc_view_suffix(recommend, search_type, search_keyword, head_id=head_id)

        if kind == "mini":
            urls.append("https://m.dcinside.com/mini/{}/{}{}".format(board_id, document_id, mobile_suffix))
        else:
            urls.append("https://m.dcinside.com/board/{}/{}{}".format(board_id, document_id, mobile_suffix))

        kind_urls = {
            "normal": "https://gall.dcinside.com/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "minor": "https://gall.dcinside.com/mgallery/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "mini": "https://gall.dcinside.com/mini/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "person": "https://gall.dcinside.com/person/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
        }
        if kind in kind_urls:
            urls.append(kind_urls[kind])

        urls.extend([
            "https://gall.dcinside.com/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "https://gall.dcinside.com/mgallery/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "https://gall.dcinside.com/mini/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
            "https://gall.dcinside.com/person/board/view/?id={}&no={}{}".format(board_id, document_id, pc_suffix),
        ])
        return self.__dedupe_urls(urls)

    def __build_pc_view_urls(self, board_id, document_id, kind=None):
        kind = (kind or "").lower()
        urls = []
        kind_urls = {
            "normal": "https://gall.dcinside.com/board/view/?id={}&no={}".format(board_id, document_id),
            "minor": "https://gall.dcinside.com/mgallery/board/view/?id={}&no={}".format(board_id, document_id),
            "mini": "https://gall.dcinside.com/mini/board/view/?id={}&no={}".format(board_id, document_id),
            "person": "https://gall.dcinside.com/person/board/view/?id={}&no={}".format(board_id, document_id),
        }
        if kind in kind_urls:
            urls.append(kind_urls[kind])
        urls.extend([
            "https://gall.dcinside.com/board/view/?id={}&no={}".format(board_id, document_id),
            "https://gall.dcinside.com/mgallery/board/view/?id={}&no={}".format(board_id, document_id),
            "https://gall.dcinside.com/mini/board/view/?id={}&no={}".format(board_id, document_id),
            "https://gall.dcinside.com/person/board/view/?id={}&no={}".format(board_id, document_id),
        ])
        return self.__dedupe_urls(urls)

    async def __fetch_parsed_from_urls(self, urls, validator=None):
        queue = list(urls)
        idx = 0
        while idx < len(queue):
            url = queue[idx]
            idx += 1

            try:
                status, _, text = await self.__request_text("GET", url)
                if status >= 400:
                    continue
                if not text:
                    continue

                redirect_url = self.__extract_top_level_redirect_url(text)
                if redirect_url:
                    redirect_url = self.__normalize_redirect_url(url, redirect_url)
                    if redirect_url and redirect_url not in queue:
                        queue.append(redirect_url)
                    continue
                parsed = lxml.html.fromstring(text)
                if validator and not validator(parsed, text, url):
                    continue
                return parsed, text, url
            except Exception:
                continue
        return None, "", None

    def __normalize_redirect_url(self, current_url, redirect_url):
        normalized_url = urljoin(current_url, redirect_url)
        current_parsed = urlparse(current_url)
        current_query = parse_qs(current_parsed.query)
        preserve_recommend = (
            "1" in current_query.get("recommend", [])
            or "recommend" in current_query.get("exception_mode", [])
        )
        preserved_head_id = self.__normalize_head_id(
            (current_query.get("headid") or current_query.get("search_head") or [None])[0]
        )
        if not preserve_recommend and preserved_head_id is None:
            return normalized_url

        parsed = urlparse(normalized_url)
        target_host = (parsed.netloc or "").lower()
        target_path = parsed.path or ""
        target_head_key = "search_head" if target_host in {"gall.dcinside.com", "search.dcinside.com"} else "headid"
        target_recommend_key = (
            "exception_mode"
            if target_host in {"gall.dcinside.com", "search.dcinside.com"} and "/lists" in target_path
            else "recommend"
        )
        target_recommend_value = "recommend" if target_recommend_key == "exception_mode" else "1"
        query_items = []
        recommend_added = False
        head_added = False
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key in {"recommend", "exception_mode"}:
                if preserve_recommend:
                    if not recommend_added:
                        query_items.append((target_recommend_key, target_recommend_value))
                        recommend_added = True
                else:
                    query_items.append((key, value))
                continue
            if key in {"headid", "search_head"}:
                if preserved_head_id is not None and key == target_head_key and not head_added:
                    query_items.append((target_head_key, preserved_head_id))
                    head_added = True
                elif preserved_head_id is None:
                    query_items.append((key, value))
                continue
            query_items.append((key, value))
        if preserve_recommend and not recommend_added:
            query_items.append((target_recommend_key, target_recommend_value))
        if preserved_head_id is not None and not head_added:
            query_items.append((target_head_key, preserved_head_id))
        return parsed._replace(query=urlencode(query_items)).geturl()
    async def __gallery_miner_from_web(self, category, category_code, name=None):
        # Prime ci_c cookie required by search_gallmain endpoint.
        await self.__request_text("GET", "https://gall.dcinside.com/m")

        cookies = self.session.cookie_jar.filter_cookies("https://gall.dcinside.com")
        ci_token = cookies.get("ci_c").value if cookies.get("ci_c") else ""

        headers = XML_HTTP_REQ_HEADERS.copy()
        headers["Referer"] = "https://gall.dcinside.com/m"
        headers["Origin"] = "https://gall.dcinside.com"
        headers["User-Agent"] = PC_USER_AGENT

        payload = {
            "ci_t": ci_token,
            "key": category_code,
            "type": "category",
            "cateName": category,
            "galltype": "M",
        }

        status, _, text = await self.__request_text(
            "POST",
            "https://gall.dcinside.com/ajax/gallery_main_ajax/search_gallmain/",
            headers=headers,
            data=payload,
        )
        if status != 200:
            raise RuntimeError(f"search_gallmain failed: {status}")

        parsed = lxml.html.fromstring(text)
        gallerys = {}
        for anchor in parsed.xpath("//a[@href]"):
            board_name = anchor.text_content().strip()
            board_id = self.__extract_board_id_from_href(anchor.get("href"))
            if name and (not board_name or name not in board_name):
                continue
            self.__upsert_gallery(gallerys, board_name, board_id)

        if not gallerys:
            raise RuntimeError("empty gallery result from web")
        return gallerys

    async def __gallery_miner_from_mobile(self, category_code, name=None):
        url = "https://m.dcinside.com/mcategory/" + category_code
        gallerys = {}
        lis = []

        status, _, text = await self.__request_text("GET", url)
        if status >= 400:
            raise RuntimeError(f"gallery mobile failed: {status}")
        parsed = lxml.html.fromstring(text)
        for item in parsed.xpath("/html/body/div/div/div/section[3]/ul/li"):
            lis.append(item)
        for item in parsed.xpath('//*[@id="base-div"]/ul/li'):
            lis.append(item)
        for item in lis:
            anchor = item[0]
            board_name = (anchor.text or "").strip()
            board_id = self.__extract_board_id_from_href(anchor.get("href"))
            if name and (not board_name or name not in board_name):
                continue
            self.__upsert_gallery(gallerys, board_name, board_id)
        return gallerys

    async def gallery_miner(self, category="게임", name=None):
        urllist = {
            "여성":"3", "생물":"4", "이슈":"5", "여행/풍경":"6", "음식":"7",
            "디지털/IT":"8", "합성":"9", "정부/기관":"10", "수능":"11", "취미":"12", "학술":"13",
            "교육":"14", "교통/운송":"15", "패션":"16", "밀리터리":"17", "성인":"18",
            "생활":"19", "직업":"20", "게임":"21", "국내방송":"22", "음악":"23", "스포츠":"24",
            "스포츠스타":"25", "연예":"26", "대학":"27", "정치인/유명인":"28", "성공/계발":"29", "지역":"30", "해외방송":"31",
            "질문":"33", "기타":"34", "기업":"35", "쇼핑/장터":"37", "미디어":"38",
            "만화/애니":"39", "건강/심리":"40", "금융/재테크":"41", "공무원":"42",
        }
        category_code = urllist[category]
        try:
            return await self.__gallery_miner_from_web(category, category_code, name)
        except Exception:
            # Keep backward-compatible behavior if web endpoint changes.
            return await self.__gallery_miner_from_mobile(category_code, name)

    async def gallery(self, name=None):
        url = "https://m.dcinside.com/galltotal"
        gallerys={}
        status, _, text = await self.__request_text("GET", url)
        if status >= 400:
            raise RuntimeError(f"gallery total failed: {status}")
        parsed = lxml.html.fromstring(text)
        for i in parsed.xpath('//*[@id="total_1"]/li'):
            for e in i.iter():
                if e.tag == "a":
                    board_name = e.text
                    board_id = e.get("href").split("/")[-1]
                    if name:
                        if name in board_name:
                            gallerys[board_name] = board_id
                    else:
                        gallerys[board_name] = board_id
        return gallerys
    async def board(self, board_id, num=-1, start_page=1, recommend=False, document_id_upper_limit=None, document_id_lower_limit=None, is_minor=False, kind=None, max_scan_pages=None, search_type=None, search_keyword=None, head_id=None, headtexts_collector=None):
        page = start_page
        scanned_pages = 0
        if headtexts_collector is not None:
            headtexts_collector[:] = []
        else:
            self.last_board_headtexts = []
        headtexts_captured = False
        upper_limit = to_optional_int(document_id_upper_limit)
        lower_limit = to_optional_int(document_id_lower_limit)
        while num:
            if max_scan_pages is not None and scanned_pages >= max_scan_pages:
                break
            list_urls = self.__build_list_urls(
                board_id,
                page,
                recommend=recommend,
                kind=kind,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
            )
            cache_key = self.__board_kind_cache_key(
                board_id,
                kind=kind,
                recommend=recommend,
                search_keyword=search_keyword,
            )
            cached_url, cached_pattern = self.__get_cached_list_url(list_urls, cache_key)
            if cached_url and cached_url != list_urls[0]:
                parsed, text, used_url = await self.__fetch_parsed_from_urls(
                    [cached_url],
                    validator=self.__is_usable_board_page,
                )
                if parsed is None:
                    self.__invalidate_list_url_pattern(cache_key)
                    parsed, text, used_url = await self.__fetch_parsed_from_urls(
                        list_urls,
                        validator=self.__is_usable_board_page,
                    )
            else:
                parsed, text, used_url = await self.__fetch_parsed_from_urls(
                    list_urls,
                    validator=self.__is_usable_board_page,
                )
            if cached_pattern and used_url and self.__list_url_pattern(used_url) != cached_pattern:
                self.__invalidate_list_url_pattern(cache_key)
            if used_url:
                self.__cache_list_url_pattern(cache_key, used_url)
            scanned_pages += 1
            if parsed is None:
                break
            if not headtexts_captured:
                headtexts = self.__parse_mobile_headtext_tabs(parsed)
                if headtexts_collector is not None:
                    headtexts_collector[:] = headtexts
                else:
                    self.last_board_headtexts = headtexts
                headtexts_captured = True
            if "등록된 게시물이 없습니다." in text:
                break
            yielded_in_page = 0
            is_mobile_source = self.__is_mobile_request(used_url)

            mobile_rows = [
                row
                for row in parsed.xpath("//ul[contains(@class, 'gall-detail-lst')]/li")
                if not row.get("class", "").startswith("ad")
            ]
            if mobile_rows:
                for row in mobile_rows:
                    indexdata = self.__parse_mobile_list_item(
                        row,
                        board_id,
                        kind=kind,
                        is_mobile_source=is_mobile_source,
                        recommend=recommend,
                    )
                    if indexdata is None:
                        continue
                    document_id = to_optional_int(indexdata.id)
                    if document_id is None:
                        continue
                    if upper_limit is not None and upper_limit <= document_id:
                        continue
                    if lower_limit is not None and lower_limit >= document_id:
                        return

                    yield indexdata
                    yielded_in_page += 1
                    num -= 1
                    if num == 0:
                        break
            else:
                rows = parsed.xpath("//tr[contains(@class, 'ub-content') and contains(@class, 'us-post')]")
                for row in rows:
                    indexdata = self.__parse_pc_board_row(
                        row,
                        board_id,
                        kind=kind,
                        recommend=recommend,
                        is_mobile_source=is_mobile_source,
                    )
                    if indexdata is None:
                        continue
                    document_id = to_optional_int(indexdata.id)
                    if document_id is None:
                        continue
                    if upper_limit is not None and upper_limit <= document_id:
                        continue
                    if lower_limit is not None and lower_limit >= document_id:
                        return

                    yield indexdata
                    yielded_in_page += 1
                    num -= 1
                    if num == 0:
                        break

            if yielded_in_page == 0:
                break
            page += 1

    async def board_precise_times(self, board_id, page=1, recommend=False, kind=None, search_type=None, search_keyword=None, head_id=None, target_ids=None):
        precise_times = {}
        requested_ids = {str(value).strip() for value in (target_ids or []) if str(value).strip()}
        remaining_ids = set(requested_ids)
        page_count = 1 + (BOARD_TIME_LOOKAHEAD_PAGES if requested_ids else 0)
        start_page = max(to_int(page, 1), 1)

        for current_page in range(start_page, start_page + page_count):
            list_urls = [
                self.__with_pc_list_page_size(url)
                for url in self.__build_list_urls(
                    board_id,
                    current_page,
                    recommend=recommend,
                    kind=kind,
                    search_type=search_type,
                    search_keyword=search_keyword,
                    head_id=head_id,
                )
                if not self.__is_mobile_request(url)
            ]
            parsed, _, _ = await self.__fetch_parsed_from_urls(
                list_urls,
                validator=self.__is_usable_board_page,
            )
            if parsed is None:
                continue

            rows = parsed.xpath("//tr[contains(@class, 'ub-content') and contains(@class, 'us-post')]")
            for row in rows:
                item = self.__parse_pc_board_row(
                    row,
                    board_id,
                    kind=kind,
                    recommend=recommend,
                    is_mobile_source=False,
                )
                if item is None:
                    continue
                if not getattr(item, "time_is_precise", False):
                    continue
                item_id = str(item.id)
                if requested_ids and item_id not in requested_ids:
                    continue
                precise_times[item_id] = item.time
                remaining_ids.discard(item_id)
            if not remaining_ids:
                break
        return precise_times

    async def __pc_document_media_sources(self, board_id, document_id, kind=None):
        for url in self.__build_pc_view_urls(board_id, document_id, kind=kind):
            try:
                status, _, text = await self.__request_text("GET", url)
            except Exception:
                continue
            if status >= 400 or not text:
                continue
            try:
                parsed = lxml.html.fromstring(text)
            except Exception:
                continue
            containers = parsed.xpath("//div[contains(@class, 'writing_view_box')]")
            if not containers:
                containers = parsed.xpath("//div[@class='thum-txtin']")
            if not containers:
                containers = parsed.xpath("//div[contains(@class, 'thum-txt-area')]")
            if not containers:
                continue
            sources = self.__real_document_media_sources(containers[0])
            if sources:
                return sources
        return []
    async def __repair_placeholder_images_from_pc(self, doc_content, board_id, document_id, kind=None):
        if not self.__has_placeholder_document_images(doc_content):
            return doc_content

        pc_media_sources = await self.__pc_document_media_sources(board_id, document_id, kind=kind)
        if not pc_media_sources:
            return doc_content

        mobile_images = set(self.__document_image_elements(doc_content))
        remaining_media = list(pc_media_sources)

        for el in doc_content.xpath(".//img | .//video | .//source[not(ancestor::video)]"):
            tag = (getattr(el, "tag", "") or "").lower()
            if tag == "img":
                if el not in mobile_images:
                    continue
                current_src = self.__pick_document_image_src(el)
            else:
                current_src = self.__pick_document_video_src(el)

            if not self.__is_placeholder_document_image_src(current_src):
                for idx, media in enumerate(remaining_media):
                    if media["src"] == current_src:
                        remaining_media.pop(idx)
                        break
                continue
            if tag != "img":
                continue
            if not remaining_media:
                break

            replacement = remaining_media.pop(0)
            if replacement["type"] == "image":
                el.set("data-original", replacement["src"])
                el.set("src", replacement["src"])
                for attr in ("data-gif", "data-src", "data-mp4"):
                    el.attrib.pop(attr, None)
                continue

            if replacement["type"] == "video":
                parent = el.getparent()
                if parent is None:
                    continue
                parent.replace(el, self.__document_video_element(replacement["src"]))

        return doc_content
    async def __replace_poll_iframes(self, doc_content):
        for iframe in list(doc_content.xpath(".//iframe[@src]")):
            src = iframe.get("src")
            poll_src = self.__normalize_poll_url(src)
            if not poll_src:
                continue
            try:
                status, _, text = await self.__request_text("GET", self.__poll_preview_url(poll_src))
                poll = self.__parse_poll_summary(text) if status < 400 else None
            except Exception:
                try:
                    status, _, text = await self.__request_text("GET", poll_src)
                    poll = self.__parse_poll_summary(text) if status < 400 else None
                except Exception:
                    poll = None
            iframe.getparent().replace(iframe, self.__poll_card_element(poll_src, poll=poll))
        return doc_content

    async def document(self, board_id, document_id, kind=None, recommend=False, search_type=None, search_keyword=None, head_id=None):
        parsed, text, used_url = await self.__fetch_parsed_from_urls(
            self.__build_view_urls(
                board_id,
                document_id,
                kind=kind,
                recommend=recommend,
                search_type=search_type,
                search_keyword=search_keyword,
                head_id=head_id,
            ),
            validator=self.__is_usable_document_page,
        )
        if parsed is None:
            return None
        is_mobile_source = self.__is_mobile_request(used_url)
        # Try various XPaths for title/meta container
        doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview-tit-box')]")
        if not doc_head_containers:
            # Fallback for minor gallery or dynamic structure
            doc_head_containers = parsed.xpath("//div[@class='gall-tit-box']")
        if not doc_head_containers:
            # PC view fallback (m board can redirect to gall.dcinside.com)
            doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview_head')]")
            
        if not doc_head_containers:
            return None
            
        doc_head_container = doc_head_containers[0]
        
        # Try various XPaths for content container
        doc_content_container = parsed.xpath("//div[@class='thum-txtin']")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'writing_view_box')]")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'thum-txt-area')]")

        if len(doc_content_container):
            header = self.__parse_document_header(doc_head_container)
            title = header["title"]
            subject = header["subject"]
            author = header["author"]
            author_id = header["author_id"]
            author_role = header["author_role"]
            time_str = header["time_str"]

            view_count, voteup_count, votedown_count, logined_voteup_count = self.__parse_document_counts(
                parsed,
                document_id,
                header["meta_text"],
            )

            doc_content = self.__prepare_document_content(doc_content_container[0])
            if is_mobile_source:
                doc_content = await self.__repair_placeholder_images_from_pc(
                    doc_content,
                    board_id,
                    document_id,
                    kind=kind,
                )
            await self.__replace_poll_iframes(doc_content)
            related_posts = []
            embedded_comments = []
            embedded_comment_total = 0
            if is_mobile_source:
                related_posts = self.__parse_embedded_mobile_posts(parsed, board_id, document_id, kind=kind, recommend=recommend)
                embedded_comments, embedded_comment_total = self.__parse_embedded_mobile_comments(parsed)

            return Document(
                    id = document_id,
                    board_id = board_id,
                    title= title,
                    author= author,
                    author_id =author_id,
                    author_role=author_role,
                    contents= self.__document_contents_text(doc_content),
                    images= self.__document_images(doc_content, board_id, document_id),
                    html= lxml.html.tostring(doc_content, encoding=str),
                    view_count= view_count,
                    voteup_count= voteup_count,
                    votedown_count= votedown_count,
                    logined_voteup_count= logined_voteup_count,
                    comments= lambda b=board_id, d=document_id, k=kind, mobile=is_mobile_source: self.comments(b, d, kind=k, prefer_mobile=mobile),
                    time= self.__parse_time(time_str),
                    subject=subject,
                    is_mobile_source=is_mobile_source,
                    related_posts=related_posts,
                    embedded_comments=embedded_comments,
                    embedded_comment_total=embedded_comment_total,
                    )
        else:
            # fail due to unusual tags in mobile version
            # at now, just skip it
            return None

    async def __get_pc_comment_context(self, board_id, document_id, kind=None):
        for url in self.__build_pc_view_urls(board_id, document_id, kind=kind):
            try:
                status, _, text = await self.__request_text("GET", url)
            except Exception:
                continue
            if status >= 400 or not text:
                continue
            parsed = lxml.html.fromstring(text)
            e_s_n_o = parsed.xpath("string(//input[@id='e_s_n_o']/@value)").strip()
            gall_type = parsed.xpath("string(//input[@id='_GALLTYPE_']/@value)").strip()
            if not e_s_n_o or not gall_type:
                continue
            return {
                "referer": url,
                "e_s_n_o": e_s_n_o,
                "board_type": parsed.xpath("string(//input[@id='board_type']/@value)").strip(),
                "_GALLTYPE_": gall_type,
                "secret_article_key": parsed.xpath("string(//input[@id='secret_article_key']/@value)").strip(),
            }
        raise RuntimeError("pc comment context not found")
    async def __comments_from_pc(self, board_id, document_id, num=-1, start_page=1, kind=None):
        if num == 0:
            return
        context = await self.__get_pc_comment_context(board_id, document_id, kind=kind)
        seen_ids = set()

        for page in range(start_page, 999999):
            payload = {
                "id": board_id,
                "no": document_id,
                "cmt_id": board_id,
                "cmt_no": document_id,
                "focus_cno": "",
                "focus_pno": "",
                "e_s_n_o": context["e_s_n_o"],
                "comment_page": str(page),
                "sort": "D",
                "prevCnt": "",
                "board_type": context["board_type"],
                "_GALLTYPE_": context["_GALLTYPE_"],
                "secret_article_key": context["secret_article_key"],
            }
            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": context["referer"],
                "X-Requested-With": "XMLHttpRequest",
            }
            status, _, body = await self.__request_text(
                "POST",
                "https://gall.dcinside.com/board/comment/",
                headers=headers,
                data=payload,
            )
            if status >= 400:
                raise RuntimeError(f"pc comment fetch failed: {status}")
            if not body.strip():
                raise RuntimeError("pc comment fetch returned empty body")

            try:
                data = json.loads(body)
            except Exception as exc:
                raise RuntimeError("pc comment fetch returned invalid json") from exc
            comments = data.get("comments") or []
            if not comments:
                if seen_ids:
                    raise RuntimeError("pc comment fetch ended early")
                break

            yielded_in_page = 0
            for raw in comments:
                comment_id = str(raw.get("no") or "").strip()
                if not comment_id or comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)
                yield self.__parse_pc_comment(raw)
                yielded_in_page += 1
                num -= 1
                if num == 0:
                    return

            if yielded_in_page == 0:
                if seen_ids:
                    raise RuntimeError("pc comment page produced no new comments")
                break

            pagination = str(data.get("pagination") or "")
            max_page = 1
            for page_no in re.findall(r">(\d+)<", pagination):
                try:
                    max_page = max(max_page, int(page_no))
                except ValueError:
                    continue
            if page >= max_page:
                break

    async def __comments_from_mobile(self, board_id, document_id, num=-1, start_page=1, fail_fast=False):
        if num == 0:
            return
        url = "https://m.dcinside.com/ajax/response-comment"
        for page in range(start_page, 999999):
            payload = {"id": board_id, "no": document_id, "cpage": page, "managerskill":"", "del_scope": "1", "csort": ""}
            try:
                status, _, body = await self.__request_text(
                    "POST",
                    url,
                    headers=XML_HTTP_REQ_HEADERS,
                    data=payload,
                )
            except RuntimeError:
                if fail_fast:
                    raise
                break
            if status >= 400:
                if fail_fast:
                    raise RuntimeError(f"mobile comment fetch failed: {status}")
                break
            if not body or not body.strip():
                if fail_fast:
                    raise RuntimeError("mobile comment fetch returned empty body")
                break
            try:
                parsed = lxml.html.fromstring(body)
            except Exception:
                if fail_fast:
                    raise RuntimeError("mobile comment fetch returned invalid html")
                break
            comment_rows = self.__mobile_comment_rows(parsed)
            if not comment_rows:
                if fail_fast:
                    raise RuntimeError("mobile comment page produced no comment rows")
                break
            for li in comment_rows:
                yield self.__parse_mobile_comment_li(li)
                num -= 1
                if num == 0:
                    return
            page_num_els = parsed.xpath(".//span[contains(concat(' ', normalize-space(@class), ' '), ' pgnum ')]")
            if page_num_els:
                page_numbers = [
                    int(value)
                    for value in re.findall(r"\d+", " ".join(el.text_content() for el in page_num_els))
                ]
                if page_numbers and page >= max(page_numbers):
                    break
            else:
                break

    @staticmethod
    def __comment_id(comment):
        return str(getattr(comment, "id", None) or "").strip()

    @staticmethod
    def __pc_comment_fetch_num(remaining, yielded_count):
        if remaining == -1:
            return -1
        if yielded_count:
            return remaining + yielded_count
        return remaining

    async def __deduped_comments(self, comments, yielded_ids, remaining_state, stats, skip_seen=False):
        async for comment in comments:
            stats["seen"] = True
            comment_id = self.__comment_id(comment)
            if skip_seen and comment_id and comment_id in yielded_ids:
                continue
            if comment_id:
                yielded_ids.add(comment_id)
            if remaining_state["value"] != -1:
                remaining_state["value"] -= 1
            yield comment
            if remaining_state["value"] == 0:
                return

    async def comments(self, board_id, document_id, num=-1, start_page=1, kind=None, prefer_mobile=True):
        if num == 0:
            return
        yielded_ids = set()
        remaining_state = {"value": num}

        if prefer_mobile:
            mobile_stats = {"seen": False}
            try:
                mobile_comments = self.__comments_from_mobile(
                    board_id,
                    document_id,
                    num=num,
                    start_page=start_page,
                    fail_fast=True,
                )
                async for comment in self.__deduped_comments(
                    mobile_comments,
                    yielded_ids,
                    remaining_state,
                    mobile_stats,
                ):
                    yield comment
                if mobile_stats["seen"]:
                    return
            except Exception:
                logger.debug(
                    "mobile comments failed, falling back to pc: board=%s doc=%s",
                    board_id,
                    document_id,
                    exc_info=True,
                )

        pc_stats = {"seen": False}
        try:
            pc_fetch_num = self.__pc_comment_fetch_num(remaining_state["value"], len(yielded_ids))
            pc_comments = self.__comments_from_pc(
                board_id,
                document_id,
                num=pc_fetch_num,
                start_page=start_page,
                kind=kind,
            )
            async for comment in self.__deduped_comments(
                pc_comments,
                yielded_ids,
                remaining_state,
                pc_stats,
                skip_seen=True,
            ):
                yield comment
            if pc_stats["seen"] and (remaining_state["value"] == -1 or remaining_state["value"] <= 0):
                return
        except Exception:
            logger.debug(
                "pc comments failed, falling back to mobile: board=%s doc=%s",
                board_id,
                document_id,
                exc_info=True,
            )

        if prefer_mobile:
            return

        mobile_comments = self.__comments_from_mobile(
            board_id,
            document_id,
            num=-1,
            start_page=start_page,
        )
        mobile_stats = {"seen": False}
        async for comment in self.__deduped_comments(
            mobile_comments,
            yielded_ids,
            remaining_state,
            mobile_stats,
            skip_seen=True,
        ):
            yield comment
    # The Flask app is read-only today, so these write helpers are intentionally
    # not connected to routes. Keep them isolated until write support is planned.
    async def write_comment(self, board_id, document_id, contents="", dccon_id="", dccon_src="", parent_comment_id="", name="", password="", is_minor=False):
        url = "https://m.dcinside.com/board/{}/{}".format(board_id, document_id)
        async with self.session.get(url) as res:
            parsed = lxml.html.fromstring(await res.text())
        hide_robot = self.__required_attr(parsed, "//input[@class='hide-robot']", "name", "hide robot input")
        csrf_token = self.__required_attr(parsed, "//meta[@name='csrf-token']", "content", "csrf token")
        title = self.__required_text(parsed, "//span[@class='tit']", "document title")
        board_name = self.__required_text(parsed, "//a[@class='gall-tit-lnk']", "board name")
        con_key = await self.__access("com_submit", url, require_conkey=False, csrf_token=csrf_token)
        header = XML_HTTP_REQ_HEADERS.copy()
        header["Referer"] = url
        header["Host"] = "m.dcinside.com"
        header["Origin"] = "https://m.dcinside.com"
        header["X-CSRF-TOKEN"] = csrf_token
        cookies = {
            "m_dcinside_" + (board_id or ""): (board_id or ""),
            "m_dcinside_lately": quote((board_id or "") + "|" + (board_name or "") + ","),
            "_ga": "GA1.2.693521455.1588839880",
            }
        url = "https://m.dcinside.com/ajax/comment-write"
        payload = {
                "comment_memo": contents,
                "comment_nick": name,
                "comment_pw": password,
                "mode": "com_write",
                "comment_no": parent_comment_id,
                "id": board_id,
                "no": document_id,
                "best_chk": "",
                "subject": title,
                "board_id": "0",
                "reple_id":"",
                "cpage": "1",
                "con_key": con_key,
                hide_robot: "1",
                }
        if dccon_id: payload["detail_idx"] = dccon_id
        if dccon_src: payload["comment_memo"] = "<img src='{}' class='written_dccon' alt='1'>".format(dccon_src)
        #async with self.session.post(url, headers=header, data=payload, cookies=cookies) as res:
        async with self.session.post(url, headers=header, data=payload, cookies=cookies) as res:
            parsed = await res.text()
        try:
            parsed = json.loads(parsed)
        except Exception as e:
            raise Exception("Error while writing comment: " + unquote(str(parsed)))
        if "data" not in parsed:
            raise Exception("Error while writing comment: " + unquote(str(parsed)))
        return str(parsed["data"])
    async def modify_document(self, board_id, document_id, title="", contents="", name="", password="", is_minor=False):
        if not password:
            url = "https://m.dcinside.com/write/{}/modify/{}".format(board_id, document_id)
            async with self.session.get(url) as res:
                return await self.__write_or_modify_document(board_id, title, contents, name, password, intermediate=await res.text(), intermediate_referer=url, document_id=document_id, is_minor=is_minor)
        url = "https://m.dcinside.com/confirmpw/{}/{}?mode=modify".format(board_id, document_id)
        referer = url
        async with self.session.get(url) as res:
            parsed = lxml.html.fromstring(await res.text())
        token = self.__required_attr(parsed, "//input[@name='_token']", "value", "password token")
        csrf_token = self.__required_attr(parsed, "//meta[@name='csrf-token']", "content", "csrf token")
        con_key = await self.__access("Modifypw", url, require_conkey=False, csrf_token=csrf_token)
        payload = {
                "_token": token,
                "board_pw": password,
                "id": board_id,
                "no": document_id,
                "mode": "modify",
                "con_key": con_key,
                }
        header = XML_HTTP_REQ_HEADERS.copy()
        header["Referer"] = referer
        header["Host"] = "m.dcinside.com"
        header["Origin"] = "https://m.dcinside.com"
        header["X-CSRF-TOKEN"] = csrf_token
        url = "https://m.dcinside.com/ajax/pwcheck-board"
        async with self.session.post(url, headers=header, data=payload) as res:
            response_text = await res.text()
            self.__validate_password_check_response(response_text)
        payload = {
                "board_pw": password,
                "id": board_id,
                "no": document_id,
                "_token": csrf_token
                }
        header = POST_HEADERS.copy()
        header["Referer"] = referer
        url = "https://m.dcinside.com/write/{}/modify/{}".format(board_id, document_id)
        async with self.session.post(url, headers=header, data=payload) as res:
            return await self.__write_or_modify_document(board_id, title, contents, name, password, intermediate=await res.text(), intermediate_referer=url, document_id=document_id)
    async def remove_document(self, board_id, document_id, password="", is_minor=False):
        if not password:
            url = "https://m.dcinside.com/board/{}/{}".format(board_id, document_id)
            async with self.session.get(url) as res:
                parsed = lxml.html.fromstring(await res.text())
            csrf_token = self.__required_attr(parsed, "//meta[@name='csrf-token']", "content", "csrf token")
            header = XML_HTTP_REQ_HEADERS.copy()
            header["Referer"] = url
            header["X-CSRF-TOKEN"] = csrf_token
            con_key = await self.__access("board_Del", url, require_conkey=False, csrf_token=csrf_token)
            url = "https://m.dcinside.com/del/board"
            payload = { "id": board_id, "no": document_id, "con_key": con_key }
            async with self.session.post(url, headers=header, data=payload) as res:
                res = await res.text()
            if res.find("true") < 0:
                raise Exception("Error while removing: " + unquote(str(res)))
            return True
        url = "https://m.dcinside.com/confirmpw/{}/{}?mode=del".format(board_id, document_id)
        referer = url
        async with self.session.get(url) as res:
            parsed = lxml.html.fromstring(await res.text())
        token = self.__required_attr(parsed, "//input[@name='_token']", "value", "password token")
        csrf_token = self.__required_attr(parsed, "//meta[@name='csrf-token']", "content", "csrf token")
        board_name = self.__required_text(parsed, "//a[@class='gall-tit-lnk']", "board name")
        con_key = await self.__access("board_Del", url, require_conkey=False, csrf_token=csrf_token)
        payload = {
                "_token": token,
                "board_pw": password,
                "id": board_id,
                "no": document_id,
                "mode": "del",
                "con_key": con_key,
                }
        header = XML_HTTP_REQ_HEADERS.copy()
        header["Referer"] = url
        header["X-CSRF-TOKEN"] = csrf_token
        cookies = {
            "m_dcinside_" + (board_id or ""): (board_id or ""),
            "m_dcinside_lately": quote((board_id or "") + "|" + (board_name or "") + ","),
            "_ga": "GA1.2.693521455.1588839880",
            }
        url = "https://m.dcinside.com/del/board"
        async with self.session.post(url, headers=header, data=payload, cookies=cookies) as res:
            res = await res.text()
        if res.find("true") < 0:
            raise Exception("Error while removing: " + unquote(str(res)))
        return True
    async def write_document(self, board_id, title="", contents="", name="", password="", is_minor=False):
        return await self.__write_or_modify_document(board_id, title, contents, name, password, is_minor=is_minor)
    async def __write_or_modify_document(self, board_id, title="", contents="", name="", password="", intermediate=None, intermediate_referer=None, document_id=None, is_minor=False):
        if not intermediate:
            url = "https://m.dcinside.com/write/{}".format(board_id)
            async with self.session.get(url) as res:
                parsed = lxml.html.fromstring(await res.text())
        else:
            parsed = lxml.html.fromstring(intermediate)
            url = intermediate_referer
        first_url = url
        rand_code = self.__optional_attr(parsed, "//input[@name='code']", "value")
        user_id = self.__required_attr(parsed, "//input[@name='user_id']", "value", "user id") if not name else None
        mobile_key = self.__required_attr(parsed, "//input[@id='mobile_key']", "value", "mobile key")
        hide_robot = self.__required_attr(parsed, "//input[@class='hide-robot']", "name", "hide robot input")
        csrf_token = self.__required_attr(parsed, "//meta[@name='csrf-token']", "content", "csrf token")
        con_key = await self.__access("dc_check2", url, require_conkey=False, csrf_token=csrf_token)
        board_name = self.__required_text(parsed, "//a[@class='gall-tit-lnk']", "board name")
        header = XML_HTTP_REQ_HEADERS.copy()
        header["Referer"] = url
        header["X-CSRF-TOKEN"] = csrf_token
        url = "https://m.dcinside.com/ajax/w_filter"
        payload = {
                "subject": title,
                "memo": contents,
                "mode": "write",
                "id": board_id,
                }
        if rand_code:
            payload["code"] = rand_code
        async with self.session.post(url, headers=header, data=payload) as res:
            res = await res.text()
            res = json.loads(res)
        if not res["result"]:
            raise Exception("Erorr while write document: " + str(res))
        header = POST_HEADERS.copy()
        url = "https://mupload.dcinside.com/write_new.php"
        header["Host"] = "mupload.dcinside.com"
        header["Referer"] = first_url
        payload = {
                "subject": title,
                "memo": contents,
                hide_robot: "1",
                "GEY3JWF": hide_robot,
                "id": board_id,
                "contentOrder": "order_memo",
                "mode": "write",
                "Block_key": con_key,
                "bgm":"",
                "iData":"",
                "yData":"",
                "tmp":"",
                "imgSize": "850",
                "is_minor": "1" if is_minor else "",
                "mobile_key": mobile_key,
                "GEY3JWF": hide_robot,
            }
        if rand_code:
            payload["code"] = rand_code
        if name:
            payload["name"] = name
            payload["password"] = password
        else:
            payload["user_id"] = user_id
        if intermediate:
            payload["mode"] = "modify"
            payload["delcheck"] = ""
            payload["t_ch2"] = ""
            payload["no"] = document_id
        cookies = {
            "m_dcinside_" + (board_id or ""): (board_id or ""),
            "m_dcinside_lately": quote((board_id or "") + "|" + (board_name or "") + ","),
            "_ga": "GA1.2.693521455.1588839880",
            }
        async with self.session.post(url, headers=header, data=payload, cookies=cookies) as res:
            response_text = await res.text()
            if res.status >= 400:
                raise Exception("Error while writing document: " + unquote(str(response_text)))
            response_headers = dict(getattr(res, "headers", {}) or {})
            response_url = str(getattr(res, "url", "") or "")
        if document_id:
            self.__raise_if_write_response_failed(response_text, "modifying")
            modified_id = self.__extract_document_id_from_write_response(
                response_text,
                response_url=response_url,
                response_headers=response_headers,
            )
            if modified_id and str(modified_id) != str(document_id):
                raise Exception("Error while modifying document: upstream returned different document id")
            if modified_id or self.__is_successful_modify_response(response_text):
                return str(document_id)
            raise Exception("Error while modifying document: could not verify upstream response")
        self.__raise_if_write_response_failed(response_text, "writing")
        created_id = self.__extract_document_id_from_write_response(
            response_text,
            response_url=response_url,
            response_headers=response_headers,
        )
        if created_id:
            return created_id
        raise Exception("Error while writing document: could not parse upstream response")

    def __validate_password_check_response(self, response_text):
        text = response_text or ""
        if not text.strip():
            raise Exception("Error while modifying: maybe the password is incorrect")

        parsed_json = self.__loads_json_or_none(text)
        if parsed_json is not None:
            failure_message = self.__json_failure_message(parsed_json)
            if failure_message:
                raise Exception("Error while modifying: " + failure_message)
            if self.__json_success_value(parsed_json) is False:
                raise Exception("Error while modifying: maybe the password is incorrect")
            return

        if self.__contains_failure_signal(text, include_alert=True):
            raise Exception("Error while modifying: " + self.__clean_failure_text(text))

    def __loads_json_or_none(self, text):
        try:
            return json.loads(text)
        except Exception:
            return None

    def __json_success_value(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "ok", "success", "1", "y", "yes"}:
                return True
            if normalized in {"false", "fail", "failed", "error", "0", "n", "no"}:
                return False
            return None
        if isinstance(value, dict):
            for key in ("result", "success", "status", "code", "ok"):
                if key in value:
                    result = self.__json_success_value(value[key])
                    if result is not None:
                        return result
            data = value.get("data")
            if isinstance(data, (bool, int, float, str)):
                return self.__json_success_value(data)
        return None

    def __json_failure_message(self, value):
        if isinstance(value, dict):
            result = self.__json_success_value(value)
            message = " ".join(
                str(value.get(key) or "")
                for key in ("message", "msg", "error", "reason", "alert")
                if value.get(key)
            ).strip()
            if result is False:
                return message or "maybe the password is incorrect"
            if message and self.__contains_failure_signal(message, include_alert=False):
                return message
        elif isinstance(value, str):
            if self.__contains_failure_signal(value, include_alert=True):
                return value
        return None

    def __clean_failure_text(self, text):
        text = unquote(str(text or ""))
        try:
            parsed = lxml.html.fromstring(text)
            text = parsed.text_content()
        except Exception:
            pass
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:200] or "upstream rejected the request"

    def __contains_failure_signal(self, text, include_alert=False):
        raw = unquote(str(text or ""))
        lowered = raw.lower()
        alert_messages = self.__extract_alert_messages(raw)
        success_phrases = [
            "성공",
            "완료",
            "등록되었습니다",
            "등록 되었습니다",
            "수정되었습니다",
            "수정 되었습니다",
            "작성되었습니다",
            "작성 되었습니다",
        ]
        failure_phrases = [
            "비밀번호가 틀",
            "비밀번호를 확인",
            "비밀번호 확인",
            "비밀번호가 일치",
            "잘못된 비밀번호",
            "패스워드가 틀",
            "password is incorrect",
            "incorrect password",
            "wrong password",
            "invalid password",
            "오류",
            "에러",
            "실패",
            "fail",
            "failed",
            "error",
            "invalid",
            "forbidden",
            "denied",
            "권한",
            "차단",
            "자동등록방지",
            "캡차",
            "captcha",
            "금지어",
            "도배",
        ]

        for alert in alert_messages:
            normalized_alert = alert.strip().lower()
            if not normalized_alert:
                continue
            if any(phrase in normalized_alert for phrase in success_phrases):
                continue
            if include_alert:
                return True
            if any(phrase in normalized_alert for phrase in failure_phrases):
                return True

        return any(phrase in lowered for phrase in failure_phrases)

    def __extract_alert_messages(self, text):
        messages = []
        for quote_char, message in re.findall(r"alert\s*\(\s*(['\"])(.*?)\1\s*\)", text or "", flags=re.I | re.S):
            messages.append(message)
        return messages

    def __raise_if_write_response_failed(self, response_text, action):
        if self.__contains_failure_signal(response_text, include_alert=False):
            raise Exception("Error while {} document: {}".format(action, self.__clean_failure_text(response_text)))

        for alert in self.__extract_alert_messages(response_text or ""):
            if any(phrase in alert for phrase in ["성공", "완료", "등록", "수정"]):
                continue
            raise Exception("Error while {} document: {}".format(action, self.__clean_failure_text(alert)))

        parsed_json = self.__loads_json_or_none(response_text or "")
        if parsed_json is not None:
            failure_message = self.__json_failure_message(parsed_json)
            if failure_message:
                raise Exception("Error while {} document: {}".format(action, failure_message))

    def __is_successful_modify_response(self, response_text):
        parsed_json = self.__loads_json_or_none(response_text or "")
        if parsed_json is not None:
            success = self.__json_success_value(parsed_json)
            if success is True:
                return True

        text = unquote(str(response_text or ""))
        return any(
            phrase in text
            for phrase in [
                "수정되었습니다",
                "수정 되었습니다",
                "수정이 완료",
                "수정 완료",
                "modify_success",
            ]
        )

    def __extract_document_id_from_write_response(self, response_text, response_url=None, response_headers=None):
        response_headers = response_headers or {}
        for url in [
            response_url,
            response_headers.get("Location"),
            response_headers.get("location"),
        ]:
            document_id = self.__extract_document_id_from_url(url)
            if document_id:
                return document_id

        parsed_json = self.__loads_json_or_none(response_text or "")
        document_id = self.__extract_document_id_from_json(parsed_json)
        if document_id:
            return document_id

        redirect_url = self.__extract_top_level_redirect_url(response_text or "")
        document_id = self.__extract_document_id_from_url(redirect_url)
        if document_id:
            return document_id

        stripped = (response_text or "").strip()
        if re.match(r"^https?://", stripped):
            document_id = self.__extract_document_id_from_url(stripped)
            if document_id:
                return document_id
        return None

    def __extract_document_id_from_json(self, value):
        if isinstance(value, dict):
            for key in ("no", "document_id", "doc_id", "article_no", "article_id"):
                document_id = value.get(key)
                if re.fullmatch(r"\d+", str(document_id or "")):
                    return str(document_id)
            data = value.get("data")
            if isinstance(data, (dict, list)):
                return self.__extract_document_id_from_json(data)
            if re.fullmatch(r"\d+", str(data or "")):
                return str(data)
        if isinstance(value, list):
            for item in value:
                document_id = self.__extract_document_id_from_json(item)
                if document_id:
                    return document_id
        return None

    def __extract_document_id_from_url(self, url):
        if not url:
            return None
        parsed = urlparse(str(url))
        query = parse_qs(parsed.query)
        for key in ("no", "document_id", "doc_id"):
            values = query.get(key)
            if values and re.fullmatch(r"\d+", str(values[0])):
                return str(values[0])
        parts = [part for part in (parsed.path or "").split("/") if part]
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-3] in {"board", "mini"}:
            return parts[-1]
        return None

    async def __access(self, token_verify, target_url, require_conkey=True, csrf_token=None):
        if require_conkey:
            async with self.session.get(target_url) as res:
                parsed = lxml.html.fromstring(await res.text())
            con_key = self.__required_attr(parsed, "//input[@id='con_key']", "value", "con key")
            payload = { "token_verify": token_verify, "con_key": con_key }
        else:
            payload = { "token_verify": token_verify, }
        url = "https://m.dcinside.com/ajax/access"
        headers = XML_HTTP_REQ_HEADERS.copy()
        headers["Referer"] = target_url
        headers["X-CSRF-TOKEN"] = csrf_token
        async with self.session.post(url, headers=headers, data=payload) as res:
            return (await res.json())["Block_key"]
