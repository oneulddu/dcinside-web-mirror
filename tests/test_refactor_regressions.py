import base64
import json
import socket
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from app import create_app, routes
from app.services import media_proxy


def _gallery_item(name="테스트 갤러리"):
    return {
        "rank": 1,
        "name": name,
        "board_id": "test",
        "board_kind": "minor",
        "internal_supported": True,
    }


def _board_item(post_id="321"):
    return {
        "id": post_id,
        "title": "검색 결과 글",
        "subject": "일반",
        "author": "익명",
        "author_code": None,
        "author_role": None,
        "time": "-",
        "time_display": "-",
        "needs_time_hydrate": False,
        "comment_count": 0,
        "voteup_count": 1,
        "has_image": False,
        "has_video": False,
        "isimage": False,
        "isvideo": False,
        "isrecommend": False,
    }


def _read_payload():
    return (
        {
            "title": "검색 결과 글",
            "author": "익명",
            "author_code": None,
            "author_role": None,
            "time": "-",
            "voteup_count": 1,
            "contents": "본문",
            "html": "<p>본문</p>",
            "related_posts": [],
        },
        [],
        [],
    )


def _cookie_names(response):
    return {
        header.split("=", 1)[0]
        for header in response.headers.getlist("Set-Cookie")
    }


def _recent_cookie_rows(response):
    header = next(
        value
        for value in response.headers.getlist("Set-Cookie")
        if value.startswith("recent_galleries=")
    )
    cookie = SimpleCookie()
    cookie.load(header)
    encoded = cookie["recent_galleries"].value
    padded = encoded + "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


@pytest.mark.parametrize(
    ("mode", "raises", "expected_text"),
    [
        ("list", False, "흥한 갤러리 1~1위"),
        ("list", True, "흥한 갤러리 목록을 가져오지 못했습니다."),
        ("search", False, "검색 결과 1건"),
        ("search", True, "갤러리 검색 결과를 가져오지 못했습니다."),
    ],
)
def test_index_list_and_search_make_one_external_call(monkeypatch, mode, raises, expected_text):
    calls = {"list": [], "search": []}

    def fake_list():
        calls["list"].append(())
        if raises and mode == "list":
            raise RuntimeError("list failed")
        return [_gallery_item("목록 갤러리")], 1_700_000_000

    def fake_search(query):
        calls["search"].append(query)
        if raises and mode == "search":
            raise RuntimeError("search failed")
        return [_gallery_item("검색 갤러리")]

    monkeypatch.setattr(routes, "get_heung_galleries", fake_list)
    monkeypatch.setattr(routes, "search_galleries", fake_search)
    app = create_app()

    path = "/?heung_q=%EA%B2%80%EC%83%89%EC%96%B4" if mode == "search" else "/"
    response = app.test_client().get(path)

    assert response.status_code == 200
    assert expected_text in response.get_data(as_text=True)
    assert calls == {
        "list": [()] if mode == "list" else [],
        "search": ["검색어"] if mode == "search" else [],
    }


