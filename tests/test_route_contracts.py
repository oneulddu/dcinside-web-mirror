from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

from bs4 import BeautifulSoup
import pytest

from app import create_app, routes


async def _board_payload(*args, **kwargs):
    return [
        {
            "id": "123",
            "title": "fixture",
            "subject": None,
            "author": "익명",
            "author_code": None,
            "author_role": None,
            "time": "-",
            "time_display": "-",
            "needs_time_hydrate": False,
            "comment_count": 0,
            "voteup_count": 0,
            "has_image": False,
            "has_video": False,
            "isimage": False,
            "isvideo": False,
            "isrecommend": False,
        }
    ], []


async def _read_payload(*args, **kwargs):
    return (
        {
            "title": "fixture read",
            "author": "익명",
            "author_code": None,
            "author_role": None,
            "time": "-",
            "voteup_count": 0,
            "contents": "fixture",
            "html": "<p>fixture</p>",
            "related_posts": [],
        },
        [],
        [],
    )


def _gallery_payload():
    return [
        {
            "rank": 1,
            "name": "테스트 갤러리",
            "board_id": "test",
            "board_kind": "minor",
            "internal_supported": True,
        }
    ], 1


def _rule_endpoints(app):
    return {
        rule.rule: rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.rule != "/static/<path:filename>"
    }


def _cookie_names(response):
    return {
        header.split("=", 1)[0]
        for header in response.headers.getlist("Set-Cookie")
    }


def test_screen_and_service_route_map_contract():
    app = create_app()

    assert _rule_endpoints(app) == {
        "/": "main.index",
        "/board": "main.board",
        "/board/times": "main.board_times",
        "/embed/link-preview": "main.embed_link_preview",
        "/embed/youtube-size": "main.youtube_size",
        "/favicon.ico": "main.favicon",
        "/healthz": "main.healthz",
        "/legacy/": "main.index_compat_redirect",
        "/legacy/board": "main.board_compat_redirect",
        "/legacy/read": "main.read_compat_redirect",
        "/legacy/recent": "main.recent_compat_redirect",
        "/media": "main.media",
        "/movie": "main.movie",
        "/read": "main.read",
        "/read/related": "main.read_related",
        "/recent": "main.recent",
        "/recent/clear": "main.recent_clear",
        "/recent/remove": "main.recent_remove",
        "/v2/": "main.index_compat_redirect",
        "/v2/board": "main.board_compat_redirect",
        "/v2/read": "main.read_compat_redirect",
        "/v2/recent": "main.recent_compat_redirect",
    }
    assert all(rule.endpoint.startswith("main.") for rule in app.url_map.iter_rules() if rule.endpoint != "static")


