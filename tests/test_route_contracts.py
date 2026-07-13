from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from bs4 import BeautifulSoup

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
    ], [], None


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


def test_search_pager_preserves_search_position(monkeypatch):
    async def search_payload(*args, **kwargs):
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        assert kwargs["search_pos"] == -20816199
        return rows, categories, {
            "next_pos": -20806199,
            "block_max_page": 3,
            "source_pattern": "mobile",
        }

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=2&s_type=subject_m&serval=hello&s_pos=-20816199"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    pager_links = {
        link.get_text(strip=True): parse_qs(urlparse(link["href"]).query)
        for link in soup.select(".board-pager .pager-center a")
    }

    assert response.status_code == 200
    assert soup.select_one("#board-list")["data-search-pos"] == "-20816199"
    assert parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)["s_pos"] == ["-20816199"]
    assert parse_qs(urlparse(soup.select_one("a.feed-item")["href"]).query)["source_pattern"] == ["mobile"]
    assert pager_links["이전"]["page"] == ["1"]
    assert pager_links["다음"]["page"] == ["3"]
    assert pager_links["이전"]["s_pos"] == ["-20816199"]
    assert pager_links["다음"]["s_pos"] == ["-20816199"]
    assert pager_links["이전"]["source_pattern"] == ["mobile"]
    assert pager_links["다음"]["source_pattern"] == ["mobile"]
    assert soup.select_one(".board-search-form [name='s_pos']") is None


def test_search_position_accepts_s_keyword_alias(monkeypatch):
    async def search_payload(*args, **kwargs):
        assert kwargs["search_keyword"] == "hello"
        assert kwargs["search_pos"] == -123
        return await _board_payload(*args, **kwargs)

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()

    response = app.test_client().get("/board?board=test&s_keyword=hello&s_pos=-123")

    assert response.status_code == 200


def test_search_position_accepts_pc_search_pos_alias(monkeypatch):
    async def fake_board_payload(page, board, recommend, kind=None, **kwargs):
        assert kwargs["search_keyword"] == "hello"
        assert kwargs["search_pos"] == -123
        return [], [], {"prev_pos": None, "next_pos": None, "block_max_page": 1}

    monkeypatch.setattr(routes, "_load_board_payload", fake_board_payload)
    app = create_app()

    response = app.test_client().get(
        "/board?board=test&s_keyword=hello&search_pos=-123"
    )

    assert response.status_code == 200


def test_search_first_page_links_to_previous_search_block(monkeypatch):
    async def search_payload(*args, **kwargs):
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        return rows, categories, {"prev_pos": -20826199, "next_pos": -20806199}

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=1&serval=hello&s_pos=-20816199"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    previous_query = parse_qs(
        urlparse(soup.select_one(".board-pager .pager-center a.pager-btn")["href"]).query
    )

    assert response.status_code == 200
    assert previous_query["page"] == ["1"]
    assert previous_query["s_pos"] == ["-20826199"]


def test_search_pager_prefers_regular_next_page_before_next_block(monkeypatch):
    async def search_payload(*args, **kwargs):
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        return rows, categories, {
            "prev_pos": -30,
            "next_page": 16,
            "next_pos": -10,
            "block_max_page": 14,
        }

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=15&serval=hello&s_pos=-20"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    next_link = next(
        link for link in soup.select(".board-pager .pager-center a")
        if link.get_text(strip=True) == "다음"
    )
    next_query = parse_qs(urlparse(next_link["href"]).query)

    assert next_query["page"] == ["16"]
    assert next_query["s_pos"] == ["-20"]


def test_search_first_page_previous_link_omits_position_for_first_block(monkeypatch):
    async def search_payload(*args, **kwargs):
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        return rows, categories, {"prev_pos": 0, "next_pos": -20806199}

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=1&serval=hello&s_pos=-20816199"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    previous_query = parse_qs(
        urlparse(soup.select_one(".board-pager .pager-center a.pager-btn")["href"]).query
    )

    assert response.status_code == 200
    assert previous_query["page"] == ["1"]
    assert "s_pos" not in previous_query


def test_search_block_roundtrip_restores_previous_block_last_page(monkeypatch):
    async def search_payload(page, *args, **kwargs):
        rows, categories, _search_nav = await _board_payload(page, *args, **kwargs)
        if kwargs.get("search_pos") is None:
            return rows, categories, {
                "next_pos": -20,
                "source_pattern": "mobile",
            }
        return rows, categories, {
            "prev_pos": -30,
            "source_pattern": "normal",
        }

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    client = app.test_client()

    first = client.get("/board?board=test&page=7&serval=hello&source_pattern=mobile")
    first_soup = BeautifulSoup(first.data, "html.parser")
    next_link = next(
        link for link in first_soup.select(".board-pager a")
        if link.get_text(strip=True) == "다음"
    )
    next_query = parse_qs(urlparse(next_link["href"]).query)
    second = client.get(next_link["href"])
    second_soup = BeautifulSoup(second.data, "html.parser")
    previous_link = next(
        link for link in second_soup.select(".board-pager a")
        if link.get_text(strip=True) == "이전"
    )
    previous_query = parse_qs(urlparse(previous_link["href"]).query)

    assert next_query["page"] == ["1"]
    assert next_query["prev_page"] == ["7~0~mobile"]
    assert previous_query["page"] == ["7"]
    assert previous_query["s_pos"] == ["-30"]
    assert previous_query["source_pattern"] == ["mobile"]


