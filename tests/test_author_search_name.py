"""Original upstream names survive display anonymization and author backfill."""
from unittest.mock import AsyncMock

import lxml.html
import pytest

from app import routes
from app.services import core
from app.services.dc.api import API
from app.services.dc.models import Comment, Document


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(core, '_AUTHOR_CODE_CACHE', {})
    return API.__new__(API)


@pytest.mark.parametrize(('name', 'display', 'search'), [
    ('ㅇㅇ(123.45)', '익명', 'ㅇㅇ'),
    # 작성자 코드를 함께 확인하지 못하면 괄호를 닉네임 일부로 본다.
    ('고정닉(uid123)', '고정닉', '고정닉(uid123)'),
    ('ㅇㅇ123', '익명123', 'ㅇㅇ123'),
    ('테스트갤러', '익명', '테스트갤러'),
    ('익명', '익명', '익명'),
    ('   ', '익명', None),
    (None, '익명', None),
])
@pytest.mark.parametrize('source', ['mobile', 'pc', 'legacy'])
def test_board_names_and_related_json(api, name, display, search, source):
    writer = '' if name is None else name
    if source == 'pc':
        node = lxml.html.fromstring(f'''<tr data-no="123">
            <td class="gall_tit"><a href="view/?no=123">제목</a></td>
            {'<td class="gall_writer">' + writer + '</td>' if name is not None else ''}
            </tr>''')
        item = api._API__parse_pc_board_row(node, 'test')
    else:
        node = lxml.html.fromstring(f'''<li><a href="/board/test/123">
            <div><span class="sp-lst-img"></span><span class="subjectin">제목</span></div>
            <ul class="ginfo"><li>{writer}</li><li>12:30</li><li>조회 1</li><li><span>1</span></li></ul>
            </a></li>''')
        parser = api._API__parse_mobile_list_item if source == 'mobile' else api._API__parse_legacy_mobile_board_row
        item = parser(node, 'test')
    assert item.author_search_name == search
    row = core._index_item_to_dict(item)
    assert row['author'] == display
    assert row['author_search_name'] == search
    assert routes._serialize_related_posts([row])[0]['author_search_name'] == search


@pytest.mark.parametrize('source', ['mobile', 'pc'])
@pytest.mark.parametrize('name', ['ㅇㅇ(123.45)', '고정닉(uid123)', '익명', None])
def test_comments(api, source, name):
    if source == 'mobile':
        node = lxml.html.fromstring(f'<li no="1"><div><span class="nick">{name or ""}</span></div><p>댓글</p><span>12:30</span></li>')
        comment = api._API__parse_mobile_comment_li(node)
    else:
        comment = api._API__parse_pc_comment({'no': '1', 'name': name, 'memo': '댓글'})
    expected = name.split('(')[0] if name and name.endswith('.45)') else name
    if name == '익명':
        expected = '익명'
    assert comment.author_search_name == expected
    assert core._comment_to_dict(comment)['author_search_name'] == expected


@pytest.mark.parametrize(('raw', 'expected'), [
    ('닉(부캐)', '닉(부캐)'),
    ('(ㅇㅇ)', '(ㅇㅇ)'),
    ('abc(def)', 'abc(def)'),
    ('ㅇㅇ(1.2)', 'ㅇㅇ'),
    ('고정닉(uid_1)', '고정닉(uid_1)'),
])
def test_nickname_parentheses_survive(api, raw, expected):
    comment = api._API__parse_pc_comment({'no': '1', 'name': raw, 'memo': '댓글'})
    assert comment.author_search_name == expected


@pytest.mark.parametrize(('raw', 'user_id', 'expected'), [
    ('고정닉(uid_1)', 'uid_1', '고정닉'),
    ('abc(def)', 'uid_1', 'abc(def)'),
    ('(uid_1)', 'uid_1', '(uid_1)'),
])
def test_comment_strips_only_confirmed_author_code(api, raw, user_id, expected):
    comment = api._API__parse_pc_comment({'no': '1', 'name': raw, 'user_id': user_id, 'memo': '댓글'})
    assert comment.author_search_name == expected


def test_mobile_comment_strips_confirmed_block_id(api):
    node = lxml.html.fromstring('<li no="1"><div><span class="nick">고정닉(uid9)</span>'
                                '<span class="blockCommentId" data-info="uid9"></span></div><p>댓글</p><span>12:30</span></li>')
    assert api._API__parse_mobile_comment_li(node).author_search_name == '고정닉'


def test_pc_writer_text_strips_only_matching_uid(api):
    def row(text, uid):
        return lxml.html.fromstring(f'''<tr data-no="123">
            <td class="gall_tit"><a href="view/?no=123">제목</a></td>
            <td class="gall_writer" data-uid="{uid}">{text}</td></tr>''')
    assert api._API__parse_pc_board_row(row('고정닉(uid1)', 'uid1'), 'test').author_search_name == '고정닉'
    assert api._API__parse_pc_board_row(row('abc(def)', 'uid1'), 'test').author_search_name == 'abc(def)'


