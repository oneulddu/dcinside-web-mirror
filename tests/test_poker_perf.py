"""Cache, preparation, transport reuse, and shared limiter regression tests."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

from app import create_app, poker_routes
from app.services import pokergosu as pg

FIXTURES = Path(__file__).parent / 'fixtures/pokergosu'


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(pg, 'time', value)
    return value


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch, tmp_path):
    monkeypatch.setenv('MIRROR_POKER_STATE_FILE', str(tmp_path / 'upstream.json'))
    monkeypatch.setenv('MIRROR_POKER_STALE_SECONDS', '600')


def failing(status):
    def fail(path):
        raise pg.PokerError('upstream unavailable', status)
    return fail


@pytest.mark.parametrize('status', [502, 503])
@pytest.mark.parametrize('kind,ttl', [('board', 20), ('post', 30), ('comments', 300)])
def test_stale_success_survives_negative_cache_and_recovers(monkeypatch, clock, status, kind, ttl, poker_upstream):
    reader = pg.Reader()
    raw = (FIXTURES / ('board.html' if kind == 'board' else 'post.html')).read_bytes()
    upstream = poker_upstream(raw)
    calls = []
    broken = False

    def fetch(path):
        calls.append(path)
        if broken:
            raise pg.PokerError('transient', status)
        return upstream(path)

    monkeypatch.setattr(reader, '_fetch', fetch)
    read = {'board': lambda: reader.board(1), 'post': lambda: reader.post(123),
            'comments': lambda: reader.comment_page(123, 1)}[kind]
    fresh = read()
    assert 'stale' not in fresh
    clock.now += ttl - 1
    assert read() == fresh and len(calls) == 1
    clock.now += 2
    broken = True
    stale = read()
    assert stale == dict(fresh, stale=True)
    stale['posts' if kind == 'board' else 'comments'].clear()
    assert read() == dict(fresh, stale=True) and len(calls) == 2
    clock.now += 11
    broken = False
    assert read() == fresh and len(calls) == 3
    assert 'stale' not in read()


@pytest.mark.parametrize('status', [400, 403, 404])
def test_authoritative_error_discards_old_success(monkeypatch, clock, status):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'board.html').read_bytes())
    reader.board(1)
    clock.now += 21
    monkeypatch.setattr(reader, '_fetch', failing(status))
    for _ in range(2):
        with pytest.raises(pg.PokerError) as exc:
            reader.board(1)
        assert exc.value.status == status
    # A later transient error must not resurrect a removed/restricted page.
    clock.now += 11
    monkeypatch.setattr(reader, '_fetch', failing(502))
    with pytest.raises(pg.PokerError):
        reader.board(1)


def test_stale_window_is_not_extended_by_repeated_errors(monkeypatch, clock):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'board.html').read_bytes())
    reader.board(1)
    monkeypatch.setattr(reader, '_fetch', failing(502))
    clock.now = 1021
    assert reader.board(1)['stale']
    clock.now = 1619
    assert reader.board(1)['stale']
    # Still inside the 10s negative cache, but outside the original stale window.
    clock.now = 1621
    with pytest.raises(pg.PokerError):
        reader.board(1)


def test_stale_config_and_bounded_pruning(monkeypatch, clock):
    monkeypatch.setenv('MIRROR_POKER_STALE_SECONDS', '5')
    monkeypatch.setattr(pg, 'CACHE_LIMIT', 2)
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'board.html').read_bytes())
    reader.board(1)
    clock.now += 21
    reader.board(2)
    assert ('board', 'free', 1) in reader.cache  # Expired but still eligible.
    reader.board(3)
    assert len(reader.cache) == 2
    assert ('board', 'free', 1) not in reader.cache
    clock.now += 26
    reader.board(4)
    assert list(reader.cache) == [('board', 'free', 4)]


def test_zero_stale_disables_fallback(monkeypatch, clock):
    monkeypatch.setenv('MIRROR_POKER_STALE_SECONDS', '0')
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'board.html').read_bytes())
    reader.board(1)
    clock.now += 21
    monkeypatch.setattr(reader, '_fetch', failing(503))
    with pytest.raises(pg.PokerError):
        reader.board(1)


def test_failed_refresh_still_coalesces_and_copies(monkeypatch, clock):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'board.html').read_bytes())
    reader.board(1)
    clock.now += 21
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        raise pg.PokerError('busy', 503)

    monkeypatch.setattr(reader, '_fetch', fetch)
    with ThreadPoolExecutor(4) as pool:
        first = pool.submit(reader.board, 1)
        assert entered.wait(2)
        rest = [pool.submit(reader.board, 1) for _ in range(3)]
        release.set()
        results = [future.result(3) for future in [first, *rest]]
    assert len(calls) == 1 and not reader.flights
    assert all(data['stale'] for data in results)
    results[0]['posts'].clear()
    assert all(len(data['posts']) == 2 for data in results[1:])


def install_session(monkeypatch, outcomes=None):
    sessions, starts = [], []
    outcomes = list(outcomes or [])

    class Session:
        def __init__(self, **kwargs):
            assert kwargs == {'impersonate': 'chrome', 'trust_env': False}
            self.thread = threading.get_ident()
            self.closed = False
            self.calls = 0
            sessions.append(self)

        def close(self):
            self.closed = True

        def get(self, url, **kwargs):
            assert threading.get_ident() == self.thread and not self.closed
            self.calls += 1
            starts.append(pg.time.time())
            outcome = outcomes.pop(0) if outcomes else 200
            if isinstance(outcome, Exception):
                raise outcome
            body = (FIXTURES / 'board.html').read_bytes()
            if kwargs['content_callback'](body) != len(body):
                raise pg.requests.RequestsError('write aborted')
            return SimpleNamespace(status_code=outcome, headers={})

    monkeypatch.setattr(pg.requests, 'Session', Session)
    return sessions, starts


def test_shared_cooldown_between_readers(monkeypatch, clock, tmp_path):
    sessions, starts = install_session(monkeypatch, [429, 200])
    first, second = pg.Reader(), pg.Reader()
    with pytest.raises(pg.PokerError):
        first._fetch('/free?page=1')
    with pytest.raises(pg.PokerError) as exc:
        second._fetch('/hand?page=1')
    assert exc.value.status == 503 and len(starts) == 1 and sessions[0].closed
    assert json.loads((tmp_path / 'upstream.json').read_text())['cooldown_until'] == 1060
    clock.now = 1061
    second._fetch('/hand?page=1')
    assert len(starts) == 2


def test_comment_page_challenge_pauses_only_comment_pages(monkeypatch, clock, tmp_path, caplog):
    # 원본이 이전 댓글 요청만 확인 화면으로 막으면 글·목록 읽기는 계속돼야 한다.
    sessions, starts = install_session(monkeypatch, [403, 200, 200])
    first, second = pg.Reader(), pg.Reader()
    with pytest.raises(pg.PokerError) as exc:
        first._fetch(pg.comment_api_path('best', 123, 1))
    assert exc.value.status == 503 and str(exc.value) == pg.COMMENTS_BLOCKED_MESSAGE
    assert 'comment pages blocked' in caplog.text
    state = json.loads((tmp_path / 'upstream.json').read_text())
    assert state['cooldown_until'] <= clock.now and state[pg.COMMENT_BLOCK_KEY] == 1000 + 21600
    second._fetch('/best/123')
    second._fetch('/best?page=1')
    assert len(starts) == 3
    with pytest.raises(pg.PokerError) as exc:
        second._fetch(pg.comment_api_path('best', 456, 2))
    assert str(exc.value) == pg.COMMENTS_BLOCKED_MESSAGE and len(starts) == 3
    assert second.comments_blocked() is True
    clock.now = 1000 + 21600 + 1
    assert second.comments_blocked() is False
    second._fetch(pg.comment_api_path('best', 456, 2))
    assert len(starts) == 4


def test_comment_block_set_during_pacing_wait_stops_request(monkeypatch, clock):
    # 요청 간격을 기다리는 사이 다른 워커가 댓글 차단을 걸면 전송하지 않는다.
    _, starts = install_session(monkeypatch)
    reader, other = pg.Reader(), pg.Reader()
    reader._fetch('/best?page=1')
    original_sleep = clock.sleep

    def sleep(delay):
        other._upstream_state('block_comments')
        original_sleep(delay)

    clock.sleep = sleep
    with pytest.raises(pg.PokerError) as exc:
        reader._fetch(pg.comment_api_path('best', 123, 1))
    assert str(exc.value) == pg.COMMENTS_BLOCKED_MESSAGE and len(starts) == 1


def test_old_cpage_block_record_does_not_pause_comment_api(monkeypatch, clock, tmp_path):
    # 막혔던 ?cpage= 시절의 기록은 새 JSON 경로를 막지 않는다.
    (tmp_path / 'upstream.json').write_text(json.dumps({'comments_blocked_until': 5000}))
    _, starts = install_session(monkeypatch)
    reader = pg.Reader()
    assert reader.comments_blocked() is False
    reader._fetch(pg.comment_api_path('best', 123, 1))
    assert len(starts) == 1


def test_comment_api_request_looks_like_the_upstream_page(monkeypatch):
    seen = []

    class Session:
        def __init__(self, **kwargs):
            pass

        def get(self, url, **kwargs):
            seen.append((url, kwargs.get('headers')))
            kwargs['content_callback'](b'{}')
            return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(pg.requests, 'Session', Session)
    reader = pg.Reader()
    reader._fetch(pg.comment_api_path('hand', 77, 3))
    reader._fetch('/hand/77')
    assert seen[0] == (pg.BASE_URL + '/api2/board/getcommnet/hand/77/3/25/xpage/20/undefined/undefined/0',
                       {'Accept': 'application/json, text/plain, */*', 'Referer': pg.BASE_URL + '/hand/77'})
    assert seen[1] == (pg.BASE_URL + '/hand/77', None)


def test_page_challenge_still_cools_down_comment_pages(monkeypatch, clock):
    _, starts = install_session(monkeypatch, [429])
    reader = pg.Reader()
    with pytest.raises(pg.PokerError):
        reader._fetch('/best?page=1')
    with pytest.raises(pg.PokerError) as exc:
        reader._fetch(pg.comment_api_path('best', 123, 1))
    assert str(exc.value) == pg.COOLDOWN_MESSAGE and len(starts) == 1
    assert reader.comments_blocked() is False


def test_shared_start_pacing_between_readers(monkeypatch, clock):
    _, starts = install_session(monkeypatch)
    first, second = pg.Reader(), pg.Reader()
    first._fetch('/free?page=1')
    second._fetch('/hand?page=1')
    first._fetch('/news?page=1')
    assert starts == pytest.approx([1000, 1000.3, 1000.6])
    assert clock.sleeps == pytest.approx([0.3, 0.3])


def test_future_shared_timestamps_are_clamped_after_clock_step_back(monkeypatch, clock, tmp_path):
    # 다른 워커가 시계가 앞서 있을 때 적은 값이 남아도 긴 대기·차단으로 번지지 않는다.
    (tmp_path / 'upstream.json').write_text(json.dumps({'last_start': 1300, 'cooldown_until': 5000}))
    _, starts = install_session(monkeypatch)
    reader = pg.Reader()
    with pytest.raises(pg.PokerError):
        reader._fetch('/free?page=1')
    assert starts == [] and clock.sleeps == []
    clock.now = 1061
    reader._fetch('/free?page=1')
    assert starts == [1061]
    assert max(clock.sleeps, default=0) <= 0.3


@pytest.mark.parametrize('state', ['garbage', '[]', '{"last_start":"bad","cooldown_until":null}',
                                    '{"last_start":NaN}', '\udcff'])
def test_corrupt_state_recovers(monkeypatch, tmp_path, clock, state):
    path = tmp_path / 'upstream.json'
    path.write_bytes(state.encode('utf-8', errors='surrogateescape'))
    _, starts = install_session(monkeypatch)
    pg.Reader()._fetch('/free?page=1')
    assert starts == [1000]
    assert json.loads(path.read_text())['last_start'] == 1000


def test_unusable_state_logs_once_and_keeps_local_limits(monkeypatch, tmp_path, clock, caplog):
    monkeypatch.setenv('MIRROR_POKER_STATE_FILE', str(tmp_path))  # A directory cannot be opened as a file.
    _, starts = install_session(monkeypatch, [200, 429])
    reader = pg.Reader()
    reader._fetch('/free?page=1')
    with pytest.raises(pg.PokerError):
        reader._fetch('/free?page=2')
    with pytest.raises(pg.PokerError):
        reader._fetch('/free?page=3')
    assert starts == pytest.approx([1000, 1000.3])
    assert sum('shared limiter unavailable' in record.message for record in caplog.records) == 1


def test_sessions_reused_only_on_own_thread(monkeypatch):
    sessions, _ = install_session(monkeypatch)
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_check_upstream', lambda action: 0)
    barrier = threading.Barrier(2)

    def fetch_twice():
        barrier.wait(timeout=3)
        reader._fetch('/free?page=1')
        reader._fetch('/free?page=2')

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(fetch_twice) for _ in range(2)]
        for future in futures:
            future.result(3)
    assert len(sessions) == 2
    assert len({session.thread for session in sessions}) == 2
    assert all(session.calls == 2 and not session.closed for session in sessions)


def test_request_error_discards_session_and_returns_stale(monkeypatch, clock):
    sessions, _ = install_session(monkeypatch, [200, pg.requests.RequestsError('timeout'), 200])
    reader = pg.Reader()
    reader.board(1)
    clock.now += 21
    assert reader.board(1)['stale']
    assert sessions[0].closed
    clock.now += 11
    assert 'stale' not in reader.board(1)
    assert len(sessions) == 2


def test_oversize_discards_session_and_next_request_has_new_callback(monkeypatch, clock):
    sessions, _ = install_session(monkeypatch)
    reader = pg.Reader()
    original_limit = pg.MAX_HTML_BYTES
    monkeypatch.setattr(pg, 'MAX_HTML_BYTES', 2)
    with pytest.raises(pg.PokerError):
        reader._fetch('/free?page=1')
    assert sessions[0].closed
    monkeypatch.setattr(pg, 'MAX_HTML_BYTES', original_limit)
    assert reader._fetch('/free?page=2') == (FIXTURES / 'board.html').read_bytes()
    assert reader._fetch('/free?page=3') == (FIXTURES / 'board.html').read_bytes()
    assert len(sessions) == 2 and sessions[1].calls == 2


def test_busy_slots_return_stale(monkeypatch, clock):
    install_session(monkeypatch)
    reader = pg.Reader()
    reader.board(1)
    clock.now += 21
    monkeypatch.setattr(reader, 'slots', SimpleNamespace(acquire=lambda **kwargs: False))
    assert reader.board(1)['stale']


@pytest.mark.parametrize('path,fixture_name', [('/poker/free/123', 'post.html'),
                                             ('/poker/hand/123/comments?cpage=1', 'comments-page-1.html')])
def test_routes_prepare_once_per_cache_fill_and_keep_signed_images(monkeypatch, clock, path, fixture_name, poker_upstream):
    reader = pg.Reader()
    calls = []
    original = poker_routes.prepare_html

    def prepare(raw, **kwargs):
        calls.append(kwargs['base_url'])
        return original(raw, **kwargs)

    monkeypatch.setattr(reader, '_fetch', poker_upstream((FIXTURES / fixture_name).read_bytes()))
    monkeypatch.setattr(poker_routes, 'reader', reader)
    monkeypatch.setattr(poker_routes, 'prepare_html', prepare)
    client = create_app().test_client()
    first = client.get(path)
    assert first.status_code == 200
    count = len(calls)
    assert count == (4 if fixture_name == 'post.html' else 28)
    second = client.get(path)
    assert second.status_code == 200 and len(calls) == count
    raw = second.data if fixture_name == 'post.html' else ''.join(row['html'] for row in second.json['comments'])
    soup = BeautifulSoup(raw, 'html.parser')
    if fixture_name == 'post.html':
        soup = soup.select_one('#article-body')
    assert not soup.select('script,[onerror]')
    assert soup.select_one('img[data-body-image-src]')['data-body-image-src'].startswith('/poker/media?')
    assert all('www.pokergosu.com/' in base for base in calls)
    clock.now += 301
    monkeypatch.setattr(reader, '_fetch', failing(502))
    third = client.get(path)
    assert third.status_code == 200 and len(calls) == count
    if fixture_name != 'post.html':
        assert 'stale' not in second.json and third.json['stale'] is True


def test_raw_cache_cannot_bypass_preparation(monkeypatch):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'post.html').read_bytes())
    raw = reader.post(123)
    prepared = reader.post(123, prepare=lambda html: 'prepared')
    assert raw['html'] != prepared['html'] == 'prepared'
    assert all(comment['html'] == 'prepared' for comment in prepared['comments'])
    assert reader.post(123)['html'] == raw['html']


def test_parser_warning_has_key_no_body_and_stale_fallback(monkeypatch, clock, caplog):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'post.html').read_bytes())
    reader.post(123, 'hand')
    clock.now += 31
    monkeypatch.setattr(reader, '_fetch', lambda path: b'<p>SECRET_BODY</p>')
    assert reader.post(123, 'hand')['stale']
    assert "('post', 'hand', 123)" in caplog.text
    assert 'structure parse failed' in caplog.text and 'SECRET_BODY' not in caplog.text


def test_empty_first_page_warns_but_later_empty_page_does_not(monkeypatch, caplog):
    reader = pg.Reader()
    raw = '<title>뉴스 게시판</title><div class="grid-cols-2 gap-y-4"></div>'.encode()
    monkeypatch.setattr(reader, '_fetch', lambda path: raw)
    assert reader.board(2, 'news')['posts'] == []
    assert not caplog.records
    assert reader.board(1, 'news')['posts'] == []
    assert "('board', 'news', 1)" in caplog.text


def test_comment_page_structure_warning_includes_board_pid_page(monkeypatch, caplog):
    reader = pg.Reader()
    monkeypatch.setattr(reader, '_fetch', lambda path: (FIXTURES / 'comments-page-2.html').read_bytes())
    with pytest.raises(pg.PokerError):
        reader.comment_page(123, 1, 'hand')
    assert "('comments', 'hand', 123, 1)" in caplog.text


def test_list_json_passes_stale_marker(monkeypatch):
    monkeypatch.setattr(poker_routes.reader, 'board',
                        lambda *args, **kwargs: {'posts': [], 'has_next': False, 'stale': True})
    response = create_app().test_client().get('/poker/free/list?page=1')
    assert response.status_code == 200 and response.json['stale'] is True
