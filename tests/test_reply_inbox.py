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
        return {}, [{"id": "31", "reply_to": "30", "author": "숨김", "contents": "본문"},
                    {"id": "32", "parent_id": "30"}, {"id": "33", "reply_to": "0"}], []

    monkeypatch.setattr(routes, "async_read", read)
    path = "/read/reply-activity?board=test&kind=minor&pid=12"
    assert client.get(path).status_code == 403
    client.post("/write/unlock", data={"key": "inbox-test"}, headers={"Origin": "http://localhost"})
    assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get(path.replace("pid=12", "pid=0")).status_code == 400
    result = client.get(path)
    assert calls == [(12, "test", "minor")]
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json == {"ok": True, "comments": ["31", "32", "33"], "replies": [["30", "31"]]}


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required")
def test_browser_reply_inbox():
    subprocess.run(["node", "--test", str(Path(__file__).parent / "javascript/reply_inbox.test.cjs")], check=True)
