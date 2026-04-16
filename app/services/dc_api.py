import asyncio
import json
import lxml.html
import os
from datetime import datetime, timedelta
import itertools
import aiohttp
import filetype
from urllib.parse import parse_qs, urlparse

def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


DOCS_PER_PAGE = 200
HTTP_TIMEOUT = env_int("MIRROR_HTTP_TIMEOUT", 20)
MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 7.0; SM-G892A Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/67.0.3396.87 Mobile Safari/537.36"
PC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

GET_HEADERS = {
    "User-Agent": MOBILE_USER_AGENT
     }
XML_HTTP_REQ_HEADERS = {
    "Accept": "*/*",
    "Connection": "keep-alive",
    "User-Agent": MOBILE_USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
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
    "__gat_mobile_search": 1,
    "list_count": DOCS_PER_PAGE,
    }

import re
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
def peek(iterable):
    try:
        first = next(iterable)
    except StopIteration:
        return None
    return first, itertools.chain((first,), iterable)

class DocumentIndex:
    __slots__ = ["id", "subject", "title", "board_id", "has_image", "author", "author_id", "time", "view_count", "comment_count", "voteup_count",
            "document", "comments", "isimage", "isrecommend", "isdcbest", "ishit", "is_mobile_source"]
    def __init__(self, id, board_id, title, has_image, author, author_id, time, view_count, comment_count, voteup_count, document, comments, subject, isimage, isrecommend, isdcbest, ishit, is_mobile_source=False):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.has_image = has_image
        self.author = author
        self.author_id = author_id
        self.time = time
        self.view_count = view_count
        self.comment_count = comment_count
        self.voteup_count = voteup_count
        self.document = document
        self.comments = comments
        self.subject = subject
        self.isimage = isimage
        self.isrecommend = isrecommend
        self.isdcbest = isdcbest
        self.ishit = ishit
        self.is_mobile_source = bool(is_mobile_source)
    def __str__(self):
        return f"{self.subject or ''}\t|{self.id}\t|{self.time.isoformat()}\t|{self.author}\t|{self.title}({self.comment_count}) +{self.voteup_count}"

class Document:
    __slots__ = ["id", "board_id", "title", "author", "author_id", "contents", "images", "html", "view_count", "voteup_count", "votedown_count", "logined_voteup_count", "time", "subject", "comments", "is_mobile_source", "related_posts", "embedded_comments", "embedded_comment_total"]
    def __init__(self, id, board_id, title, author, author_id, contents, images, html, view_count, voteup_count, votedown_count, logined_voteup_count, time, comments, subject=None, is_mobile_source=False, related_posts=None, embedded_comments=None, embedded_comment_total=0):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.author = author
        self.author_id = author_id
        self.contents = contents
        self.images = images
        self.html = html
        self.view_count = view_count
        self.voteup_count = voteup_count
        self.votedown_count = votedown_count
        self.logined_voteup_count = logined_voteup_count
        self.comments = comments
        self.time = time
        self.subject = subject
        self.is_mobile_source = bool(is_mobile_source)
        self.related_posts = list(related_posts or [])
        self.embedded_comments = list(embedded_comments or [])
        self.embedded_comment_total = embedded_comment_total
    def __str__(self):
        return f"{self.subject or ''}\t|{self.id}\t|{self.time.isoformat()}\t|{self.author}\t|{self.title}({self.comment_count}) +{self.voteup_count} -{self.votedown_count}\n{self.contents}"

class Comment:
    __slots__ = ["id", "parent_id", "author", "author_id", "contents", "dccon", "voice", "time", "is_reply"]
    def __init__(self, id, parent_id, author, author_id, contents, dccon, voice, time, is_reply=False):
        self.id = id
        self.parent_id = parent_id
        self.author = author
        self.author_id = author_id
        self.contents = contents
        self.dccon = dccon
        self.voice = voice
        self.time = time
        self.is_reply = bool(is_reply)
    def __str__(self):
        return f"ㄴ {self.author}: {self.contents or ''}{self.dccon or ''}{self.voice or ''} | {self.time}"

class Image:
    __slots__ = ["src", "document_id", "board_id", "session"]
    def __init__(self, src, document_id, board_id, session):
        self.src = src
        self.document_id = document_id
        self.board_id = board_id
        self.session = session
    async def load(self):
        headers = GET_HEADERS.copy()
        headers["Referer"] = "https://m.dcinside.com/board/{}/{}".format(self.board_id, self.document_id)
        async with self.session.get(self.src, cookies=GALLERY_POSTS_COOKIES, headers=headers) as res:
            return await res.read()
    async def download(self, path):
        headers = GET_HEADERS.copy()
        headers["Referer"] = "https://m.dcinside.com/board/{}/{}".format(self.board_id, self.document_id)
        async with self.session.get(self.src, cookies=GALLERY_POSTS_COOKIES, headers=headers) as res:
            bytes = await res.read()
            ext = filetype.guess(bytes).extension
            with open(path + '.' + ext, 'wb') as f:
                f.write(bytes)



