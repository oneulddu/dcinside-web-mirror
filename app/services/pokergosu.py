"""On-demand, bounded public Pokergosu reader. Independent of the DC event loop."""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import logging
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit

from curl_cffi import requests
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from lxml import etree, html

from .poker_boards import BASE_URL, MAX_PAGE, BOARDS, poker_link, is_login_redirect, validate_search
from .poker_media import youtube_iframe_src
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


def _board_spec(board_id):
    if board_id not in BOARDS:
        raise PokerError("지원하지 않는 게시판이에요.", 404)
    return BOARDS[board_id]


def _next_page(tree, page, posts):
    pages = [int(v.rsplit(' ', 1)[1]) for v in tree.xpath('//button/@aria-label')
             if re.fullmatch(r'Go to page [0-9]{1,8}', v)]
    return bool(posts) and page < MAX_PAGE and any(p > page for p in pages)


def _row_link(links, board_id):
    for link in links:
        parsed = poker_link(link.get('href'))
        if parsed and parsed['board_id'] == board_id and parsed['pid'] and _text(link):
            return link, parsed['pid']
    return None, None


def _comments_count(links):
    return next((_number(_text(a)) for a in links if re.fullmatch(r'\[[0-9]+\]', _text(a))), 0)


def _upstream_image_path(value):
    try:
        url = urlsplit(urljoin(BASE_URL, value or ''))
        if url.scheme == 'https' and url.hostname in ('ipokergosu.com', 'www.ipokergosu.com'):
            return url.path
    except ValueError:
        pass
    return ''


def _has_attachment(link):
    return any(_upstream_image_path(image.get('src')) == '/files/pg/util/file.gif'
               for image in link.xpath('.//img[@alt="file"]'))


def _has_news_image(card, pid):
    for link in card.xpath('.//a[@href]'):
        target = poker_link(link.get('href'))
        if not target or target['board_id'] != 'news' or target['pid'] != pid:
            continue
        if any(_upstream_image_path(image.get('src')).startswith(('/img2/', '/files/attach/'))
               for image in link.xpath('.//img[@src]')):
            return True
    return False


def parse_board(raw, page, board_id='free', *, search=False):
    spec = _board_spec(board_id)
    tree = _tree(raw)
    title = tree.xpath('string(//title)').strip()
    if (search and title != '검색 - 포커고수') or (not search and spec['label'] not in title):
        raise PokerError("게시판 목록 구조를 확인하지 못했어요.")
    posts, seen = [], set()
    if board_id == 'news':
        grids = tree.xpath(f'//div[{_class("grid-cols-2")} and {_class("gap-y-4")}]')
        if len(grids) != 1:
            raise PokerError("뉴스 목록 구조를 확인하지 못했어요.")
        cards = grids[0].xpath('./div')
        for card in cards:
            links = card.xpath('./p/a[@href]')
            link, pid = _row_link(links, board_id)
            if link is None and search:
                raise PokerError("뉴스 검색 결과 구조를 확인하지 못했어요.")
            if link is None or pid in seen:
                continue
            seen.add(pid)
            posts.append(dict(board_id=board_id, id=pid, title=_text(link),
                              comment_count=_comments_count(links), author=None, time=None,
                              has_image=_has_news_image(card, pid), has_attachment=False,
                              view_count=None, voteup_count=None))
        if cards and not posts:
            raise PokerError("뉴스 목록 구조를 확인하지 못했어요.")
    else:
        tables = [table for table in tree.xpath('//table[.//tr]')
                  if all(label in _text(_first(table.xpath('.//tr'))) for label in ('제목', '글쓴이', '날짜'))]
        if not tables:
            raise PokerError("게시판 목록 구조를 확인하지 못했어요.")
        for row in tables[0].xpath('.//tr')[1:]:
            cells = row.xpath('./td')
            if len(cells) < 3:
                if search:
                    raise PokerError("검색 결과 구조를 확인하지 못했어요.")
                continue
            links = cells[0].xpath('.//a[@href]')
            link, pid = _row_link(links, board_id)
            if link is None and search:
                # Upstream may retain the shared notice row above board results.
                notice, _ = _row_link(links, 'notice')
                if notice is None or board_id == 'notice':
                    raise PokerError("검색 결과 구조를 확인하지 못했어요.")
            if link is None or pid in seen:
                continue
            seen.add(pid)
            posts.append(dict(board_id=board_id, id=pid, title=_text(link), author=_text(cells[1]) or None,
                              has_image=False, has_attachment=_has_attachment(link),
                              time=_text(cells[2]) or None, comment_count=_comments_count(links),
                              view_count=_number(_text(cells[3])) if len(cells) > 3 else None,
                              voteup_count=_number(_text(cells[4])) if len(cells) > 4 else None))
    return dict(posts=posts, has_next=_next_page(tree, page, posts))


