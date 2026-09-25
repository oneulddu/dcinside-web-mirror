from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import lxml.html
import pytest

from app import create_app, routes
from app.services import core, recent
from app.services.dc.api import API


PC_HEADING = '''<h2><a href="https://gall.dcinside.com/mgallery/board/lists/?id=cloudgame">클라우드 게임 갤러리<div class="pagehead_titicon mgall sp_img"><span class="blind">마이너</span></div></a></h2>'''
MOBILE_HEADING = '''<section class="gall-tit-group"><div class="gall-tit-box">
<a href="/board/cloudgame" class="gall-tit-lnkempty"></a>
<h3 class="gall-tit"><a href="/board/cloudgame" class="gall-tit-lnk"> 클라우드 게임 </a></h3>
<span class="mgall-tit"><em class="blind">마이너</em></span><span class="count">(17,230)</span></div></section>'''
BOARD_BODY = '''<table><tr class="ub-content us-post" data-no="123">
<td class="gall_tit"><a href="/mgallery/board/view/?id=cloudgame&no=123">글 제목</a></td>
<td class="gall_writer" data-nick="작성자" data-ip="1.2"></td>
<td class="gall_date" title="2026.09.09 12:00:00"></td>
</tr></table>'''
READ_BODY = '''<div class="gallview_head"><span class="title_subject">글 제목</span>
<span class="nickname">작성자</span><span class="gall_date">2026.09.09 12:00:00</span></div>
<div class="writing_view_box"><p>본문</p></div>'''


@pytest.fixture(autouse=True)
def isolate_caches(monkeypatch):
    monkeypatch.setattr(core, "BOARD_FILL_AUTHOR_CODES", False)
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 0))
    with core._BOARD_INDEX_CACHE_LOCK:
        core._BOARD_INDEX_CACHE.clear()
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    yield
    with core._BOARD_INDEX_CACHE_LOCK:
        core._BOARD_INDEX_CACHE.clear()
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()


@pytest.mark.parametrize("heading, expected", [
    (PC_HEADING, "클라우드 게임"),
    (MOBILE_HEADING, "클라우드 게임"),
    ('<h2><a href="/board/cloudgame"><span>클라우드 게임</span></a></h2>', "클라우드 게임"),
    ('<h3><a class="gall-tit-lnk" href="/mini/cloudgame">클라우드 게임 미니 갤러리</a></h3>', "클라우드 게임"),
    ('<h2><a href="/mgallery/board/lists/?id=cloudgame">English Name 갤러리</a></h2>', "English Name"),
    ('<h2><a href="/mgallery/board/lists/?id=other">다른 갤러리</a></h2>', None),
    ('<h2><a href="/board/cloudgame/123">글 제목</a></h2>', None),
    ('<h2><a href="https://example.com/board/cloudgame">다른 사이트</a></h2>', None),
    ('<title>글 제목 - 클라우드 게임 갤러리</title><h2>글 제목</h2>', None),
])
def test_extract_gallery_name_only_from_matching_gallery_heading(heading, expected):
    parsed = lxml.html.fromstring("<html>" + heading + "</html>")
    api = API.__new__(API)
    assert api._API__parse_gallery_name(parsed, "cloudgame") == expected


def install_upstream(monkeypatch, heading=PC_HEADING):
    api = API.__new__(API)
    calls = []

    async def fetch(urls, **kwargs):
        is_read = any("/view/" in url for url in urls)
        body = READ_BODY if is_read else BOARD_BODY
        text = "<html><body>" + heading + body + "</body></html>"
        calls.append(is_read)
        url = "https://gall.dcinside.com/mgallery/board/" + ("view/" if is_read else "lists/") + "?id=cloudgame"
        return lxml.html.fromstring(text), text, url

    async def comments(*args, **kwargs):
        if False:
            yield

    api._API__fetch_parsed_from_urls = fetch
    api.comments = comments

    @asynccontextmanager
    async def context():
        yield api

    async def read_payload(pid, board, **kwargs):
        return await core._read_document_with_api(api, str(pid), board, **kwargs)

    monkeypatch.setattr(core, "dc_api_context", context)
    monkeypatch.setattr(routes, "async_read", read_payload)
    return calls