def test_screen_status_redirect_cookie_and_html_contract(monkeypatch):
    monkeypatch.setattr(routes, "get_heung_galleries", _gallery_payload)
    monkeypatch.setattr(routes, "search_galleries", lambda query: [])
    monkeypatch.setattr(routes, "_load_board_payload", _board_payload)
    monkeypatch.setattr(routes, "async_read", _read_payload)

    app = create_app()
    client = app.test_client()

    index_response = client.get("/?heung_q=%ED%85%8C%EC%8A%A4%ED%8A%B8")
    board_response = client.get(
        "/board?board=test&page=2&kind=minor&gallery_name=%ED%85%8C%EC%8A%A4%ED%8A%B8"
    )
    read_response = client.get(
        "/read?board=test&pid=123&kind=minor&gallery_name=%ED%85%8C%EC%8A%A4%ED%8A%B8"
    )
    recent_response = client.get("/recent")

    assert [
        index_response.status_code,
        board_response.status_code,
        read_response.status_code,
        recent_response.status_code,
    ] == [200, 200, 200, 200]
    assert BeautifulSoup(index_response.data, "html.parser").title.get_text(strip=True) == "테스트 갤러리 검색 - 숨터"
    assert BeautifulSoup(board_response.data, "html.parser").select_one("a.feed-item") is not None
    assert BeautifulSoup(read_response.data, "html.parser").title.get_text(strip=True) == "fixture read - 숨터"
    assert {"recent_galleries", "recent_galleries_key"} <= _cookie_names(board_response)
    assert {"recent_galleries", "recent_galleries_key"} <= _cookie_names(read_response)

    for alias, target in (
        ("/v2/?heung_q=a&x=1&x=2", "/?heung_q=a&x=1&x=2"),
        ("/v2/board?board=test&page=2&x=1&x=2", "/board?board=test&page=2&x=1&x=2"),
        ("/v2/read?board=test&pid=123&x=1&x=2", "/read?board=test&pid=123&x=1&x=2"),
        ("/v2/recent?x=1&x=2", "/recent?x=1&x=2"),
        ("/legacy/?heung_q=a&x=1&x=2", "/?heung_q=a&x=1&x=2"),
        ("/legacy/board?board=test&page=2&x=1&x=2", "/board?board=test&page=2&x=1&x=2"),
        ("/legacy/read?board=test&pid=123&x=1&x=2", "/read?board=test&pid=123&x=1&x=2"),
        ("/legacy/recent?x=1&x=2", "/recent?x=1&x=2"),
    ):
        for method in ("GET", "HEAD"):
            response = client.open(alias, method=method, follow_redirects=False)
            assert response.status_code == 308
            assert response.headers["Location"] == target
            assert parse_qsl(urlparse(response.headers["Location"]).query) == parse_qsl(urlparse(target).query)
            assert _cookie_names(response) == set()

    exact_query = "x=1&x=2&blank=&encoded=%2F%20"
    response = client.get(f"/legacy/read?{exact_query}", follow_redirects=False)
    assert response.headers["Location"] == f"/read?{exact_query}"
    assert client.get("/legacy/unknown", follow_redirects=False).status_code == 404
    assert client.get("/static/legacy/css/main.css", follow_redirects=False).status_code == 404