class API:
    def __init__(self):
        timeout = aiohttp.ClientTimeout(
            total=HTTP_TIMEOUT,
            connect=min(10, HTTP_TIMEOUT),
            sock_read=HTTP_TIMEOUT,
        )
        self.session = aiohttp.ClientSession(
            headers=GET_HEADERS,
            cookies={"_ga": "GA1.2.693521455.1588839880"},
            timeout=timeout,
        )
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

        if self.__is_rate_limited_response(status, text[:1000]):
            raise RuntimeError(f"rate limited: {status}")

        return status, response_headers, text

    def __build_list_urls(self, board_id, page, recommend=False, kind=None):
        kind = (kind or "").lower()
        urls = []
        recommend_suffix = "&recommend=1" if recommend else ""

        if kind == "mini":
            urls.append("https://m.dcinside.com/mini/{}?page={}{}".format(board_id, page, recommend_suffix))
        elif recommend:
            urls.append("https://m.dcinside.com/board/{}?recommend=1&page={}".format(board_id, page))
        else:
            urls.append("https://m.dcinside.com/board/{}?page={}".format(board_id, page))

        kind_urls = {
            "normal": "https://gall.dcinside.com/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "minor": "https://gall.dcinside.com/mgallery/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "mini": "https://gall.dcinside.com/mini/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "person": "https://gall.dcinside.com/person/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
        }
        if kind in kind_urls:
            urls.append(kind_urls[kind])

        urls.extend([
            "https://gall.dcinside.com/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "https://gall.dcinside.com/mgallery/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "https://gall.dcinside.com/mini/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
            "https://gall.dcinside.com/person/board/lists/?id={}&page={}{}".format(board_id, page, recommend_suffix),
        ])
        return self.__dedupe_urls(urls)

    def __build_view_urls(self, board_id, document_id, kind=None):
        kind = (kind or "").lower()
        urls = []

        if kind == "mini":
            urls.append("https://m.dcinside.com/mini/{}/{}".format(board_id, document_id))
        else:
            urls.append("https://m.dcinside.com/board/{}/{}".format(board_id, document_id))

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

    def __is_usable_board_page(self, parsed, text, url):
        if "등록된 게시물이 없습니다." in text:
            return True
        mobile_rows = parsed.xpath(
            "//ul[contains(@class, 'gall-detail-lst')]/li["
            "not(contains(concat(' ', normalize-space(@class), ' '), ' ad ')) and "
            "(.//a[contains(@class, 'lt') and (contains(@href, '/board/') or contains(@href, '/mini/'))])"
            "]"
        )
        pc_rows = parsed.xpath("//tr[contains(@class, 'ub-content') and contains(@class, 'us-post')]")
        return bool(mobile_rows or pc_rows)

    def __is_usable_document_page(self, parsed, text, url):
        doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview-tit-box')]")
        if not doc_head_containers:
            doc_head_containers = parsed.xpath("//div[@class='gall-tit-box']")
        if not doc_head_containers:
            doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview_head')]")

        doc_content_container = parsed.xpath("//div[@class='thum-txtin']")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'writing_view_box')]")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'thum-txt-area')]")
        return bool(doc_head_containers and doc_content_container)

    def __parse_mobile_list_item(self, row, board_id, kind=None, is_mobile_source=True):
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

        row_class = " {} ".format(row.get("class", ""))
        if " ad " in row_class:
            return None

        link_nodes = row.xpath(
            ".//a[contains(@class, 'lt') and "
            "(contains(@href, '/board/') or contains(@href, '/mini/'))]"
        )
        if not link_nodes:
            link_nodes = row.xpath(
                ".//a[(contains(@href, '/board/') or contains(@href, '/mini/')) "
                "and not(contains(@href, '#comment_box'))]"
            )
        if not link_nodes:
            return None

        link = link_nodes[0]
        href = link.get("href", "")
        id_match = re.search(r"/(\d+)(?:[?#]|$)", href)
        if not id_match:
            return None
        document_id = id_match.group(1)

        subject = None
        subject_el = link.xpath(".//span[contains(@class, 'subjectin')]")
        if subject_el:
            title = " ".join(subject_el[0].text_content().split())
            subject_tag = subject_el[0].xpath(".//b")
            if subject_tag:
                subject = subject_tag[0].text_content().strip() or None
        else:
            title = " ".join(link.text_content().split())
        if not title:
            return None

        ginfo = link.xpath(".//ul[contains(@class, 'ginfo')]/li")
        author = "익명"
        post_time = self.__parse_time("")
        view_count = 0
        voteup_count = 0
        if len(ginfo) >= 1:
            author = " ".join(ginfo[0].text_content().split()) or "익명"
        if len(ginfo) >= 2:
            post_time = self.__parse_time(" ".join(ginfo[1].text_content().split()))
        if len(ginfo) >= 3:
            view_count = to_int(" ".join(ginfo[2].text_content().split()), 0)
        if len(ginfo) >= 4:
            voteup_count = to_int(" ".join(ginfo[3].text_content().split()), 0)

        comment_count = 0
        comment_nodes = row.xpath(".//a[contains(@class, 'rt')]//*[contains(@class, 'ct')]")
        if comment_nodes:
            comment_count = to_int(" ".join(comment_nodes[0].text_content().split()), 0)

        author_id = None
        block_info = row.xpath(".//*[contains(@class, 'blockInfo')]/@data-info")
        if block_info:
            author_id = (block_info[0] or "").strip() or None
        if not author_id:
            gallog_hrefs = row.xpath(".//a[contains(@href, 'gallog.dcinside.com/')]/@href")
            if gallog_hrefs:
                match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", gallog_hrefs[0])
                if match:
                    author_id = match.group(1)

        icon_text = " ".join(link.xpath(".//span[contains(@class,'sp-lst')]/text()"))
        icon_class = " ".join(link.xpath(".//span[contains(@class,'sp-lst')]/@class"))
        flags = "{} {}".format(icon_text, icon_class)
        isimage = ("이미지" in flags) or ("img" in flags)
        isrecommend = "reco" in flags
        isdcbest = ("best" in flags) or (board_id == "dcbest")
        ishit = "hit" in flags

        return DocumentIndex(
            id=document_id,
            board_id=board_id,
            title=title,
            has_image=isimage,
            author=author,
            author_id=author_id,
            view_count=view_count,
            voteup_count=voteup_count,
            comment_count=comment_count,
            document=lambda b=board_id, d=document_id, k=kind: self.document(b, d, kind=k),
            comments=lambda b=board_id, d=document_id, k=kind: self.comments(b, d, kind=k),
            time=post_time,
            subject=subject,
            isimage=isimage,
            isrecommend=isrecommend,
            isdcbest=isdcbest,
            ishit=ishit,
            is_mobile_source=is_mobile_source,
        )

    def __parse_embedded_mobile_posts(self, parsed, board_id, current_document_id, kind=None):
        posts = []
        seen_ids = {str(current_document_id)}
        rows = parsed.xpath("//ul[contains(@class, 'gall-detail-lst')]/li")
        for row in rows:
            item = self.__parse_mobile_list_item(row, board_id, kind=kind, is_mobile_source=True)
            if item is None or item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            posts.append(item)
        return posts

    def __parse_mobile_comment_li(self, li):
        li_classes = set((li.get("class") or "").split())
        nick_node = li[0] if len(li) > 0 else None
        content_node = li[1] if len(li) > 1 else li
        time_node = li[2] if len(li) > 2 else None

        author_id = None
        if nick_node is not None:
            block_id_nodes = nick_node.xpath(".//*[contains(@class, 'blockCommentId')]")
            if block_id_nodes:
                author_id = block_id_nodes[0].get("data-info", None)
            if not author_id:
                block_ip_nodes = nick_node.xpath(".//*[contains(@class, 'blockCommentIp')]")
                if block_ip_nodes:
                    author_id = "".join(block_ip_nodes[0].itertext()).strip()

        author = "익명"
        if nick_node is not None:
            nick_buttons = nick_node.xpath(".//*[contains(@class, 'nick')]")
            if nick_buttons:
                author = " ".join(nick_buttons[0].itertext()).strip() or "익명"
            else:
                author = " ".join(nick_node.itertext()).strip() or "익명"

        dccon_images = content_node.xpath(
            ".//img[contains(@src, 'dccon') or contains(@data-original, 'dccon') "
            "or contains(@data-gif, 'dccon') or contains(@src, 'dicad')]"
        )
        dccon = None
        if dccon_images:
            dccon = (
                dccon_images[0].get("data-gif")
                or dccon_images[0].get("data-original")
                or dccon_images[0].get("src")
            )

        voice = None
        voice_nodes = content_node.xpath(".//iframe/@src")
        if voice_nodes:
            voice = voice_nodes[0]

        return Comment(
            id=li.get("no"),
            parent_id=li.get("m_no"),
            author=author,
            author_id=author_id,
            contents="\n".join(i.strip() for i in content_node.itertext() if i.strip()),
            dccon=dccon,
            voice=voice,
            time=self.__parse_time(time_node.text_content() if time_node is not None else ""),
            is_reply="comment-add" in li_classes,
        )

    def __mobile_comment_rows(self, parsed):
        rows = parsed.xpath(
            ".//ul[contains(@class, 'all-comment-lst')]/li["
            "contains(concat(' ', normalize-space(@class), ' '), ' comment ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' comment-add ')"
            "]"
        )
        if rows:
            return rows
        if len(parsed) >= 2:
            return parsed[1].xpath(
                ".//li[contains(concat(' ', normalize-space(@class), ' '), ' comment ') "
                "or contains(concat(' ', normalize-space(@class), ' '), ' comment-add ')]"
            )
        return []

    def __parse_embedded_mobile_comments(self, parsed):
        comments = []
        seen_ids = set()
        for li in self.__mobile_comment_rows(parsed):
            comment = self.__parse_mobile_comment_li(li)
            comment_id = str(comment.id or "").strip()
            if comment_id and comment_id in seen_ids:
                continue
            if comment_id:
                seen_ids.add(comment_id)
            comments.append(comment)

        total = 0
        total_nodes = parsed.xpath("string((//input[@id='reple_totalCnt'])[1]/@value)")
        if total_nodes:
            try:
                total = int(total_nodes)
            except ValueError:
                total = 0
        if total <= 0:
            title_text = " ".join(
                parsed.xpath("//div[contains(@class, 'all-comment-tit')]//*[contains(@class, 'ct')]/text()")
            )
            match = re.search(r"\d+", title_text)
            if match:
                total = int(match.group(0))
        return comments, total

    def __extract_top_level_redirect_url(self, text):
        if not text:
            return None

        # Some board pages include login/menu actions with "location.href" deep in the
        # document. Only treat a redirect as real when it appears in the initial payload
        # as a top-level redirect script or meta refresh.
        try:
            parsed = lxml.html.document_fromstring(text)
        except (lxml.etree.ParserError, ValueError):
            return None

        for meta in parsed.xpath("/html/head/meta | /html/body/meta"):
            redirect_url = self.__extract_meta_refresh_url(meta)
            if redirect_url:
                return redirect_url

        for script in parsed.xpath("/html/head/script | /html/body/script"):
            redirect_url = self.__extract_script_redirect_url(script.text_content() or "")
            if redirect_url:
                return redirect_url
        return None

    def __extract_meta_refresh_url(self, meta):
        http_equiv = (meta.get("http-equiv") or "").strip().lower()
        if http_equiv != "refresh":
            return None
        content = (meta.get("content") or "").strip()
        if not content:
            return None
        match = re.search(r"url\s*=\s*([^;]+)", content, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip().strip("'\"")

    def __extract_script_redirect_url(self, script_text):
        if not script_text:
            return None

        location_prefix = r"(?:(?:window|top|document)\.)*location"
        patterns = [
            rf"{location_prefix}(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
            rf"{location_prefix}\.(?:replace|assign)\(\s*['\"]([^'\"]+)['\"]\s*\)",
        ]
        for pattern in patterns:
            match = re.search(pattern, script_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def __upsert_gallery(self, gallerys, board_name, board_id):
        if not board_name or not board_id:
            return
        current = gallerys.get(board_name)
        if current is None:
            gallerys[board_name] = board_id
            return
        if current == board_id:
            return
        # Avoid dropping one of duplicated names by attaching board_id.
        gallerys[f"{board_name} ({board_id})"] = board_id

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
    async def board(self, board_id, num=-1, start_page=1, recommend=False, document_id_upper_limit=None, document_id_lower_limit=None, is_minor=False, kind=None, max_scan_pages=None):
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

        page = start_page
        scanned_pages = 0
        while num:
            if max_scan_pages is not None and scanned_pages >= max_scan_pages:
                break
            parsed, text, used_url = await self.__fetch_parsed_from_urls(
                self.__build_list_urls(board_id, page, recommend=recommend, kind=kind),
                validator=self.__is_usable_board_page,
            )
            scanned_pages += 1
            if parsed is None:
                break
            if "등록된 게시물이 없습니다." in text:
                break
            yielded_in_page = 0
            is_mobile_source = self.__is_mobile_request(used_url)

            doc_headers = [i[0] for i in parsed.xpath("//ul[contains(@class, 'gall-detail-lst')]/li") if not i.get("class", "").startswith("ad")]
            if doc_headers:
                for doc in doc_headers:
                    href = ""
                    title = ""
                    subject = None
                    author = "익명"
                    author_id = None
                    time = self.__parse_time("")
                    view_count = 0
                    voteup_count = 0
                    comment_count = 0
                    isimage = False
                    isdcbest = False
                    isrecommend = False
                    ishit = False
                    classname = ""

                    try:
                        # Legacy mobile structure
                        href = doc[0].get("href", "")
                        if href:
                            document_id = href.split("/")[-1].split("?")[0]
                        else:
                            document_id = ""
                        if len(doc[0][1]) == 5:
                            subject = doc[0][1][0].text
                            author = " ".join(doc[0][1][1].text_content().split()) if len(doc[0][1]) > 1 else "익명"
                            time = self.__parse_time(doc[0][1][2].text or "")
                            view_count = to_int(doc[0][1][3].text.split()[-1] if doc[0][1][3].text else 0, 0)
                            voteup_count = to_int(doc[0][1][4][0].text.split()[-1] if doc[0][1][4][0].text else 0, 0)
                        else:
                            subject = None
                            author = " ".join(doc[0][1][0].text_content().split()) if len(doc[0][1]) > 0 else "익명"
                            time = self.__parse_time(doc[0][1][1].text or "")
                            view_count = to_int(doc[0][1][2].text.split()[-1] if doc[0][1][2].text else 0, 0)
                            voteup_count = to_int(doc[0][1][3].text_content().split()[-1], 0)
                        classname = doc[0][0][0].get("class", "")
                        title = (doc[0][0][1].text or "").strip()
                        comment_count = to_int(doc[1][0].text if len(doc) > 1 and len(doc[1]) else 0, 0)
                        author_id_nodes = doc.xpath(".//span[contains(@class, 'blockInfo')]/@data-info")
                        if author_id_nodes:
                            author_id = (author_id_nodes[0] or "").strip() or None
                        if not author_id:
                            gallog_hrefs = doc.xpath(".//a[contains(@href, 'gallog.dcinside.com/')]/@href")
                            if gallog_hrefs:
                                match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", gallog_hrefs[0])
                                if match:
                                    author_id = match.group(1)
                        if not author_id:
                            onclick_nodes = doc.xpath(".//*[@onclick]/@onclick")
                            for onclick_text in onclick_nodes:
                                match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", onclick_text)
                                if match:
                                    author_id = match.group(1)
                                    break
                        if "sp-lst-img" in classname:
                            isimage = True
                        elif "sp-lst-recoimg" in classname:
                            isimage = True
                            isrecommend = True
                        elif "sp-lst-recotxt" in classname:
                            isrecommend = True
                        elif "sp-lst-best" in classname:
                            isdcbest = True
                        elif "sp-lst-hit" in classname:
                            ishit = True
                    except Exception:
                        # Best board uses a different mobile list markup.
                        lt = doc.xpath(".//a[contains(@class, 'lt')]")
                        if not lt:
                            continue
                        link = lt[0]
                        href = link.get("href", "")
                        id_match = re.search(r"/(\d+)(?:\\?|$)", href)
                        if not id_match:
                            continue
                        document_id = id_match.group(1)

                        subject_el = link.xpath(".//span[contains(@class, 'subjectin')]")
                        if subject_el:
                            title = " ".join(subject_el[0].text_content().split())
                            subj_tag = subject_el[0].xpath(".//b")
                            if subj_tag:
                                subject = subj_tag[0].text_content().strip()
                        if not title:
                            title = " ".join(link.text_content().split())

                        ginfo = link.xpath(".//ul[contains(@class, 'ginfo')]/li")
                        if len(ginfo) >= 1:
                            author = " ".join(ginfo[0].text_content().split()) or "익명"
                        if len(ginfo) >= 2:
                            time = self.__parse_time(" ".join(ginfo[1].text_content().split()))
                        if len(ginfo) >= 3:
                            view_count = to_int(" ".join(ginfo[2].text_content().split()), 0)
                        if len(ginfo) >= 4:
                            voteup_count = to_int(" ".join(ginfo[3].text_content().split()), 0)

                        rt = doc.xpath(".//a[contains(@class, 'rt')]")
                        if rt:
                            comment_count = to_int(" ".join(rt[0].text_content().split()), 0)

                        icon_text = " ".join(link.xpath(".//span[contains(@class,'sp-lst')]/text()"))
                        icon_class = " ".join(link.xpath(".//span[contains(@class,'sp-lst')]/@class"))
                        flags = "{} {}".format(icon_text, icon_class)
                        isimage = ("이미지" in flags) or ("img" in flags)
                        isrecommend = "reco" in flags
                        isdcbest = ("best" in flags) or (board_id == "dcbest")
                        ishit = "hit" in flags

                    if not href:
                        continue
                    if not document_id or not document_id.isdigit():
                        continue
                    if document_id_upper_limit and int(document_id_upper_limit) <= int(document_id):
                        continue
                    if document_id_lower_limit and int(document_id_lower_limit) >= int(document_id):
                        return

                    indexdata = DocumentIndex(
                        id=document_id,
                        board_id=board_id,
                        title=title,
                        has_image=isimage or classname.endswith("img"),
                        author=author,
                        author_id=author_id,
                        view_count=view_count,
                        voteup_count=voteup_count,
                        comment_count=comment_count,
                        document=lambda b=board_id, d=document_id, k=kind: self.document(b, d, kind=k),
                        comments=lambda b=board_id, d=document_id, k=kind: self.comments(b, d, kind=k),
                        time=time,
                        subject=subject,
                        isimage=isimage,
                        isrecommend=isrecommend,
                        isdcbest=isdcbest,
                        ishit=ishit,
                        is_mobile_source=is_mobile_source,
                    )
                    yield indexdata
                    yielded_in_page += 1
                    num -= 1
                    if num == 0:
                        break
            else:
                rows = parsed.xpath("//tr[contains(@class, 'ub-content') and contains(@class, 'us-post')]")
                for row in rows:
                    data_no = row.get("data-no", "")
                    href_els = row.xpath(".//td[contains(@class, 'gall_tit')]//a[contains(@href, 'view')]")
                    if not href_els:
                        continue
                    href = href_els[0].get("href", "")
                    no_match = re.search(r"[?&]no=(\d+)", href)
                    document_id = no_match.group(1) if no_match else data_no
                    if not document_id or not document_id.isdigit():
                        continue
                    if document_id_upper_limit and int(document_id_upper_limit) <= int(document_id):
                        continue
                    if document_id_lower_limit and int(document_id_lower_limit) >= int(document_id):
                        return

                    title = " ".join(href_els[0].text_content().split())
                    author_el = row.xpath(".//td[contains(@class, 'gall_writer')]")
                    author = "익명"
                    author_id = None
                    if author_el:
                        author = (author_el[0].get("data-nick") or "").strip()
                        if not author:
                            author = " ".join(author_el[0].text_content().split()) or "익명"
                        author_id = (author_el[0].get("data-uid") or "").strip()
                        if not author_id:
                            author_id = (author_el[0].get("data-ip") or "").strip()
                        author_id = author_id or None

                    date_el = row.xpath(".//td[contains(@class, 'gall_date')]")
                    time_text = ""
                    if date_el:
                        time_text = (date_el[0].get("title") or date_el[0].text_content() or "").strip()
                    view_count = to_int("".join(row.xpath(".//td[contains(@class, 'gall_count')]/text()") or []), 0)
                    voteup_count = to_int("".join(row.xpath(".//td[contains(@class, 'gall_recommend')]/text()") or []), 0)
                    comment_count = to_int("".join(row.xpath(".//a[contains(@class, 'reply_numbox')]//span[contains(@class, 'reply_num')]/text()") or []), 0)

                    flags = " ".join([
                        row.get("data-type", ""),
                        " ".join(row.xpath(".//td[contains(@class, 'gall_tit')]//em/@class")),
                    ])
                    isimage = ("pic" in flags) or ("img" in flags)
                    isrecommend = "recom" in flags
                    isdcbest = "best" in flags
                    ishit = ("issue" in flags) or ("hit" in flags)

                    indexdata = DocumentIndex(
                        id=document_id,
                        board_id=board_id,
                        title=title,
                        has_image=isimage,
                        author=author,
                        author_id=author_id,
                        view_count=view_count,
                        voteup_count=voteup_count,
                        comment_count=comment_count,
                        document=lambda b=board_id, d=document_id, k=kind: self.document(b, d, kind=k),
                        comments=lambda b=board_id, d=document_id, k=kind: self.comments(b, d, kind=k),
                        time=self.__parse_time(time_text),
                        subject=None,
                        isimage=isimage,
                        isrecommend=isrecommend,
                        isdcbest=isdcbest,
                        ishit=ishit,
                        is_mobile_source=is_mobile_source,
                    )
                    yield indexdata
                    yielded_in_page += 1
                    num -= 1
                    if num == 0:
                        break

            if yielded_in_page == 0:
                break
            page += 1

    async def document(self, board_id, document_id, kind=None):
        parsed, text, used_url = await self.__fetch_parsed_from_urls(
            self.__build_view_urls(board_id, document_id, kind=kind),
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

            def first_text(xpath_expr):
                nodes = parsed.xpath(xpath_expr)
                if not nodes:
                    return None
                node = nodes[0]
                if hasattr(node, "text_content"):
                    return node.text_content().strip()
                return (node.text or "").strip()

            # Improved title parsing
            subject = None
            title_subject_el = doc_head_container.xpath(".//span[contains(@class, 'title_subject')]")
            title_headtext_el = doc_head_container.xpath(".//span[contains(@class, 'title_headtext')]")
            title_el = doc_head_container.xpath(".//span[contains(@class, 'tit')]")
            if title_subject_el:
                title = title_subject_el[0].text_content().strip()
                if title_headtext_el:
                    subject = title_headtext_el[0].text_content().strip().strip("[]") or None
            elif title_el:
                title = title_el[0].text_content().strip()
            else:
                title = " ".join(doc_head_container.text_content().split()) if doc_head_container.text_content() else "제목 없음"
                
            author = "익명"
            author_id = None

            # Mobile view often exposes writer in ginfo2 first item:
            # <li><a href="/gallog/{id}">닉네임</a></li> or plain "ㅇㅇ(1.2)" text.
            ginfo_author = doc_head_container.xpath(".//ul[contains(@class, 'ginfo2')]/li[1]")
            if ginfo_author:
                author = ginfo_author[0].text_content().strip() or "익명"
                gallog_href = ginfo_author[0].xpath("string((.//a[contains(@href, '/gallog/')])[1]/@href)")
                if gallog_href:
                    match = re.search(r"/gallog/([^/?'\"#]+)", gallog_href)
                    if match:
                        author_id = match.group(1)

            if author == "익명":
                author_el = doc_head_container.xpath(".//span[@class='nickname'] | .//span[contains(@class, 'nickname')]")
                if author_el:
                    author = author_el[0].text_content().strip() or "익명"

            author_id_el = doc_head_container.xpath(".//span[@class='ip']")
            if author_id is None and author_id_el:
                author_id = author_id_el[0].text_content().strip() or None
            if not author_id:
                gallog_hrefs = doc_head_container.xpath(".//a[contains(@href, 'gallog.dcinside.com/')]/@href")
                if gallog_hrefs:
                    match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", gallog_hrefs[0])
                    if match:
                        author_id = match.group(1)
            if not author_id:
                gallog_hrefs = doc_head_container.xpath(".//a[contains(@href, '/gallog/')]/@href")
                if gallog_hrefs:
                    match = re.search(r"/gallog/([^/?'\"#]+)", gallog_hrefs[0])
                    if match:
                        author_id = match.group(1)
            if not author_id:
                onclick_nodes = doc_head_container.xpath(".//*[@onclick]/@onclick")
                for onclick_text in onclick_nodes:
                    match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", onclick_text)
                    if match:
                        author_id = match.group(1)
                        break
            
            meta_text = " ".join(doc_head_container.text_content().split())
            time_str = None
            # Mobile markup
            time_el = doc_head_container.xpath(".//span[@class='date'] | .//span[contains(@class, 'time')]")
            if time_el:
                time_str = time_el[0].text_content().strip()
            # PC markup
            if not time_str:
                time_el = doc_head_container.xpath(".//span[@class='gall_date'] | .//span[contains(@class, 'gall_date')]")
                if time_el:
                    time_str = time_el[0].text_content().strip()
            # Final fallback: parse date-like text in header block.
            if not time_str:
                m = re.search(r"\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}(?::\d{2})?", meta_text)
                if m:
                    time_str = m.group(0)
            if not time_str:
                time_str = datetime.now().strftime("%Y.%m.%d %H:%M:%S")

            # Some boards use different markup and omit legacy ids/classes.
            view_count = to_int(first_text("//ul[@class='ginfo2']/li[contains(., '조회')]"), default=-1)
            voteup_count = to_int(first_text("//span[@id='recomm_btn']"), default=-1)
            votedown_count = to_int(first_text("//span[@id='nonrecomm_btn']"), default=-1)
            logined_voteup_count = to_int(first_text("//span[@id='recomm_btn_member']"), default=0)

            if view_count < 0:
                m = re.search(r"조회\s*([0-9,]+)", meta_text)
                view_count = to_int(m.group(1), 0) if m else 0
            if voteup_count < 0:
                # Newer pages expose up count as recommend_view_up_{document_id}
                voteup_count = to_int(first_text(f"//*[@id='recommend_view_up_{document_id}']"), default=-1)
            if voteup_count < 0:
                m = re.search(r"추천\s*([0-9,]+)", meta_text)
                voteup_count = to_int(m.group(1), 0) if m else 0
            if votedown_count < 0:
                m = re.search(r"(?:비추|비추천)\s*([0-9,]+)", meta_text)
                votedown_count = to_int(m.group(1), 0) if m else 0
            if logined_voteup_count < 0:
                logined_voteup_count = 0
            
            doc_content = doc_content_container[0]
            related_posts = []
            embedded_comments = []
            embedded_comment_total = 0
            if is_mobile_source:
                related_posts = self.__parse_embedded_mobile_posts(parsed, board_id, document_id, kind=kind)
                embedded_comments, embedded_comment_total = self.__parse_embedded_mobile_comments(parsed)

            for adv in doc_content.xpath("div[@class='adv-groupin']"):
                adv.getparent().remove(adv)
            for adv in doc_content.xpath(".//img"):
                if adv.get("src", "").startswith("https://nstatic") and not adv.get("data-original"):
                    adv.getparent().remove(adv)

            def pick_image_src(el):
                # Some posts (e.g. gif/mp4 lazy media) keep the real URL in data-gif.
                for key in ("data-gif", "data-original", "src"):
                    src = el.get(key)
                    if src:
                        return src
                return None

            return Document(
                    id = document_id,
                    board_id = board_id,
                    title= title,
                    author= author,
                    author_id =author_id,
                    contents= '\n'.join(i.strip() for i in doc_content.itertext() if i.strip() and not i.strip().startswith("이미지 광고")),
                    images= [Image(
                        src=src,
                        board_id=board_id, 
                        document_id=document_id, 
                        session=self.session)
                        for i in doc_content.xpath(".//img")
                        for src in [pick_image_src(i)]
                            if src
                            and not src.startswith("https://nstatic")
                            and not src.startswith("https://img.iacstatic.co.kr")],
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
        ''' !TODO: use an alternative(PC) protocol to fetch document
        else:
            url = "https://gall.dcinside.com/{}?no={}".format(board_id, document_id)
            res = sess.get(url, timeout=TIMEOUT, headers=ALTERNATIVE_GET_HEADERS)
            parsed = lxml.html.fromstring(res.text)
            doc_content = parsed.xpath("//div[@class='thum-txtin']")[0]
            return '\n'.join(i.strip() for i in doc_content.itertext() if i.strip() and not i.strip().startswith("이미지 광고")), [i.get("src") for i in doc_content.xpath("//img") if not i.get("src","").startswith("https://nstatic")], comments(board_id, document_id, sess=sess)
        '''
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

    def __parse_pc_comment(self, raw):
        memo = raw.get("memo") or ""
        contents = memo
        dccon = None
        voice = raw.get("voice") or None

        if memo and "<" in memo:
            try:
                fragment = lxml.html.fragment_fromstring(memo, create_parent="div")
                dccon_candidates = fragment.xpath(
                    ".//img[contains(@src, 'dccon') or contains(@src, 'dicad') or contains(@class, 'written_dccon')]"
                )
                if dccon_candidates:
                    dccon = (
                        dccon_candidates[0].get("data-gif")
                        or dccon_candidates[0].get("data-original")
                        or dccon_candidates[0].get("src")
                    )
                if not voice:
                    voice_nodes = fragment.xpath(".//iframe/@src")
                    if voice_nodes:
                        voice = voice_nodes[0]
                contents = "\n".join(i.strip() for i in fragment.itertext() if i.strip())
            except Exception:
                contents = re.sub(r"<[^>]+>", "", memo).strip()

        author_id = (raw.get("user_id") or "").strip() or (raw.get("ip") or "").strip() or None
        parent_id = str(raw.get("c_no") or raw.get("parent") or "").strip() or None

        return Comment(
            id=str(raw.get("no") or "").strip() or None,
            parent_id=parent_id,
            author=(raw.get("name") or "").strip() or "익명",
            author_id=author_id,
            contents=contents,
            dccon=dccon,
            voice=voice,
            time=self.__parse_time((raw.get("reg_date") or "").strip()),
            is_reply=str(raw.get("depth") or "0").strip() != "0",
        )

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
                if fail_fast:
                    raise RuntimeError("mobile comment pagination missing")
                break
    async def comments(self, board_id, document_id, num=-1, start_page=1, kind=None, prefer_mobile=True):
        if num == 0:
            return
        yielded_from_pc = False
        yielded_ids = set()
        remaining = num
        if prefer_mobile:
            yielded_from_mobile = False
            try:
                async for comment in self.__comments_from_mobile(
                    board_id,
                    document_id,
                    num=num,
                    start_page=start_page,
                    fail_fast=True,
                ):
                    yielded_from_mobile = True
                    comment_id = str(getattr(comment, "id", None) or "").strip()
                    if comment_id:
                        yielded_ids.add(comment_id)
                    if remaining != -1:
                        remaining -= 1
                    yield comment
                    if remaining == 0:
                        return
                if yielded_from_mobile:
                    return
            except Exception:
                pass
        try:
            if remaining == -1:
                pc_fetch_num = -1
            elif yielded_ids:
                pc_fetch_num = remaining + len(yielded_ids)
            else:
                pc_fetch_num = remaining
            async for comment in self.__comments_from_pc(
                board_id,
                document_id,
                num=pc_fetch_num,
                start_page=start_page,
                kind=kind,
            ):
                yielded_from_pc = True
                comment_id = str(getattr(comment, "id", None) or "").strip()
                if comment_id and comment_id in yielded_ids:
                    continue
                if comment_id:
                    yielded_ids.add(comment_id)
                if remaining != -1:
                    remaining -= 1
                yield comment
                if remaining == 0:
                    return
            if yielded_from_pc and remaining != -1:
                if remaining <= 0:
                    return
            elif yielded_from_pc and remaining == -1:
                return
        except Exception:
            pass

        if prefer_mobile:
            return

        async for comment in self.__comments_from_mobile(
            board_id,
            document_id,
            num=-1,
            start_page=start_page,
        ):
            comment_id = str(getattr(comment, "id", None) or "").strip()
            if comment_id and comment_id in yielded_ids:
                continue
            yield comment
            if remaining != -1:
                remaining -= 1
                if remaining == 0:
                    return
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
            res = await res.text()
            if not res.strip():
                raise Exception("Error while modifying: maybe the password is incorrect")
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
        if document_id:
            return str(document_id)
        created_id = self.__extract_document_id_from_write_response(response_text)
        if created_id:
            return created_id
        raise Exception("Error while writing document: could not parse upstream response")

    def __extract_document_id_from_write_response(self, response_text):
        text = response_text or ""
        patterns = [
            r"[?&]no=(\d+)",
            r"/(?:board|mini)/[^/'\"?]+/(\d+)",
            r'"no"\s*:\s*"?(\d+)"?',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
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
    def __parse_time(self, time): 
        try:
            today = datetime.now() 
            if len(time) <= 5: 
                if time.find(":") > 0:
                    return datetime.strptime(time, "%H:%M").replace(year=today.year, month=today.month, day=today.day)
                else:
                    return datetime.strptime(time, "%m.%d").replace(year=today.year, hour=23, minute=59, second=59)
            elif len(time) <= 11:
                if time.find(":") > 0:
                    return datetime.strptime(time, "%m.%d %H:%M").replace(year=today.year)
                else:
                    try:
                        return datetime.strptime(time, "%y.%m.%d").replace(year=today.year, hour=23, minute=59, second=59)
                    except ValueError:
                        return datetime.strptime(time, "%Y.%m.%d").replace(hour=23, minute=59, second=59)
            elif len(time) <= 16:
                if time.count(".") >= 2:
                    return datetime.strptime(time, "%Y.%m.%d %H:%M")
                else:
                    return datetime.strptime(time, "%m.%d %H:%M:%S").replace(year=today.year)
            else:
                if "." in time:
                    return datetime.strptime(time, "%Y.%m.%d %H:%M:%S")
                else:
                    return datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now()

import unittest
import sys

# Check version info
version = sys.version_info
if version.major >= 3 and version.minor >= 8:
    class Test(unittest.IsolatedAsyncioTestCase):
        def setUp(self):
            pass
        async def asyncSetUp(self):
            self.api = API()
        async def asyncTearDown(self):
            await self.api.close()
        async def test_async_with(self):
            async with API() as api:
                doc = api.board(board_id='aoegame', num=1).__anext__()
                self.assertNotEqual(doc, None)
        async def test_read_minor_board_one(self):
            async for doc in self.api.board(board_id='aoegame', num=1):
                for attr in doc.__slots__:
                    if attr == 'subject': continue
                    val = getattr(doc, attr)
                    self.assertNotEqual(val, None, attr)
                    self.assertNotEqual(val, '', attr)
                self.assertGreater(doc.time, datetime.now() - timedelta(hours=1))
                self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
        async def test_read_minor_board_many(self):
            count = 0
            async for doc in self.api.board(board_id='aoegame', num=201):
                for attr in doc.__slots__:
                    if attr == 'subject': continue
                    val = getattr(doc, attr)
                    self.assertNotEqual(val, None, attr)
                    self.assertNotEqual(val, '', attr)
                count += 1
                self.assertGreater(doc.time, datetime.now() - timedelta(hours=1))
                self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
            self.assertAlmostEqual(count, 201)
        async def test_read_minor_recent_comments(self):
            async for doc in self.api.board(board_id='aoegame'):
                comments = [comm async for comm in doc.comments()]
                if not comments: continue
                for comm in comments:
                    for attr in comm.__slots__:
                        if attr in ['contents', 'dccon', 'voice', 'author_id']: continue
                        val = getattr(comm, attr)
                        self.assertNotEqual(val, None, attr)
                        self.assertNotEqual(val, '', attr)
                    self.assertNotEqual(comm.contents or comm.dccon or comm.voice, None)
                    self.assertGreater(comm.time, datetime.now() - timedelta(hours=1))
                    self.assertLess(comm.time, datetime.now() + timedelta(hours=1))
                break
        async def test_read_board_one(self):
            async for doc in self.api.board(board_id='programming', num=1):
                for attr in doc.__slots__:
                    if attr == 'subject': continue
                    val = getattr(doc, attr)
                    self.assertNotEqual(val, None, attr)
                    self.assertNotEqual(val, '', attr)
                self.assertGreater(doc.time, datetime.now() - timedelta(hours=24))
                self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
        async def test_read_board_many(self):
            count = 0
            async for doc in self.api.board(board_id='programming', num=201):
                for attr in doc.__slots__:
                    if attr == 'subject': continue
                    val = getattr(doc, attr)
                    self.assertNotEqual(val, None, attr)
                    self.assertNotEqual(val, '', attr)
                count += 1
                self.assertGreater(doc.time, datetime.now() - timedelta(hours=24))
                self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
            self.assertAlmostEqual(count, 201)
        async def test_read_recent_comments(self):
            async for doc in self.api.board(board_id='aoegame'):
                comments = [comm async for comm in doc.comments()]
                if not comments: continue
                for comm in comments:
                    for attr in comm.__slots__:
                        if attr in ['contents', 'dccon', 'voice', 'author_id']: continue
                        val = getattr(comm, attr)
                        self.assertNotEqual(val, None, attr)
                        self.assertNotEqual(val, '', attr)
                    self.assertNotEqual(comm.contents or comm.dccon or comm.voice, None)
                    self.assertGreater(comm.time, datetime.now() - timedelta(hours=24))
                    self.assertLess(comm.time, datetime.now() + timedelta(hours=1))
                break
        async def test_minor_document(self):
            doc = await (await self.api.board(board_id='aoegame', num=1).__anext__()).document()
            self.assertNotEqual(doc, None)
            for attr in doc.__slots__:
                if attr in ['author_id', 'subject']: continue
                val = getattr(doc, attr)
                self.assertNotEqual(val, None, attr)
                self.assertNotEqual(val, '', attr)
            self.assertGreater(doc.time, datetime.now() - timedelta(hours=1))
            self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
        async def test_document(self):
            doc = await (await self.api.board(board_id='programming', num=1).__anext__()).document()
            self.assertNotEqual(doc, None)
            for attr in doc.__slots__:
                if attr in ['author_id', 'subject']: continue
                val = getattr(doc, attr)
                self.assertNotEqual(val, None, attr)
            self.assertGreater(doc.time, datetime.now() - timedelta(hours=1))
            self.assertLess(doc.time, datetime.now() + timedelta(hours=1))
        '''
        async def test_write_mod_del_document_comment(self):
            board_id='programming'
            doc_id = await self.api.write_document(board_id=board_id, title="제목", contents="내용", name="닉네임", password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc.contents, "내용")
            doc_id = await self.api.modify_document(board_id=board_id, document_id=doc_id, title="수정된 제목", contents="수정된 내용", name="수정된 닉네임", password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc.contents, "수정된 내용")
            comm_id = await self.api.write_comment(board_id=board_id, document_id=doc_id, contents="댓글", name="닉네임", password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            comm = await doc.comments().__anext__()
            self.assertEqual(comm.contents, "댓글")
            await self.api.remove_document(board_id=board_id, document_id=doc_id, password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc, None)
        async def test_minor_write_mod_del_document_comment(self):
            board_id='stick'
            doc_id = await self.api.write_document(board_id=board_id, title="제목", contents="내용", name="닉네임", password="비밀번호", is_minor=True)
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc.contents, "내용")
            doc_id = await self.api.modify_document(board_id=board_id, document_id=doc_id, title="수정된 제목", contents="수정된 내용", name="수정된 닉네임", password="비밀번호", is_minor=True)
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc.contents, "수정된 내용")
            comm_id = await self.api.write_comment(board_id=board_id, document_id=doc_id, contents="댓글", name="닉네임", password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            comm = await doc.comments().__anext__()
            self.assertEqual(comm.contents, "댓글")
            await self.api.remove_document(board_id=board_id, document_id=doc_id, password="비밀번호")
            doc = await self.api.document(board_id=board_id, document_id=doc_id)
            self.assertEqual(doc, None)
        '''

if __name__ == "__main__":
    unittest.main()
