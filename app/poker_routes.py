"""Separate routes for the first Pokergosu board; DC contracts stay intact."""
from flask import Blueprint, abort, make_response, render_template, request, url_for

from .services.pokergosu import BASE_URL, MAX_PAGE, PokerError, reader
from .services.poker_media import build_image_response, prepare_html

bp = Blueprint('poker', __name__, url_prefix='/poker')


def _page():
    value = request.args.get('page', '1')
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1 <= int(value) <= MAX_PAGE:
        abort(400)
    return int(value)


def _error(error, source_url, page):
    return_url = url_for('poker.board', page=page)
    if request.endpoint == 'poker.board':
        return_url = url_for('poker.board', page=1) if page > 1 else '/'
    response = make_response(render_template('poker/error.html', title='포커고수 · 숨터', message=str(error),
        status=error.status, source_url=source_url, return_url=return_url,
        retry_url=request.path + ('?page=' + str(page))), error.status)
    response.headers['Cache-Control'] = 'no-store'
    if error.status == 503:
        response.headers['Retry-After'] = '60'
    return response


@bp.get('/free')
def board():
    page = _page()
    source_url = f'{BASE_URL}/free?page={page}'
    try:
        data = reader.board(page)
    except PokerError as exc:
        return _error(exc, source_url, page)
    return render_template('poker/board.html', title='포커고수 자유게시판 · 숨터', data=data,
                           page=page, source_url=source_url)


@bp.get('/free/<int:pid>')
def read(pid):
    page = _page()
    if not 1 <= pid <= 999999999999:
        abort(400)
    source_url = f'{BASE_URL}/free/{pid}'
    try:
        data = reader.post(pid)
    except PokerError as exc:
        return _error(exc, source_url, page)
    data['html'] = prepare_html(data['html'])
    for comment in data['comments']:
        comment['html'] = prepare_html(comment['html'])
    return render_template('poker/read.html', title=data['title'] + ' · 숨터', data=data,
                           page=page, source_url=source_url)


@bp.get('/media')
def media():
    return build_image_response(request.args.get('src', ''), request.args.get('sig', ''))