def parse_search(raw, page, board_id='free'):
    return parse_board(raw, page, board_id, search=True)


def _inner(node):
    return etree.tostring(node, encoding='unicode', method='html') if node is not None else ''


def _comment_page(total_node, complete):
    """Only the comment heading's pager identifies this response's page."""
    buttons = total_node.xpath('.//button[@aria-label]') if total_node is not None else []
    active = []
    for button in buttons:
        if 'Page-active' not in (button.get('class') or '').split():
            continue
        match = re.fullmatch(r'Go to page ([0-9]{1,5})', button.get('aria-label', ''))
        if not match or not 1 <= int(match[1]) <= MAX_PAGE:
            return None
        active.append(int(match[1]))
    if len(active) == 1:
        return active[0]
    return 1 if not buttons and complete else None


def parse_post(raw, pid):
    tree = _tree(raw)
    document = _first(tree.xpath(f'//*[{_class("boarddocument")}]'))
    if document is None:
        raise PokerError("본문을 확인하지 못했어요. 원문에서 접근 가능 여부를 확인해 주세요.")
    body = _first(document.xpath(f'.//*[{_class("edboard")}]'))
    if body is None:
        raise PokerError("본문 구조가 달라 내용을 표시하지 못했어요.")
    # Some boarddocument wrappers also contain comments. Only inspect the blocks
    # before the body, otherwise a missing article author becomes a comment author.
    headers = body.getparent().xpath('preceding-sibling::*')
    title = _text(_first([node for block in headers for node in block.xpath('.//a[@aria-labelledby="title"]')]))
    if not title:
        raise PokerError("본문 구조가 달라 내용을 표시하지 못했어요.")
    author = _text(_first([node for block in headers for node in block.xpath('.//button[not(@title)]//p')])) or None
    meta = [node for block in headers for node in block.xpath(f'.//p[{_class("numbertime")}]')]
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
                             is_reply=bool(parents), author=_text(_first(node.xpath('.//button[not(@title)]//p'))) or None,
                             time=_text(_first(node.xpath(f'.//*[{_class("numbertime")}]'))), html=_inner(content)))
    total_node = _first(tree.xpath('//*[@id="comment"]'))
    total_text = ' '.join(total_node.itertext()) if total_node is not None else ''
    total = _number(total_text) if re.search(r'댓글\s*수\s*\d', total_text) else None
    partial = total is None or total != len(comments)
    comment_page = _comment_page(total_node, not partial)
    return dict(id=pid, title=title, author=author, time=stamp,
                view_count=next((_number(_text(e)) for e in meta if '조회 수' in _text(e)), None),
                voteup_count=next((_number(_text(e)) for e in meta if '추천 수' in _text(e)), None),
                html=_inner(body), comments=comments, comment_count=total,
                comments_partial=partial, comment_page=comment_page,
                comments_next_page=comment_page - 1 if comment_page and comment_page > 1 else None,
                unsupported_media=(any(not youtube_iframe_src(node.get('src')) for node in body.xpath('.//iframe'))
                    or bool(body.xpath('.//video | .//audio | .//object | .//embed | .//source | .//a[@download]'))
                    or any(_has_attachment(link) for link in body.xpath('.//a'))))


@dataclass
class CacheEntry:
    fresh_until: float = 0.0
    stale_until: float = 0.0
    value: dict | None = None
    error_until: float = 0.0
    error: tuple | None = None