@pytest.mark.asyncio
@pytest.mark.parametrize(('name', 'expected'), [('고정닉(uid7)', '고정닉'), ('abc(def)', 'abc(def)'), ('(abc)', '(abc)')])
async def test_document_strips_only_gallog_confirmed_code(api, monkeypatch, name, expected):
    html = (f'<html><body><div class="gallview-tit-box"><span class="tit">제목</span>'
            f'<ul class="ginfo2"><li><a href="/gallog/uid7">{name}</a></li></ul></div>'
            '<div class="writing_view_box"><p>본문</p></div></body></html>')
    url = 'https://m.dcinside.com/board/test/123'
    monkeypatch.setattr(api, '_API__fetch_parsed_from_urls', AsyncMock(return_value=(lxml.html.fromstring(html), html, url)))

    async def comments(*args, **kwargs):
        if False:
            yield

    monkeypatch.setattr(api, 'comments', comments)
    doc = await api.document('test', '123')
    assert doc.author_search_name == expected


@pytest.mark.parametrize('nick', ['닉(부캐)', '(ㅇㅇ)', 'abc(def)'])
def test_pc_data_nick_is_kept_verbatim(api, nick):
    node = lxml.html.fromstring(f'''<tr data-no="123">
        <td class="gall_tit"><a href="view/?no=123">제목</a></td>
        <td class="gall_writer" data-nick="{nick}" data-uid="uid1">{nick}</td>
        </tr>''')
    item = api._API__parse_pc_board_row(node, 'test')
    assert item.author_search_name == nick


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['mobile', 'pc'])
@pytest.mark.parametrize('name', ['ㅇㅇ(123.45)', '고정닉(uid123)', '익명', None])
async def test_document_payload_and_backfill(api, monkeypatch, source, name):
    if source == 'mobile':
        writer = f'<ul class="ginfo2"><li>{name}</li></ul>' if name else ''
        header = f'<div class="gallview-tit-box"><span class="tit">제목</span>{writer}</div>'
        url = 'https://m.dcinside.com/board/test/123'
    else:
        writer = f'<span class="nickname">{name}</span>' if name else ''
        header = f'<div class="gallview_head"><span class="title_subject">제목</span>{writer}</div>'
        url = 'https://gall.dcinside.com/board/view/?id=test&no=123'
    html = f'<html><body>{header}<div class="writing_view_box"><p>본문</p></div></body></html>'
    monkeypatch.setattr(api, '_API__fetch_parsed_from_urls', AsyncMock(return_value=(lxml.html.fromstring(html), html, url)))

    async def comments(*args, **kwargs):
        if False:
            yield

    monkeypatch.setattr(api, 'comments', comments)
    doc = await api.document('test', '123')
    expected = name.split('(')[0] if name and name.endswith('.45)') else name
    assert doc.author_search_name == expected
    data, _, _ = await core._read_document_with_api(api, '123', 'test')
    assert data['author_search_name'] == expected
    assert data['author'] == ('고정닉' if name and name.startswith('고정닉') else '익명')
    monkeypatch.setattr(api, 'document', AsyncMock(side_effect=AssertionError('cache should avoid fetching')))
    row = await core._fill_missing_author_code(api, 'test', None, {'id': '123'})
    assert row['author_search_name'] == expected


@pytest.mark.asyncio
async def test_fetch_backfill_cache_roundtrip(api, monkeypatch):
    doc = Document('123', 'test', '제목', 'ㅇㅇ(123.45)', None, '', [], '', 0, 0, 0, 0, '-', None,
                   author_search_name='ㅇㅇ')
    fetch = AsyncMock(return_value=doc)
    monkeypatch.setattr(api, 'document', fetch)
    for _ in range(2):
        row = await core._fill_missing_author_code(api, 'test', None, {'id': '123'})
        assert row['author'] == '익명'
        assert row['author_code'] == '123.45'
        assert row['author_search_name'] == 'ㅇㅇ'
    assert fetch.await_count == 1


def test_old_constructor_defaults():
    comment = Comment('1', None, '닉', None, '', None, None, '-')
    document = Document('1', 'test', '제목', '닉', None, '', [], '', 0, 0, 0, 0, '-', None)
    assert comment.author_search_name is document.author_search_name is None


@pytest.mark.parametrize('parser_name', ['_API__parse_mobile_list_item', '_API__parse_legacy_mobile_board_row'])
def test_missing_mobile_name_node_is_not_searchable(api, parser_name):
    node = lxml.html.fromstring('<li><a href="/board/test/123"><span class="subjectin">제목</span></a></li>')
    item = getattr(api, parser_name)(node, 'test')
    assert item.author == '익명'
    assert item.author_search_name is None
    assert core._index_item_to_dict(item)['author_search_name'] is None


@pytest.mark.asyncio
async def test_backfill_without_name_preserves_existing_real_name(api, monkeypatch):
    core._cache_author_code('test', None, '123', '익명', '123.45')
    monkeypatch.setattr(api, 'document', AsyncMock(side_effect=AssertionError('unexpected fetch')))
    row = {'id': '123', 'author': '익명', 'author_search_name': 'ㅇㅇ'}
    result = await core._fill_missing_author_code(api, 'test', None, row)
    assert result['author_search_name'] == 'ㅇㅇ'
    assert result['author_code'] == '123.45'
