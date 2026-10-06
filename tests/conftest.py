import os
import json

import pytest


os.environ.setdefault("MIRROR_ENV", "development")


def poker_comment_api_json(raw, pid=123):
    """Re-shape a Pokergosu post HTML fixture into the upstream comment JSON page."""
    from lxml import etree, html as lxml_html
    from app.services import pokergosu as pg

    data = pg.parse_post(raw, pid)
    nodes, top = {}, []
    for comment in data['comments']:
        board = lxml_html.fromstring(comment['html'])
        spans = board.xpath('./a[@aria-label="title"]/span')
        source = spans[0] if spans else board
        content = (source.text or '') + ''.join(
            etree.tostring(child, encoding='unicode', method='html') for child in source)
        node = {'comment_srl': int(comment['id'][1:]), 'content': content,
                'nick_name': comment['author'] or '',
                'regdate': ''.join(ch for ch in comment['time'] if ch.isdigit()), 'children': []}
        nodes[comment['id']] = node
        parent = nodes.get(comment['parent_id'])
        (parent['children'] if parent else top).append(node)
    payload = {'comments': top, 'c_count2': len(top),
               'postinfo2': {'postinfo': [{'post_srl': pid, 'comment_count': data['comment_count']}]}}
    return json.dumps(payload, ensure_ascii=False).encode()


@pytest.fixture
def poker_upstream():
    """Fetch stub: post/list paths get the HTML fixture, comment API paths get its JSON form."""
    def build(raw, pid=123, calls=None):
        def fetch(path):
            if calls is not None:
                calls.append(path)
            if path.startswith('/api2/board/getcommnet/'):
                return poker_comment_api_json(raw, pid)
            return raw
        return fetch
    return build