def test_search_three_block_roundtrip_preserves_full_previous_page_stack(monkeypatch):
    async def search_payload(page, *args, **kwargs):
        rows, categories, _search_nav = await _board_payload(page, *args, **kwargs)
        search_pos = kwargs.get("search_pos")
        if search_pos is None:
            return rows, categories, {"next_pos": -20, "source_pattern": "mobile"}
        if search_pos == -20:
            return rows, categories, {
                "prev_pos": 0,
                "next_pos": -10 if page == 16 else None,
                "source_pattern": "mobile",
            }
        return rows, categories, {"prev_pos": -20, "source_pattern": "mobile"}

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    client = app.test_client()

    first = client.get("/board?board=test&page=18&serval=hello&source_pattern=mobile")
    first_next = next(
        link for link in BeautifulSoup(first.data, "html.parser").select(".board-pager a")
        if link.get_text(strip=True) == "다음"
    )
    second_last_parts = urlparse(first_next["href"])
    second_last_query = parse_qs(second_last_parts.query)
    second_last_query["page"] = ["16"]
    second_last_url = second_last_parts._replace(
        query=urlencode(second_last_query, doseq=True)
    ).geturl()
    second_last = client.get(second_last_url)
    second_next = next(
        link for link in BeautifulSoup(second_last.data, "html.parser").select(".board-pager a")
        if link.get_text(strip=True) == "다음"
    )
    third_query = parse_qs(urlparse(second_next["href"]).query)
    third = client.get(second_next["href"])
    back_to_second = next(
        link for link in BeautifulSoup(third.data, "html.parser").select(".board-pager a")
        if link.get_text(strip=True) == "이전"
    )
    second_query = parse_qs(urlparse(back_to_second["href"]).query)
    second_first_parts = urlparse(back_to_second["href"])
    second_first_query = parse_qs(second_first_parts.query)
    second_first_query["page"] = ["1"]
    second_first_url = second_first_parts._replace(
        query=urlencode(second_first_query, doseq=True)
    ).geturl()
    second_first = client.get(second_first_url)
    back_to_first = next(
        link for link in BeautifulSoup(second_first.data, "html.parser").select(".board-pager a")
        if link.get_text(strip=True) == "이전"
    )
    first_query = parse_qs(urlparse(back_to_first["href"]).query)

    assert third_query["prev_page"] == ["18~0~mobile,16~-20~mobile"]
    assert second_query["page"] == ["16"]
    assert second_query["s_pos"] == ["-20"]
    assert second_query["prev_page"] == ["18~0~mobile"]
    assert first_query["page"] == ["18"]
    assert "s_pos" not in first_query
    assert "prev_page" not in first_query


def test_search_board_republishes_actual_source_pattern(monkeypatch):
    seen = {}

    async def search_payload(*args, **kwargs):
        seen["list_pattern"] = kwargs.get("list_pattern")
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        return rows, categories, {
            "next_page": 2,
            "source_pattern": "normal",
        }

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=1&serval=hello&source_pattern=mobile"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    next_link = next(
        link for link in soup.select(".board-pager a")
        if link.get_text(strip=True) == "다음"
    )

    assert seen["list_pattern"] == "mobile"
    assert parse_qs(urlparse(next_link["href"]).query)["source_pattern"] == ["normal"]
    assert parse_qs(urlparse(soup.select_one("#board-list a.feed-item")["href"]).query)["source_pattern"] == ["normal"]


def test_search_board_rejects_incompatible_mobile_mini_pattern(monkeypatch):
    seen = {}

    async def search_payload(*args, **kwargs):
        seen["list_pattern"] = kwargs.get("list_pattern")
        rows, categories, _search_nav = await _board_payload(*args, **kwargs)
        return rows, categories, {"source_pattern": "mobile"}

    monkeypatch.setattr(routes, "_load_board_payload", search_payload)
    app = create_app()
    response = app.test_client().get(
        "/board?board=test&page=1&serval=hello&source_pattern=mobile_mini"
    )
    soup = BeautifulSoup(response.data, "html.parser")

    assert seen["list_pattern"] is None
    assert parse_qs(urlparse(soup.select_one("#board-list a.feed-item")["href"]).query)["source_pattern"] == ["mobile"]


def test_read_navigation_preserves_search_position(monkeypatch):
    seen = {}

    async def read_payload(*args, **kwargs):
        seen["search_pos"] = kwargs.get("search_pos")
        data, comments, images = await _read_payload(*args, **kwargs)
        data["related_posts"] = [{
            "id": "122",
            "title": "related",
            "author": "익명",
            "time": "-",
            "voteup_count": 0,
            "comment_count": 0,
        }]
        return data, comments, images

    monkeypatch.setattr(routes, "async_read", read_payload)
    app = create_app()
    response = app.test_client().get(
        "/read?board=test&pid=123&source_page=2&serval=hello&s_pos=-123&source_pattern=mobile"
    )
    soup = BeautifulSoup(response.data, "html.parser")

    assert response.status_code == 200
    assert seen["search_pos"] == -123
    for selector in (".crumb-link", ".pager-row .pager-btn"):
        query = parse_qs(urlparse(soup.select_one(selector)["href"]).query)
        assert query["s_pos"] == ["-123"]
        assert query["source_pattern"] == ["mobile"]
    assert soup.select_one("#related-list a.feed-item") is None
    assert "더보기를 누르면 불러옵니다" in soup.select_one("#related-list").get_text(" ", strip=True)
    assert soup.select_one("#related-section")["data-search-pos"] == "-123"
    assert soup.select_one("#related-section")["data-source-pattern"] == "mobile"
    canonical_query = parse_qs(urlparse(soup.select_one('meta[property="og:url"]')["content"]).query)
    assert canonical_query["s_pos"] == ["-123"]
