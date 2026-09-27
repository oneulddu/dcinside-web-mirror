"""Read-only Pokergosu board routes; DC contracts stay intact."""
from flask import Blueprint, abort, jsonify, make_response, render_template, request, url_for, redirect
from werkzeug.exceptions import HTTPException

from .services.pokergosu import PokerError, reader
from .services.poker_boards import BASE_URL, MAX_PAGE, BOARDS
from .services.poker_media import build_image_response, prepare_html

bp = Blueprint('poker', __name__, url_prefix='/poker')


def _page():
    value = request.args.get('page', '1')
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1 <= int(value) <= MAX_PAGE:
        abort(400)
    return int(value)


def _context(board_id):
    if board_id not in BOARDS:
        abort(404)
    return dict(board_id=board_id, board_name='포커고수 ' + BOARDS[board_id]['label'],
                boards=[dict(id=key, label=value['label'], login_required=value.get('login_required', False))
                        for key, value in BOARDS.items()])


def _error(error, source_url, page, board_id):
    return_url = url_for('poker.board', board_id=board_id, page=page)
    if request.endpoint == 'poker.board':
        return_url = url_for('poker.board', board_id=board_id, page=1) if page > 1 else url_for('poker.index')
    response = make_response(render_template('poker/error.html', title='포커고수 · 숨터', message=str(error),
        status=error.status, source_url=source_url, return_url=return_url,
        retry_url=request.path + ('?page=' + str(page)), **_context(board_id)), error.status)
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
    return render_template('poker/board.html', title=context['board_name'] + ' · 숨터', data=data,
                           page=page, source_url=source_url, **context)


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
        current_pid = request.args.get('current_pid')
        if current_pid is not None:
            if (not current_pid.isascii() or not current_pid.isdecimal()
                    or not 1 <= len(current_pid) <= 12 or int(current_pid) < 1):
                abort(400)
            current_pid = int(current_pid)
    except HTTPException as exc:
        return _list_response({'error': '게시판을 찾을 수 없어요.' if exc.code == 404
                               else '목록 요청을 확인해 주세요.'}, exc.code)
    try:
        data = reader.board(page, board_id=board_id)
    except PokerError as exc:
        return _list_response({'error': str(exc)}, exc.status)
    fragment = render_template('poker/_post_list.html', data=data, page=page,
                               current_pid=current_pid, mode='read', **context)
    return _list_response({'html': fragment, 'page': page})


@bp.get('/<board_id>/<int:pid>')
def read(board_id, pid):
    context = _context(board_id)
    page = _page()
    if not 1 <= pid <= 999999999999:
        abort(400)
    source_url = f'{BASE_URL}/{board_id}/{pid}'
    try:
        data = reader.post(pid, board_id=board_id)
    except PokerError as exc:
        return _error(exc, source_url, page, board_id)
    data['html'] = prepare_html(data['html'], base_url=source_url)
    for comment in data['comments']:
        comment['html'] = prepare_html(comment['html'], base_url=source_url)
    return render_template('poker/read.html', title=data['title'] + ' · 숨터', data=data,
                           page=page, source_url=source_url, **context)


@bp.get('/media')
def media():
    return build_image_response(request.args.get('src', ''), request.args.get('sig', ''))
