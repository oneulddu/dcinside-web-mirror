"""관련 게시글 초기 목록 계약.

초기 자동 로드 여부는 read.html 이 내려주는 data-has-more 삼상태와 서버 렌더 행
유무로만 결정된다. 값이 접히거나 사라지면 무한 재요청이나 "더보기를 눌러야만
보이는 빈 목록"으로 되돌아가므로, 실제 렌더 결과와 스크립트 동작을 함께 고정한다.
"""

from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from app import create_app
from app import routes


def render_read(monkeypatch, related_posts, has_more, query=""):
    async def fake_async_read(pid, board, kind=None, recommend=0, **kwargs):
        return (
            {
                "title": "본문",
                "author": "익명",
                "author_code": None,
                "time": "-",
                "voteup_count": 0,
                "html": "<p>본문</p>",
                "related_posts": related_posts,
                "_related_has_more": has_more,
            },
            [],
            [],
        )

    monkeypatch.setattr(routes, "async_read", fake_async_read)
    response = create_app().test_client().get("/read?board=test&pid=100" + query)
    assert response.status_code == 200
    return BeautifulSoup(response.data, "html.parser")


def related_post(post_id, **extra):
    row = {
        "id": post_id,
        "title": "관련 글 " + post_id,
        "author": "익명",
        "author_code": None,
        "time": "-",
        "comment_count": 0,
        "voteup_count": 0,
    }
    row.update(extra)
    return row


def visible_empty_rows(soup):
    rows = soup.select("#related-list li.empty-row")
    return [row for row in rows if row.find_parent("noscript") is None]


def test_related_loader_contract_executes_in_node():
    """자동 로드/가드/타임아웃/실패 복구 동작을 실제 스크립트로 실행해 확인한다."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the related loader contract test")

    test_script = Path(__file__).parent / "javascript" / "read_related_loader.test.cjs"
    completed = subprocess.run(
        [node, str(test_script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert "read_related_loader_contract=passed" in completed.stdout


def test_unknown_has_more_renders_autoloadable_empty_list(monkeypatch):
    """끝을 모르면 unknown 으로 내려 스크립트가 첫 목록을 스스로 불러오게 한다."""
    soup = render_read(monkeypatch, [], None)

    section = soup.select_one("#related-section")
    button = soup.select_one("#related-load-button")
    noscript = soup.select_one("#related-list noscript")

    assert section["data-has-more"] == "unknown"
    assert soup.select("#related-list a.feed-item") == []
    assert visible_empty_rows(soup) == []
    assert button.get("disabled") is None
    assert button.select_one("[data-related-more-label]").get_text(strip=True) == "더보기"
    assert "자바스크립트" in noscript.get_text()


def test_confirmed_terminal_renders_end_state_without_javascript(monkeypatch):
    """끝이 확정되면 JS 없이도 종료 문구와 비활성 더보기가 보인다."""
    soup = render_read(monkeypatch, [related_post("201")], False)

    section = soup.select_one("#related-section")
    button = soup.select_one("#related-load-button")
    rows = visible_empty_rows(soup)

    assert section["data-has-more"] == "false"
    assert len(soup.select("#related-list a.feed-item")) == 1
    assert [row.get_text(strip=True) for row in rows] == ["더 불러올 게시글이 없습니다."]
    assert button.has_attr("disabled")
    assert button["data-state"] == "no-more"
    assert button.select_one("[data-related-more-label]").get_text(strip=True) == "더 없음"
    assert soup.select_one("#related-list noscript") is None


def test_known_continuation_keeps_more_button_active(monkeypatch):
    """이어질 글이 있으면 true 로 내려 자동 로드 없이 더보기를 유지한다."""
    soup = render_read(monkeypatch, [related_post("301")], True)

    button = soup.select_one("#related-load-button")

    assert soup.select_one("#related-section")["data-has-more"] == "true"
    assert visible_empty_rows(soup) == []
    assert button.get("disabled") is None
    assert soup.select_one("#related-list noscript") is None


def test_related_row_href_prefers_item_source_page(monkeypatch):
    """행마다 유래한 페이지가 있으면 그 값으로, 없으면 현재 페이지로 링크한다."""
    soup = render_read(
        monkeypatch,
        [related_post("401", source_page=7), related_post("402")],
        True,
        query="&source_page=3",
    )

    links = soup.select("#related-list a.feed-item")

    assert "source_page=7" in links[0]["href"]
    assert "source_page=3" in links[1]["href"]


def test_status_region_is_polite_and_list_reports_busy_state(monkeypatch):
    """목록 전체가 아니라 상태 문구만 읽히도록 별도 live 영역을 유지한다."""
    soup = render_read(monkeypatch, [], None)

    status = soup.select_one("#related-status")

    assert soup.select_one("#related-list")["aria-busy"] == "false"
    assert status["role"] == "status"
    assert status["aria-live"] == "polite"
    assert "related-status" in status["class"]
    assert status.get("style") is None
