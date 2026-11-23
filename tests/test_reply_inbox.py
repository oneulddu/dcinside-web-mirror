from pathlib import Path
import shutil
import subprocess

from lxml import html
import pytest

from app import create_app, routes
from app.services.dc.api import API


def test_mobile_threads_never_cross_page_boundaries_and_mentions_override_root():
    api = API.__new__(API)
    page = html.fromstring("""<ul class="all-comment-lst">
      <li class="comment-add" no="20" m_no="0"></li>
      <li class="comment" no="30" m_no="0"></li>
      <li class="comment-add" no="31" m_no="0"></li>
      <li class="comment-add" no="32" m_no="31"></li>
    </ul>""")
    assert [c.reply_to for c in api._mobile_comment_page(list(page))] == [None, None, "30", "31"]
    assert not api._API__parse_pc_comment({"no": 80, "parent": 40, "depth": 1}).reply_to
    assert api._API__parse_pc_comment({"no": 80, "c_no": 50, "depth": 1}).reply_to == "50"


def test_activity_only_returns_reply_edges_after_unlock(monkeypatch):
    app = create_app()
    app.config.update(TESTING=True, WRITE_KEY="inbox-test", SECRET_KEY="test")
    client = app.test_client()
    calls = []

    async def read(pid, board, kind):
        calls.append((pid, board, kind))
        return {"_comments_complete": False}, [{"id": "31", "reply_to": "30", "author": "숨김", "contents": "본문"},
                    {"id": "32", "parent_id": "30", "author": "ㅇㅇ", "contents": " 첫 줄\n둘째 " + "가" * 90},
                    {"id": "33", "reply_to": "0", "dccon": "https://example.invalid/a.png", "contents": ""}], []

    monkeypatch.setattr(routes, "async_read", read)
    path = "/read/reply-activity?board=test&kind=minor&pid=12"
    assert client.get(path).status_code == 403
    client.post("/write/unlock", data={"key": "inbox-test"}, headers={"Origin": "http://localhost"})
    assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get(path.replace("pid=12", "pid=0")).status_code == 400
    result = client.get(path)
    assert calls == [(12, "test", "minor")]
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json == {"ok": True, "comments": ["31", "32", "33"], "replies": [["30", "31"]], "previews": {},
                           "complete": False}
    assert client.get(path + "&after=x").status_code == 400
    assert client.get(path + "&after=31").json["previews"] == {
        "32": {"author": "ㅇㅇ", "text": ("첫 줄 둘째 " + "가" * 90)[:80]},
        "33": {"author": "", "text": "이모티콘"}}


def test_stale_complete_snapshot_is_not_a_browser_baseline(monkeypatch):
    app = create_app()
    app.config.update(TESTING=True, WRITE_KEY="inbox-test", SECRET_KEY="test")
    client = app.test_client()
    client.post("/write/unlock", data={"key": "inbox-test"}, headers={"Origin": "http://localhost"})

    async def stale_read(*args, **kwargs):
        return {"_comments_complete": True, "_served_stale": True}, [], []

    monkeypatch.setattr(routes, "async_read", stale_read)
    result = client.get("/read/reply-activity?board=test&pid=12")
    assert result.status_code == 200 and result.json["complete"] is False


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required")
def test_browser_reply_inbox():
    subprocess.run(["node", "--test", str(Path(__file__).parent / "javascript/reply_inbox.test.cjs")], check=True)


@pytest.mark.parametrize("path", ["/write/unlock", "/poker/free/search"])
def test_nonwriting_pages_load_notification_consumer_without_inbox_controls(path):
    app = create_app()
    app.config.update(TESTING=True, WRITE_KEY="key", VISITOR_CHECK=False)
    page = html.fromstring(app.test_client().get(path).data)
    assert page.xpath('//script[contains(@src,"/reply_inbox.js")]')
    assert not page.xpath('//*[@id="reply-inbox-open"]')
