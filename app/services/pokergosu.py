"""On-demand, bounded public Pokergosu reader. Independent of the DC event loop."""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
import re
import logging
import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from lxml import etree, html

BASE_URL = "https://www.pokergosu.com"
MAX_PAGE = 10000
MAX_HTML_BYTES = 4 * 1024 * 1024
CACHE_LIMIT = 256
TIMEOUT = 20
logger = logging.getLogger(__name__)


class PokerError(Exception):
    def __init__(self, message="원본을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.", status=502):
        super().__init__(message)
        self.status = status


def _class(name):
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _text(node):
    return " ".join(node.text_content().split()) if node is not None else ""


def _first(nodes):
    return nodes[0] if nodes else None


def _number(value):
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group().replace(",", "")) if match else 0


def _tree(raw):
    try:
        return html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8", no_network=True))
    except (etree.ParserError, ValueError) as exc:
        raise PokerError("원본 페이지 구조를 확인하지 못했어요.") from exc


def parse_board(raw, page):
    tree = _tree(raw)
    tables = [table for table in tree.xpath('//table[.//tr]')
              if all(label in _text(_first(table.xpath('.//tr'))) for label in ('제목', '글쓴이', '날짜'))]
    if not tables or "자유 게시판" not in tree.xpath('string(//title)'):
        raise PokerError("게시판 목록 구조를 확인하지 못했어요.")
    posts, seen = [], set()
    for row in tables[0].xpath('.//tr'):
        cells = row.xpath('./td')
        if len(cells) < 3:
            continue
        links = cells[0].xpath('.//a[@href]')
        link = next((a for a in links if re.fullmatch(r'/free/\d+', urlsplit(a.get('href')).path)), None)
        if link is None:
            continue
        pid = int(urlsplit(link.get('href')).path.rsplit('/', 1)[1])
        if pid in seen:
            continue
        seen.add(pid)
        comment = next((_text(a) for a in links if re.fullmatch(r'\[\d+\]', _text(a))), '')
        posts.append(dict(id=pid, title=_text(link), author=_text(cells[1]), time=_text(cells[2]),
                          comment_count=_number(comment), view_count=_number(_text(cells[3])) if len(cells) > 3 else 0,
                          voteup_count=_number(_text(cells[4])) if len(cells) > 4 else 0))
    pages = [int(v.rsplit(' ', 1)[1]) for v in tree.xpath('//button/@aria-label') if re.fullmatch(r'Go to page \d+', v)]
    return dict(posts=posts, has_next=bool(posts) and page < MAX_PAGE and any(p > page for p in pages))


def _inner(node):
    return etree.tostring(node, encoding='unicode', method='html') if node is not None else ''


