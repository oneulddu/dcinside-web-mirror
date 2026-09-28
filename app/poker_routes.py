"""Read-only Pokergosu board routes; DC contracts stay intact."""
from functools import partial
from urllib.parse import urlencode

from flask import Blueprint, abort, jsonify, make_response, render_template, request, url_for, redirect
from werkzeug.exceptions import HTTPException

from .services.pokergosu import PokerError, reader
from .services.poker_boards import BASE_URL, MAX_PAGE, BOARDS, search_types, validate_search
from .services.poker_media import build_image_response, prepare_html

bp = Blueprint('poker', __name__, url_prefix='/poker')


def _page():
    value = request.args.get('page', '1')
    if (len(request.args.getlist('page')) > 1 or not value.isascii() or not value.isdecimal()
            or len(value) > 5 or not 1 <= int(value) <= MAX_PAGE):
        abort(400)
    return int(value)


def _context(board_id):
    if board_id not in BOARDS:
        abort(404)
    return dict(board_id=board_id, board_name='포커고수 ' + BOARDS[board_id]['label'],
                boards=[dict(id=key, label=value['label'], login_required=value.get('login_required', False))
                        for key, value in BOARDS.items()])


def _search(board_id, *, required=False):
    if not required and not any(key in request.args for key in ('s', 'v')):
        return None
    if any(len(request.args.getlist(key)) > 1 for key in ('s', 'v', 'page')):
        raise ValueError('검색 요청을 확인해 주세요.')
    return validate_search(board_id, request.args.get('s', '1'), request.args.get('v', ''))


def _error(error, source_url, page, board_id, search=None):
    params = dict(page=page)
    if search:
        params.update(s=search['s'], v=search['v'])
    return_url = url_for('poker.search' if search else 'poker.board', board_id=board_id, **params)
    if request.endpoint == 'poker.board':
        return_url = url_for('poker.board', board_id=board_id, page=1) if page > 1 else url_for('poker.index')
    response = make_response(render_template('poker/error.html', title='포커고수 · 숨터', message=str(error),
        status=error.status, source_url=source_url, return_url=return_url,
        retry_url=request.path + '?' + urlencode(params), search=search, **_context(board_id)), error.status)
    response.headers['Cache-Control'] = 'no-store'
    if error.status == 503:
        response.headers['Retry-After'] = '60'
    return response


@bp.get('')
@bp.get('/')
def index():
    return redirect(url_for('poker.board', board_id='free', page=1))


@bp.get('/<board_id>')
def board(board_id):
    context = _context(board_id)
    page = _page()
    source_url = f'{BASE_URL}/{board_id}?page={page}'
    try:
        data = reader.board(page, board_id=board_id)
    except PokerError as exc:
        return _error(exc, source_url, page, board_id)
    response = make_response(render_template('poker/board.html', title=context['board_name'] + ' · 숨터', data=data,
                           page=page, source_url=source_url, search=None, search_types=search_types(board_id),
                           search_error=None, **context))
    return response


@bp.get('/<board_id>/search')
def search(board_id):
    context = _context(board_id)
    types = search_types(board_id)
    raw_s = request.args.get('s', '1')
    selected = next((item for item in types if str(item['value']) == raw_s), types[0])
    if len(request.args.getlist('s')) > 1:
        selected = types[0]
    search = dict(s=selected['value'], v=request.args.get('v', '').strip(), label=selected['label'])
    page, search_error = 1, None
    try:
        page = _page()
        search = _search(board_id, required=True)
    except (ValueError, HTTPException) as exc:
        search_error = str(exc) if isinstance(exc, ValueError) else '페이지를 확인해 주세요.'
    source_url = f'{BASE_URL}/{board_id}/search?' + urlencode(dict(s=search['s'], v=search['v'], page=page))
    if BOARDS[board_id].get('login_required'):
        return _error(PokerError('원본 로그인이 필요한 게시판이에요. 원문에서 확인해 주세요.', 403),
                      source_url, page, board_id, search)
    if search_error:
        response = make_response(render_template('poker/board.html', title=context['board_name'] + ' · 숨터',
            data=dict(posts=[], has_next=False), page=page, source_url=source_url, search=search,
            search_types=types, search_error=search_error, **context), 400)
        response.headers['Cache-Control'] = 'no-store'
        return response
    try:
        data = reader.search(page, board_id=board_id, s=search['s'], v=search['v'])
    except PokerError as exc:
        return _error(exc, source_url, page, board_id, search)
    response = make_response(render_template('poker/board.html', title=context['board_name'] + ' · 숨터',
        data=data, page=page, source_url=source_url, search=search, search_types=types, search_error=None, **context))
    return response