def test_read_return_links_request_a_fresh_board_page(monkeypatch):
    monkeypatch.setattr(routes, "async_read", _read_payload)
    response = create_app().test_client().get(
        "/read?board=test&pid=123&source_page=3&recommend=1&kind=minor"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    return_links = [
        soup.select_one(".crumb-link"),
        soup.select_one(".pager-row.single .pager-btn"),
    ]

    assert response.status_code == 200
    for link in return_links:
        query = parse_qs(urlparse(link["href"]).query)
        assert query["board"] == ["test"]
        assert query["page"] == ["3"]
        assert query["refresh"] == ["1"]


def test_board_refresh_query_bypasses_service_cache(monkeypatch):
    calls = []

    async def board_payload(*args, force_refresh=False, **kwargs):
        calls.append(force_refresh)
        return await _board_payload()

    monkeypatch.setattr(routes, "_load_board_payload", board_payload)
    client = create_app().test_client()

    normal_response = client.get("/board?board=test&page=1")
    refresh_response = client.get("/board?board=test&page=1&refresh=1")

    assert normal_response.status_code == 200
    assert refresh_response.status_code == 200
    assert calls == [False, True]


def test_board_history_refresh_script_is_loaded_and_refreshes_at_most_once(monkeypatch):
    monkeypatch.setattr(routes, "_load_board_payload", _board_payload)
    response = create_app().test_client().get("/board?board=test&page=1")
    soup = BeautifulSoup(response.data, "html.parser")
    script = Path("app/static/javascript/board_return_refresh.js").read_text()

    assert soup.select_one("script[src*='/static/javascript/board_return_refresh.js']") is not None
    assert 'var RETURN_MARKER_KEY = "mirror_board_return_refresh_v1"' in script
    assert "window.sessionStorage.setItem(RETURN_MARKER_KEY, boardKey)" in script
    assert "var isMarkedReturn = consumeBoardReturn();" in script
    assert "var enteredWithRefreshMarker = hasRefreshMarker();" in script
    assert 'entries[0].type === "back_forward"' in script
    assert 'fetch(url.toString(), {' in script
    assert 'currentBoardList.replaceWith(nextBoardList)' in script
    assert 'new CustomEvent("mirror:board-refreshed"' in script
    assert "window.location.replace" not in script
    assert "window.history.replaceState(" in script


def test_board_refresh_event_rehydrates_time_and_read_state_scripts():
    time_script = Path("app/static/javascript/board_time_hydrator.js").read_text()
    read_state_script = Path("app/static/javascript/read_state.js").read_text()

    assert 'document.addEventListener("mirror:board-refreshed", hydrateBoardTimes)' in time_script
    assert 'document.addEventListener("mirror:board-refreshed", function (event)' in read_state_script
    assert "applyReadState(event.detail && event.detail.root, readStore)" in read_state_script


def test_board_clamped_page_redirects_once_and_preserves_context(monkeypatch):
    calls = []

    async def clamped_payload(*args, pagination_collector=None, **kwargs):
        calls.append((args, kwargs))
        pagination_collector.update({"requested_page": 58, "current_page": 57, "has_next": False})
        return await _board_payload()

    monkeypatch.setattr(routes, "_load_board_payload", clamped_payload)
    client = create_app().test_client()
    query = {
        "board": "test",
        "page": "58",
        "recommend": "1",
        "kind": "minor",
        "nav": "ai",
        "s_type": "comment",
        "serval": "검색어",
        "headid": "17",
        "gallery_name": "테스트 갤러리",
    }

    response = client.get("/board", query_string=query, follow_redirects=False)

    assert response.status_code == 302
    redirected = urlparse(response.headers["Location"])
    assert parse_qsl(redirected.query) == [
        ("board", "test"),
        ("recommend", "1"),
        ("page", "57"),
        ("kind", "minor"),
        ("nav", "ai"),
        ("headid", "17"),
        ("s_type", "comment"),
        ("serval", "검색어"),
        ("gallery_name", "테스트 갤러리"),
    ]
    final_response = client.get(response.headers["Location"], follow_redirects=False)
    assert final_response.status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize(
    "pagination",
    [
        {"current_page": None, "has_next": False},
        {"current_page": 3, "has_next": False},
        {"current_page": 2, "has_next": None},
        {"current_page": "invalid", "has_next": False},
    ],
)
def test_board_does_not_redirect_without_known_clamped_final_page(monkeypatch, pagination):
    async def payload(*args, pagination_collector=None, **kwargs):
        pagination_collector.update(pagination)
        return await _board_payload()

    monkeypatch.setattr(routes, "_load_board_payload", payload)
    response = create_app().test_client().get(
        "/board?board=test&page=3&s_type=subject&serval=query",
        follow_redirects=False,
    )

    assert response.status_code == 200


@pytest.mark.parametrize(("has_next", "next_is_link"), [(False, False), (None, True)])
def test_board_next_button_uses_explicit_final_page_state(monkeypatch, has_next, next_is_link):
    async def payload(*args, pagination_collector=None, **kwargs):
        pagination_collector.update({"requested_page": 2, "current_page": 2, "has_next": has_next})
        return await _board_payload()

    monkeypatch.setattr(routes, "_load_board_payload", payload)
    response = create_app().test_client().get("/board?board=test&page=2")
    soup = BeautifulSoup(response.data, "html.parser")
    next_link = next(
        (node for node in soup.select(".board-pager .pager-center a") if node.get_text(strip=True) == "다음"),
        None,
    )
    next_disabled = next(
        (node for node in soup.select(".board-pager .pager-center span.off") if node.get_text(strip=True) == "다음"),
        None,
    )

    assert (next_link is not None) is next_is_link
    assert (next_disabled is not None) is (not next_is_link)