@pytest.mark.parametrize("path", ["/board", "/read"])
@pytest.mark.parametrize("recommend", [0, 1])
@pytest.mark.parametrize("url_name", [None, "cloudgame", "예전 이름"])
def test_revisit_repairs_name_from_upstream_and_survives_worker_change(monkeypatch, path, recommend, url_name):
    calls = install_upstream(monkeypatch)
    client = create_app().test_client()
    old_row = {"board": "cloudgame", "name": "cloudgame", "kind": "minor", "recommend": recommend, "visited_at": 1}
    client.set_cookie(recent.RECENT_COOKIE_NAME, recent._encode_recent_rows([old_row]))
    query = {"board": "cloudgame", "kind": "minor", "recommend": recommend, "pid": 123}
    if url_name is not None:
        query["gallery_name"] = url_name
    response = client.get(path, query_string=query)
    assert response.status_code == 200
    assert "클라우드 게임" in response.get_data(as_text=True)
    with recent.RECENT_SERVER_CACHE_LOCK:
        recent.RECENT_SERVER_CACHE.clear()
    history = client.get("/recent")
    links = BeautifulSoup(history.data, "html.parser").select("a.feed-item")
    assert len(links) == 1
    assert links[0].select_one(".feed-title").get_text(strip=True) == "클라우드 게임"
    params = parse_qs(urlparse(links[0]["href"]).query)
    assert params["board"] == ["cloudgame"]
    # 추천글 목록에서 방문해도 최근 게시판은 전체 목록으로 연다.
    assert params["recommend"] == ["0"]
    assert params["kind"] == ["minor"]
    assert params["gallery_name"] == ["클라우드 게임"]
    if path == "/board":
        # A second visitor must receive the name from the shared board cache.
        second = create_app().test_client().get(path, query_string=query)
        assert "클라우드 게임" in second.get_data(as_text=True)
        assert calls == [False]


@pytest.mark.parametrize("path", ["/board", "/read"])
def test_missing_upstream_name_keeps_known_name(monkeypatch, path):
    install_upstream(monkeypatch, heading="")
    client = create_app().test_client()
    response = client.get(path, query_string={"board": "cloudgame", "kind": "minor", "pid": 123, "gallery_name": "기존 이름"})
    assert response.status_code == 200
    assert "기존 이름" in client.get("/recent").get_data(as_text=True)


@pytest.mark.parametrize("recommend", [0, 1])
def test_empty_board_still_repairs_gallery_name(monkeypatch, recommend):
    api = API.__new__(API)

    async def fetch(urls, **kwargs):
        text = "<html>" + MOBILE_HEADING + "<p>등록된 게시물이 없습니다.</p></html>"
        return lxml.html.fromstring(text), text, urls[0]

    api._API__fetch_parsed_from_urls = fetch

    @asynccontextmanager
    async def context():
        yield api

    monkeypatch.setattr(core, "dc_api_context", context)
    client = create_app().test_client()
    response = client.get("/board", query_string={"board": "cloudgame", "kind": "minor", "recommend": recommend})
    assert response.status_code == 200
    assert "클라우드 게임" in client.get("/recent").get_data(as_text=True)


@pytest.mark.parametrize("board_length", [12, 80])
def test_large_cookie_preserves_latest_name_without_server_cache(board_length):
    rows = [{"board": str(index).zfill(board_length), "name": "갤러리 이름 " + str(index) * 20,
             "kind": "minor", "recommend": index % 2, "visited_at": 1800000000.123 - index}
            for index in range(recent.RECENT_MAX_ITEMS)]
    encoded = recent._fit_recent_cookie_value(rows)
    assert len(encoded.encode("ascii")) <= recent.RECENT_COOKIE_MAX_BYTES
    client = create_app().test_client()
    client.set_cookie(recent.RECENT_COOKIE_NAME, encoded)
    history = BeautifulSoup(client.get("/recent").data, "html.parser")
    assert history.select_one(".feed-title").get_text(strip=True) == rows[0]["name"]
    assert all(row.get("name") for row in rows)  # Packing must not mutate server data.