@pytest.mark.parametrize(
    ("raw_kind", "expected_kind"),
    [("normal", None), ("minor", "minor"), ("mini", "mini"), ("person", "person")],
)
def test_board_and_read_forward_full_context_once_and_keep_html_cookie_contract(
    monkeypatch,
    raw_kind,
    expected_kind,
):
    board_calls = []
    read_calls = []
    recent_calls = []
    original_touch_recent = routes.touch_recent_gallery

    async def fake_board(page, board, recommend, kind=None, **kwargs):
        board_calls.append(
            {
                "page": page,
                "board": board,
                "recommend": recommend,
                "kind": kind,
                **kwargs,
            }
        )
        return [_board_item()], [], {} if kwargs.get("search_keyword") else None

    async def fake_read(pid, board, kind=None, recommend=0, head_id=None, **kwargs):
        read_calls.append(
            {
                "pid": pid,
                "board": board,
                "kind": kind,
                "recommend": recommend,
                "head_id": head_id,
                **kwargs,
            }
        )
        return _read_payload()

    def spy_touch_recent(response, board, kind, recommend=0, name=None):
        recent_calls.append(
            {
                "board": board,
                "kind": kind,
                "recommend": recommend,
                "name": name,
            }
        )
        return original_touch_recent(response, board, kind, recommend=recommend, name=name)

    monkeypatch.setattr(routes, "_load_board_payload", fake_board)
    monkeypatch.setattr(routes, "async_read", fake_read)
    monkeypatch.setattr(routes, "touch_recent_gallery", spy_touch_recent)
    app = create_app()
    client = app.test_client()
    common = {
        "board": "test",
        "kind": raw_kind,
        "recommend": "1",
        "headid": "17",
        "s_type": "comment",
        "serval": "검색어",
        "gallery_name": "테스트 갤러리",
    }

    board_response = client.get("/board", query_string={**common, "page": "3"})
    board_soup = BeautifulSoup(board_response.data, "html.parser")
    board_section = board_soup.select_one("#board-list")
    read_link_query = parse_qs(urlparse(board_soup.select_one("a.feed-item")["href"]).query)

    assert board_response.status_code == 200
    assert board_calls == [
        {
            "page": 3,
            "board": "test",
            "recommend": 1,
            "kind": expected_kind,
            "search_type": "comment",
            "search_keyword": "검색어",
            "head_id": "17",
            "search_pos": None,
        }
    ]
    assert board_section["data-kind"] == (expected_kind or "")
    assert board_section["data-recommend"] == "1"
    assert board_section["data-head-id"] == "17"
    assert board_section["data-search-type"] == "comment"
    assert board_section["data-search-keyword"] == "검색어"
    assert board_soup.select_one(".masthead-board-head h1").get_text(strip=True) == "테스트 갤러리 게시판"
    assert read_link_query["recommend"] == ["1"]
    assert read_link_query["source_page"] == ["3"]
    assert read_link_query["headid"] == ["17"]
    assert read_link_query["s_type"] == ["comment"]
    assert read_link_query["serval"] == ["검색어"]
    assert read_link_query["gallery_name"] == ["테스트 갤러리"]
    assert read_link_query.get("kind") == ([expected_kind] if expected_kind else None)
    assert {"recent_galleries", "recent_galleries_key"} <= _cookie_names(board_response)
    assert recent_calls == [
        {
            "board": "test",
            "kind": expected_kind,
            "recommend": 1,
            "name": "테스트 갤러리",
        }
    ]
    assert _recent_cookie_rows(board_response)[0] | {"visited_at": None} == {
        "board": "test",
        "name": "테스트 갤러리",
        "kind": expected_kind,
        "recommend": 1,
        "visited_at": None,
    }

    read_response = client.get(
        "/read",
        query_string={**common, "pid": "321", "source_page": "4"},
    )
    read_soup = BeautifulSoup(read_response.data, "html.parser")
    related = read_soup.select_one("#related-section")
    crumb_query = parse_qs(urlparse(read_soup.select_one(".crumb-link")["href"]).query)
    canonical_query = parse_qs(urlparse(read_soup.select_one('meta[property="og:url"]')["content"]).query)

    assert read_response.status_code == 200
    assert read_calls == [
        {
            "pid": 321,
            "board": "test",
            "kind": expected_kind,
            "recommend": 1,
                "head_id": "17",
                "search_type": "comment",
                "search_keyword": "검색어",
                "search_pos": None,
            }
    ]
    assert related["data-kind"] == (expected_kind or "")
    assert related["data-recommend"] == "1"
    assert related["data-source-page"] == "4"
    assert related["data-head-id"] == "17"
    assert related["data-search-type"] == "comment"
    assert related["data-search-keyword"] == "검색어"
    assert related["data-gallery-name"] == "테스트 갤러리"
    assert crumb_query["page"] == ["4"]
    assert crumb_query["gallery_name"] == ["테스트 갤러리"]
    assert crumb_query.get("kind") == ([expected_kind] if expected_kind else None)
    assert canonical_query["source_page"] == ["4"]
    assert canonical_query.get("kind") == ([expected_kind] if expected_kind else None)
    assert {"recent_galleries", "recent_galleries_key"} <= _cookie_names(read_response)
    assert recent_calls == [
        {
            "board": "test",
            "kind": expected_kind,
            "recommend": 1,
            "name": "테스트 갤러리",
        },
        {
            "board": "test",
            "kind": expected_kind,
            "recommend": 1,
            "name": "테스트 갤러리",
        },
    ]
    assert _recent_cookie_rows(read_response)[0]["name"] == "테스트 갤러리"