def parse_post(raw, pid):
    tree = _tree(raw)
    document = _first(tree.xpath(f'//*[{_class("boarddocument")}]'))
    if document is None:
        raise PokerError("본문을 확인하지 못했어요. 원문에서 접근 가능 여부를 확인해 주세요.")
    body = _first(document.xpath(f'.//*[{_class("edboard")}]'))
    title = _text(_first(document.xpath('.//a[@aria-labelledby="title"]')))
    if body is None or not title:
        raise PokerError("본문 구조가 달라 내용을 표시하지 못했어요.")
    meta = document.xpath(f'.//p[{_class("numbertime")}]')
    stamp = next((_text(e) for e in meta if re.fullmatch(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', _text(e))), '')
    comments, seen = [], set()
    for node in tree.xpath(f'//*[@id and {_class("comment")}]'):
        cid = node.get('id', '')
        if not re.fullmatch(r'C\d+', cid) or cid in seen:
            continue
        seen.add(cid)
        content = _first(node.xpath(f'.//*[{_class("cboard")}]'))
        if content is None:
            continue
        parents = node.xpath(f'ancestor::*[{_class("child-comments")}]')
        parent = _first(parents[-1].xpath(f'preceding-sibling::*[{_class("comment")}]')) if parents else None
        comments.append(dict(id=cid, parent_id=parent.get('id') if parent is not None else None,
                             is_reply=bool(parents), author=_text(_first(node.xpath('.//button//p'))),
                             time=_text(_first(node.xpath(f'.//*[{_class("numbertime")}]'))), html=_inner(content)))
    total_node = _first(tree.xpath('//*[@id="comment"]'))
    total_text = _text(total_node)
    total = _number(total_text) if re.search(r'댓글\s*수\s*\d', total_text) else None
    return dict(id=pid, title=title, author=_text(_first(document.xpath('.//button//p'))), time=stamp,
                view_count=next((_number(_text(e)) for e in meta if '조회 수' in _text(e)), 0),
                voteup_count=next((_number(_text(e)) for e in meta if '추천 수' in _text(e)), 0),
                html=_inner(body), comments=comments, comment_count=total,
                comments_partial=total is None or total > len(comments),
                unsupported_media=bool(body.xpath('.//iframe | .//video | .//audio | .//object | .//embed')))


class Reader:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}
        self.flights = {}
        self.slots = threading.BoundedSemaphore(2)
        self.cooldown_until = 0.0
        self.last_start = 0.0
        self.pace_lock = threading.Lock()

    def _fetch(self, path):
        with self.lock:
            if time.monotonic() < self.cooldown_until:
                raise PokerError("원본 요청이 잠시 제한됐어요. 잠시 후 다시 시도해 주세요.", 503)
        if not self.slots.acquire(timeout=1):
            raise PokerError("요청이 많아요. 잠시 후 다시 시도해 주세요.", 503)
        try:
            with self.pace_lock:
                delay = max(0, self.last_start + 0.3 - time.monotonic())
                if delay:
                    time.sleep(delay)
                self.last_start = time.monotonic()
            with self.lock:
                if time.monotonic() < self.cooldown_until:
                    raise PokerError("원본 요청이 잠시 제한됐어요. 잠시 후 다시 시도해 주세요.", 503)
            chunks, size = [], 0
            def receive(chunk):
                nonlocal size
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    return CURL_WRITEFUNC_ERROR
                chunks.append(chunk)
                return len(chunk)
            # A request owns its session, so no curl handle is shared across Flask threads.
            with requests.Session(impersonate='chrome', trust_env=False) as session:
                response = session.get(BASE_URL + path, timeout=TIMEOUT, allow_redirects=False,
                                       content_callback=receive)
            if size > MAX_HTML_BYTES:
                raise PokerError("원본 페이지가 너무 커서 가져오지 못했어요.")
            if response.headers.get('cf-mitigated') == 'challenge' or response.status_code in (403, 429):
                with self.lock:
                    self.cooldown_until = time.monotonic() + 60
                raise PokerError("원본 요청이 잠시 제한됐어요. 잠시 후 다시 시도해 주세요.", 503)
            if response.status_code in (404, 410):
                raise PokerError("글을 찾을 수 없어요. 삭제되었거나 주소가 바뀌었을 수 있어요.", 404)
            if response.status_code != 200:
                raise PokerError()
            return b''.join(chunks)
        except requests.RequestsError as exc:
            raise PokerError() from exc
        finally:
            self.slots.release()

    def get(self, key, path, parser, ttl):
        with self.lock:
            now = time.monotonic()
            cached = self.cache.get(key)
            if cached and cached[0] > now:
                return self._result(cached[1], cached[2])
            future = self.flights.get(key)
            owner = future is None
            if owner:
                future = self.flights[key] = Future()
        if not owner:
            try:
                value, error = future.result(timeout=TIMEOUT + 5)
            except FutureTimeout as exc:
                raise PokerError() from exc
            return self._result(value, error)
        value, error = None, None
        try:
            value = parser(self._fetch(path))
        except PokerError as exc:
            error = (str(exc), exc.status)
        except Exception:
            logger.exception("Pokergosu parse failed for %r", key)
            error = ("원본 페이지를 처리하지 못했어요.", 502)
        finally:
            with self.lock:
                self.cache = {k: v for k, v in self.cache.items() if v[0] > time.monotonic()}
                if len(self.cache) >= CACHE_LIMIT:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (time.monotonic() + (10 if error else ttl), value, error)
                self.flights.pop(key, None)
                future.set_result((value, error))
        return self._result(value, error)

    @staticmethod
    def _result(value, error):
        if error:
            raise PokerError(*error)
        return deepcopy(value)

    def board(self, page):
        return self.get(('board', page), f'/free?page={page}', lambda raw: parse_board(raw, page), 20)

    def post(self, pid):
        return self.get(('post', pid), f'/free/{pid}', lambda raw: parse_post(raw, pid), 30)


reader = Reader()