class Reader:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}
        self.flights = {}
        self.slots = threading.BoundedSemaphore(2)
        self.cooldown_until = 0.0
        self.last_start = 0.0
        self.pace_lock = threading.Lock()
        self.sessions = threading.local()
        # MIRROR_POKER_STALE_SECONDS extends successful values beyond their TTL.
        self.stale_seconds = max(0, float(os.getenv('MIRROR_POKER_STALE_SECONDS', '600')))
        # Shared wall-clock timestamps survive worker restarts; never replace this
        # file's inode, since every worker must flock the same file.
        self.state_file = Path(os.getenv('MIRROR_POKER_STATE_FILE',
                              str(Path(__file__).resolve().parents[2] / 'instance/poker_upstream_state.json')))
        self.state_failed = False

    def _upstream_state(self, action):
        """Check cooldown, attempt a paced start, or set cooldown under flock."""
        def update(state):
            now = time.time()
            # Clamp shared wall-clock values so a clock step backwards cannot turn
            # into a long sleep or cooldown outside the HTTP timeout.
            cooldown = min(max(self.cooldown_until, state.get('cooldown_until', 0)), now + 60)
            last = min(max(self.last_start, state.get('last_start', 0)), now)
            if action == 'cooldown':
                cooldown = max(cooldown, now + 60)
            blocked = cooldown > now
            delay = min(0.3, max(0, last + 0.3 - now)) if action == 'start' else 0
            if action == 'start' and not blocked and not delay:
                last = now
            self.cooldown_until, self.last_start = cooldown, last
            return {'cooldown_until': cooldown, 'last_start': last}, blocked, delay

        with self.pace_lock:
            if not self.state_failed:
                try:
                    self.state_file.parent.mkdir(parents=True, exist_ok=True)
                    with self.state_file.open('a+', encoding='utf-8') as handle:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                        handle.seek(0)
                        try:
                            state = json.loads(handle.read(4096))
                            if not isinstance(state, dict):
                                state = {}
                            state = {key: value for key, value in state.items()
                                     if key in ('cooldown_until', 'last_start')
                                     and type(value) in (int, float) and math.isfinite(value) and value >= 0}
                        except (ValueError, UnicodeError, OverflowError):
                            state = {}
                        state, blocked, delay = update(state)
                        handle.seek(0)
                        handle.truncate()
                        json.dump(state, handle)
                        handle.flush()
                        return blocked, delay
                except OSError:
                    self.state_failed = True
                    logger.warning('Pokergosu shared limiter unavailable; using process-local state: %s',
                                   self.state_file)
            _, blocked, delay = update({})
            return blocked, delay

    def _check_upstream(self, action):
        blocked, delay = self._upstream_state(action)
        if blocked:
            raise PokerError("원본 요청이 잠시 제한됐어요. 잠시 후 다시 시도해 주세요.", 503)
        return delay

    def _session(self):
        if not hasattr(self.sessions, 'session'):
            self.sessions.session = requests.Session(impersonate='chrome', trust_env=False)
        return self.sessions.session

    def _discard_session(self):
        session = getattr(self.sessions, 'session', None)
        if session is not None:
            del self.sessions.session
            try:
                session.close()
            except Exception:
                logger.debug('Pokergosu session close failed', exc_info=True)

    def _fetch(self, path):
        self._check_upstream('check')
        if not self.slots.acquire(timeout=1):
            raise PokerError("요청이 많아요. 잠시 후 다시 시도해 주세요.", 503)
        try:
            while delay := self._check_upstream('start'):
                time.sleep(delay)
            chunks, size = [], 0
            def receive(chunk):
                nonlocal size
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    return CURL_WRITEFUNC_ERROR
                chunks.append(chunk)
                return len(chunk)
            # A thread owns its session, and each request gets its own size guard.
            response = self._session().get(BASE_URL + path, timeout=TIMEOUT, allow_redirects=False,
                                           content_callback=receive)
            if size > MAX_HTML_BYTES:
                self._discard_session()
                raise PokerError("원본 페이지가 너무 커서 가져오지 못했어요.")
            if response.headers.get('cf-mitigated') == 'challenge' or response.status_code in (403, 429):
                self._discard_session()
                self._upstream_state('cooldown')
                raise PokerError("원본 요청이 잠시 제한됐어요. 잠시 후 다시 시도해 주세요.", 503)
            if response.status_code in (301, 302, 303, 307, 308) and is_login_redirect(response.headers.get('location')):
                raise PokerError("원본 로그인이 필요한 게시판이에요. 원문에서 확인해 주세요.", 403)
            if response.status_code in (404, 410):
                raise PokerError("글을 찾을 수 없어요. 삭제되었거나 주소가 바뀌었을 수 있어요.", 404)
            if response.status_code == 400:
                raise PokerError("원본 요청을 확인해 주세요.", 400)
            if response.status_code != 200:
                raise PokerError()
            return b''.join(chunks)
        except requests.RequestsError as exc:
            self._discard_session()
            raise PokerError() from exc
        finally:
            self.slots.release()

    def get(self, key, path, parser, ttl):
        with self.lock:
            now = time.monotonic()
            cached = self.cache.get(key)
            if cached and (cached.fresh_until > now or cached.error_until > now):
                return self._result(cached)
            future = self.flights.get(key)
            owner = future is None
            if owner:
                future = self.flights[key] = Future()
        if not owner:
            try:
                entry = future.result(timeout=TIMEOUT + 5)
            except FutureTimeout as exc:
                with self.lock:
                    cached = self.cache.get(key)
                    if cached and cached.value is not None and cached.stale_until > time.monotonic():
                        return self._stale(cached.value)
                raise PokerError() from exc
            return self._result(entry)
        value, error = None, None
        try:
            raw = self._fetch(path)
            try:
                value = parser(raw)
            except PokerError:
                # Only parser failures belong here, not transport/login errors.
                logger.warning("Pokergosu structure parse failed for %r", key)
                raise
            value.pop('stale', None)
            if key[0] == 'board' and key[2] == 1 and not value['posts']:
                logger.warning("Pokergosu empty first board page for %r", key)
        except PokerError as exc:
            error = (str(exc), exc.status)
        except Exception:
            logger.exception("Pokergosu parse failed for %r", key)
            error = ("원본 페이지를 처리하지 못했어요.", 502)
        finally:
            with self.lock:
                now = time.monotonic()
                previous = self.cache.get(key)
                if error:
                    entry = CacheEntry(error_until=now + 10, error=error)
                    if (error[1] in (502, 503) and previous and previous.value is not None
                            and previous.stale_until > now):
                        entry.value = previous.value
                        entry.stale_until = previous.stale_until
                else:
                    entry = CacheEntry(fresh_until=now + ttl,
                                       stale_until=now + ttl + self.stale_seconds, value=value)
                self.cache = {k: v for k, v in self.cache.items()
                              if max(v.stale_until, v.error_until) > now}
                # Drop expired successes even if their negative-cache entry lives on.
                for retained in self.cache.values():
                    if retained.stale_until <= now:
                        retained.value = None
                self.cache.pop(key, None)
                if len(self.cache) >= CACHE_LIMIT:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = entry
                self.flights.pop(key, None)
                future.set_result(entry)
        return self._result(entry)

    @staticmethod
    def _stale(value):
        data = deepcopy(value)
        data['stale'] = True
        return data

    @classmethod
    def _result(cls, entry):
        """Fresh data omits `stale`; only transient-error fallback sets it True."""
        if entry.error:
            if (entry.error[1] in (502, 503) and entry.value is not None
                    and entry.stale_until > time.monotonic()):
                return cls._stale(entry.value)
            raise PokerError(*entry.error)
        return deepcopy(entry.value)

    @staticmethod
    def _prepare(data, prepare):
        if prepare is not None:
            prepare_body, prepare_comment = prepare if isinstance(prepare, tuple) else (prepare, prepare)
            if 'html' in data:
                data['html'] = prepare_body(data['html'])
            for comment in data['comments']:
                comment['html'] = prepare_comment(comment['html'])
        return data

    def board(self, page, board_id='free'):
        _board_spec(board_id)
        return self.get(('board', board_id, page), f'/{board_id}?page={page}',
                        lambda raw: parse_board(raw, page, board_id), 20)

    def search(self, page, *, board_id, s, v):
        spec = _board_spec(board_id)
        try:
            search = validate_search(board_id, s, v)
            if type(page) is not int or not 1 <= page <= MAX_PAGE:
                raise ValueError('페이지를 확인해 주세요.')
        except ValueError as exc:
            raise PokerError(str(exc), 400) from exc
        if spec.get('login_required'):
            raise PokerError('원본 로그인이 필요한 게시판이에요. 원문에서 확인해 주세요.', 403)
        s, v = search['s'], search['v']
        query = urlencode(dict(s=s, v=v, page=page))
        return self.get(('search', board_id, s, v, page), f'/{board_id}/search?{query}',
                        lambda raw: parse_search(raw, page, board_id), 20)

    def post(self, pid, board_id='free', *, prepare=None):
        _board_spec(board_id)
        # Raw smoke/parser consumers cannot populate the prepared route cache.
        key = ('post', board_id, pid) + (('prepared',) if prepare is not None else ())
        if isinstance(prepare, tuple):
            key += ('body-comment',)
        return self.get(key, f'/{board_id}/{pid}',
                        lambda raw: self._prepare(parse_post(raw, pid), prepare), 30)

    def comment_page(self, pid, page, board_id='free', *, prepare=None):
        _board_spec(board_id)
        if (type(pid) is not int or not 1 <= pid <= 999999999999
                or type(page) is not int or not 1 <= page <= MAX_PAGE):
            raise PokerError('댓글 요청을 확인해 주세요.', 400)

        def parse(raw):
            data = parse_post(raw, pid)
            if data['comment_page'] != page:
                raise PokerError('원본의 댓글 페이지를 확인하지 못했어요.')
            result = {key: data[key] for key in ('comments', 'comment_count', 'comment_page', 'comments_next_page')}
            return self._prepare(result, prepare)

        key = ('comments', board_id, pid, page) + (('prepared',) if prepare is not None else ())
        return self.get(key, f'/{board_id}/{pid}?cpage={page}', parse, 300)


reader = Reader()