@pytest.mark.parametrize("route_name", ["board", "read"])
def test_board_and_read_upstream_500_does_not_set_recent_cookie(monkeypatch, route_name):
    calls = []

    async def explode(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("upstream failed")

    if route_name == "board":
        monkeypatch.setattr(routes, "_load_board_payload", explode)
        path = "/board?board=test&kind=mini&gallery_name=fail"
    else:
        monkeypatch.setattr(routes, "async_read", explode)
        path = "/read?board=test&pid=321&kind=mini&gallery_name=fail"

    app = create_app()
    app.config.update(DEBUG=False, TESTING=False, PROPAGATE_EXCEPTIONS=False)
    response = app.test_client().get(path)

    assert response.status_code == 500
    assert len(calls) == 1
    assert {"recent_galleries", "recent_galleries_key"}.isdisjoint(_cookie_names(response))


def _related_item(post_id):
    return {
        **_board_item(str(post_id)),
        "title": f"글 {post_id}",
        "source_page": 6,
    }


def test_read_related_keeps_cursor_order_uniqueness_limit_and_end_state(monkeypatch):
    calls = []

    async def fake_related(
        pid,
        after_pid,
        board,
        kind=None,
        limit=12,
        source_page=0,
        recommend=0,
        head_id=None,
        **kwargs,
    ):
        calls.append(
            {
                "pid": pid,
                "after_pid": after_pid,
                "board": board,
                "kind": kind,
                "limit": limit,
                "source_page": source_page,
                "recommend": recommend,
                "head_id": head_id,
                **kwargs,
            }
        )
        if after_pid == 100:
            return [_related_item(99), _related_item(98)], True, None
        return [_related_item(97)], False, None

    monkeypatch.setattr(routes, "async_related_after_position", fake_related)
    app = create_app()
    client = app.test_client()
    common = {
        "board": "test",
        "pid": "100",
        "kind": "mini",
        "recommend": "1",
        "source_page": "6",
        "headid": "9",
        "s_type": "subject",
        "serval": "검색어",
    }

    first = client.get(
        "/read/related",
        query_string={**common, "after_pid": "100", "limit": "2"},
    ).get_json()
    last = client.get(
        "/read/related",
        query_string={**common, "after_pid": "98", "limit": "999"},
    ).get_json()
    ids = [item["id"] for item in first["items"] + last["items"]]

    assert first["ok"] is True and first["has_more"] is True
    assert last["ok"] is True and last["has_more"] is False
    assert ids == ["99", "98", "97"]
    assert len(ids) == len(set(ids))
    assert calls == [
        {
            "pid": 100,
            "after_pid": 100,
            "board": "test",
            "kind": "mini",
            "limit": 2,
            "source_page": 6,
                "recommend": 1,
                "head_id": "9",
                "search_pos": None,
                "search_type": "subject",
            "search_keyword": "검색어",
        },
        {
            "pid": 100,
            "after_pid": 98,
            "board": "test",
            "kind": "mini",
            "limit": 30,
            "source_page": 6,
                "recommend": 1,
                "head_id": "9",
                "search_pos": None,
                "search_type": "subject",
            "search_keyword": "검색어",
        },
    ]


def test_read_related_empty_pid_skips_upstream(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("empty pid must not call upstream")

    monkeypatch.setattr(routes, "async_related_after_position", fail_if_called)
    response = create_app().test_client().get("/read/related?board=test&pid=")

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "items": [],
        "has_more": False,
        "next_s_pos": None,
    }


def test_read_related_upstream_error_is_502_once(monkeypatch):
    calls = []

    async def explode(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("related failed")

    monkeypatch.setattr(routes, "async_related_after_position", explode)
    response = create_app().test_client().get("/read/related?board=test&pid=100&after_pid=99")

    assert response.status_code == 502
    assert response.get_json() == {
        "ok": False,
        "items": [],
        "error": "related_fetch_failed",
    }
    assert len(calls) == 1
    assert {"recent_galleries", "recent_galleries_key"}.isdisjoint(_cookie_names(response))


class _MediaResponse:
    def __init__(self, body=b"", status_code=200, headers=None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False
        self.iterated = 0

    @property
    def is_redirect(self):
        return self.status_code in {301, 302, 303, 307, 308} and bool(self.headers.get("Location"))

    def iter_content(self, chunk_size=1):
        self.iterated += 1
        yield self.body

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "src",
    [
        "data:image/png;base64,AA",
        "https://user:secret@images.dcinside.com/a.jpg",
        "https://images.dcinside.com.evil.example/a.jpg",
        "https://evil-dcinside.com/a.jpg",
        "https://images.dcinside.com:bad/a.jpg",
    ],
)
def test_media_rejects_invalid_shape_userinfo_and_suffix_spoof_before_fetch(monkeypatch, src):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid URL must not reach media fetch")

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    response = create_app().test_client().get("/media", query_string={"src": src})

    assert response.status_code == 400
    assert calls == []


def test_media_rejects_private_dns_before_opening_session(monkeypatch):
    dns_calls = []

    def fake_getaddrinfo(host, port, **kwargs):
        dns_calls.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.7", port))]

    def fail_session():
        raise AssertionError("private DNS target must not open an HTTP session")

    monkeypatch.setattr(media_proxy.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(media_proxy, "_media_http_session", fail_session)
    response = create_app().test_client().get(
        "/media",
        query_string={"src": "https://images.dcinside.com/private.jpg"},
    )

    assert response.status_code == 400
    assert [(host, port) for host, port, _kwargs in dns_calls] == [("images.dcinside.com", 443)]


def test_media_drops_invalid_range_instead_of_forwarding_it(monkeypatch):
    upstream = _MediaResponse(
        b"img",
        headers={"Content-Type": "image/jpeg", "Content-Length": "3"},
    )
    calls = []

    def fake_fetch(src, headers, cookies, method="GET"):
        calls.append((src, dict(headers), dict(cookies), method))
        return upstream, None

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    response = create_app().test_client().get(
        "/media?src=https://images.dcinside.com/a.jpg",
        headers={"Range": "items=0-2"},
    )

    assert response.status_code == 200
    assert response.data == b"img"
    assert len(calls) == 1
    assert "Range" not in calls[0][1]
    assert upstream.closed is True


def test_media_rejects_disallowed_content_type_without_reading_body(monkeypatch):
    upstream = _MediaResponse(
        b"<html>private</html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return upstream, None

    monkeypatch.setattr(media_proxy, "fetch_media_response", fake_fetch)
    response = create_app().test_client().get("/media?src=https://images.dcinside.com/a.jpg")

    assert response.status_code == 415
    assert len(calls) == 1
    assert upstream.iterated == 0
    assert upstream.closed is True


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_media_get_and_head_revalidate_redirect_hop_shape(monkeypatch, method):
    redirect = _MediaResponse(
        status_code=302,
        headers={"Location": "https://images.dcinside.com.evil.example/private"},
    )
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return redirect

    def fail_wrong_method(*args, **kwargs):
        raise AssertionError("wrong upstream method")

    selected = "_http_head" if method == "HEAD" else "_http_get"
    other = "_http_get" if method == "HEAD" else "_http_head"
    monkeypatch.setattr(media_proxy, selected, fake_request)
    monkeypatch.setattr(media_proxy, other, fail_wrong_method)
    response = create_app().test_client().open(
        "/media?src=https://images.dcinside.com/start.jpg",
        method=method,
    )

    assert response.status_code == 400
    assert [url for url, _kwargs in calls] == ["https://images.dcinside.com/start.jpg"]
    assert calls[0][1]["allow_redirects"] is False
    assert redirect.closed is True


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_media_get_and_head_stop_at_redirect_limit_with_508(monkeypatch, method):
    calls = []
    responses = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        response = _MediaResponse(
            status_code=302,
            headers={"Location": f"/hop-{len(calls)}.jpg"},
        )
        responses.append(response)
        return response

    def fail_wrong_method(*args, **kwargs):
        raise AssertionError("wrong upstream method")

    selected = "_http_head" if method == "HEAD" else "_http_get"
    other = "_http_get" if method == "HEAD" else "_http_head"
    monkeypatch.setattr(media_proxy, "MEDIA_REDIRECT_LIMIT", 2)
    monkeypatch.setattr(media_proxy, selected, fake_request)
    monkeypatch.setattr(media_proxy, other, fail_wrong_method)
    response = create_app().test_client().open(
        "/media?src=https://images.dcinside.com/start.jpg",
        method=method,
    )

    assert response.status_code == 508
    assert [url for url, _kwargs in calls] == [
        "https://images.dcinside.com/start.jpg",
        "https://images.dcinside.com/hop-1.jpg",
        "https://images.dcinside.com/hop-2.jpg",
    ]
    assert all(kwargs["allow_redirects"] is False for _url, kwargs in calls)
    assert all(item.closed for item in responses)
