"""Supported public board routes and safe interpretation of upstream links."""
import re
from urllib.parse import parse_qs, urljoin, urlsplit

BASE_URL = 'https://www.pokergosu.com'
MAX_PAGE = 10000
DEFAULT_BOARD = 'best'
BOARDS = {
    'best': {'label': '추천 게시판'},
    'free': {'label': '자유 게시판'},
    'hand': {'label': '핸드 게시판'},
    'grinding': {'label': '그라인딩 게시판'},
    'strategy': {'label': '전략/번역 게시판'},
    'news': {'label': '뉴스 게시판'},
    'buyboard': {'label': '공구 게시판'},
    'notice': {'label': '공지사항'},
    'groupbuy': {'label': '공동구매', 'login_required': True},
    'qna': {'label': '문의 게시판', 'login_required': True},
}


def search_types(board_id):
    labels = ('제목+내용', '제목', '내용', '댓글', '작성자')
    return [dict(value=i, label=label) for i, label in enumerate(labels, 1)
            if board_id != 'news' or i != 5]


def validate_search(board_id, s, v):
    """Validate decoded query values once, before fetching or building cache keys."""
    choices = {str(item['value']): item for item in search_types(board_id)}
    if str(s) not in choices:
        raise ValueError('검색 범위를 확인해 주세요.')
    v = str(v or '').strip()
    if not 2 <= len(v) <= 20:
        raise ValueError('검색어는 2~20자로 입력해 주세요.')
    return dict(s=choices[str(s)]['value'], v=v, label=choices[str(s)]['label'])


def poker_link(value, base_url=BASE_URL + '/'):
    """Return a validated board, optional post/page, and supported fragment."""
    try:
        parsed = urlsplit(urljoin(base_url, value or ''))
        if (parsed.scheme not in ('http', 'https')
                or parsed.hostname not in ('pokergosu.com', 'www.pokergosu.com')
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80, 443)):
            return None
        match = re.fullmatch(r'/([a-z]+)(?:/([1-9][0-9]{0,11}))?/?', parsed.path)
        if not match or match[1] not in BOARDS:
            return None
        page = None
        values = parse_qs(parsed.query).get('page', [])
        if len(values) == 1 and re.fullmatch(r'[0-9]{1,5}', values[0]) and 1 <= int(values[0]) <= MAX_PAGE:
            page = int(values[0])
        return dict(board_id=match[1], pid=int(match[2]) if match[2] else None,
                    page=page, fragment='comment' if parsed.fragment == 'comment' else '')
    except (TypeError, ValueError):
        return None


def is_login_redirect(location):
    try:
        parsed = urlsplit(urljoin(BASE_URL, location or ''))
        return (parsed.scheme in ('http', 'https')
                and parsed.hostname in ('www.pokergosu.com', 'pokergosu.com')
                and parsed.port in (None, 80, 443)
                and parsed.username is None and parsed.password is None
                and parsed.path.rstrip('/') == '/login')
    except ValueError:
        return False