def _list_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if status == 503:
        response.headers['Retry-After'] = '60'
    return response


@bp.get('/<board_id>/list')
def post_list(board_id):
    try:
        context = _context(board_id)
        page = _page()
        search = _search(board_id)
        current_pid = request.args.get('current_pid')
        if current_pid is not None:
            if (not current_pid.isascii() or not current_pid.isdecimal()
                    or not 1 <= len(current_pid) <= 12 or int(current_pid) < 1):
                abort(400)
            current_pid = int(current_pid)
    except (HTTPException, ValueError) as exc:
        status = exc.code if isinstance(exc, HTTPException) else 400
        return _list_response({'error': '게시판을 찾을 수 없어요.' if status == 404
                               else '목록 요청을 확인해 주세요.'}, status)
    try:
        data = (reader.search(page, board_id=board_id, s=search['s'], v=search['v']) if search
                else reader.board(page, board_id=board_id))
    except PokerError as exc:
        return _list_response({'error': str(exc)}, exc.status)
    fragment = render_template('poker/_post_list.html', data=data, page=page,
                               current_pid=current_pid, mode='read', search=search, **context)
    payload = {'html': fragment, 'page': page}
    if data.get('stale'):
        payload['stale'] = True
    return _list_response(payload)


@bp.get('/<board_id>/<int:pid>')
def read(board_id, pid):
    context = _context(board_id)
    page = _page()
    try:
        search = _search(board_id)
    except ValueError:
        # 잘못된 검색 조건은 원본을 부르기 전에 떼어 내고 일반 글 주소로 보낸다.
        return redirect(url_for('poker.read', board_id=board_id, pid=pid, page=page))
    if not 1 <= pid <= 999999999999:
        abort(400)
    source_url = f'{BASE_URL}/{board_id}/{pid}'
    try:
        data = reader.post(pid, board_id=board_id, prepare=(
            partial(prepare_html, base_url=source_url, allow_youtube=True),
            partial(prepare_html, base_url=source_url)))
    except PokerError as exc:
        return _error(exc, source_url, page, board_id, search)
    response = make_response(render_template('poker/read.html', title=data['title'] + ' · 숨터', data=data,
                           page=page, source_url=source_url, search=search, **context))
    return response


@bp.get('/<board_id>/<int:pid>/comments')
def comments(board_id, pid):
    try:
        _context(board_id)
        value = request.args.get('cpage', '')
        if (not 1 <= pid <= 999999999999 or not value.isascii() or not value.isdecimal()
                or not 1 <= len(value) <= 5 or not 1 <= int(value) <= MAX_PAGE):
            abort(400)
        page = int(value)
    except HTTPException as exc:
        return _list_response({'error': '게시판을 찾을 수 없어요.' if exc.code == 404
                               else '댓글 요청을 확인해 주세요.'}, exc.code)
    source_url = f'{BASE_URL}/{board_id}/{pid}'
    try:
        data = reader.comment_page(pid, page, board_id=board_id,
                                   prepare=partial(prepare_html, base_url=source_url))
    except PokerError as exc:
        return _list_response({'error': str(exc)}, exc.status)
    rows = []
    for comment in data['comments']:
        rows.append({'id': comment['id'], 'html': render_template('poker/_comment.html', comment=comment)})
    payload = {'comments': rows, 'page': data['comment_page'],
               'next_page': data['comments_next_page'], 'total': data['comment_count']}
    if data.get('stale'):
        payload['stale'] = True
    return _list_response(payload)


@bp.get('/media')
def media():
    return build_image_response(request.args.get('src', ''), request.args.get('sig', ''))
