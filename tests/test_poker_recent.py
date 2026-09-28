"""Pokergosu views stay out of the DC recent-gallery history, including rows left by older builds."""
import base64
import json

import pytest

from app import create_app, routes
from app.services import recent


def encode(rows):
    return base64.urlsafe_b64encode(json.dumps(rows, ensure_ascii=False).encode()).decode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(recent, "RECENT_SERVER_CACHE", {})
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([], 0))
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_legacy_poker_rows_are_hidden_but_dc_same_id_row_stays(client):
    rows = [
        {"board": "poker:free", "name": "포커고수 자유 게시판", "kind": "poker", "visited_at": 30},
        {"board": "poker:news", "kind": None, "visited_at": 20},
        {"board": "free", "name": "자유 갤러리", "kind": "minor", "visited_at": 10},
    ]
    client.set_cookie(recent.RECENT_COOKIE_NAME, encode(rows))
    for path in ("/", "/recent"):
        html = client.get(path).get_data(as_text=True)
        assert "포커고수 자유 게시판" not in html and "/poker/news" not in html
        assert "자유 갤러리" in html


def test_poker_kind_is_not_accepted_for_dc_rows():
    assert recent.normalize_recent_entry({"board": "free", "kind": "poker"}) is None
    assert recent.normalize_recent_entry({"board": "poker:free", "kind": ""}) is None
    assert recent.normalize_recent_entry({"board": "free", "kind": "minor"})["kind"] == "minor"
