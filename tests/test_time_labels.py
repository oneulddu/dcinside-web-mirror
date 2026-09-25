from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, routes
from app.services.recent import format_relative_day_time


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 26, 16, 0, tzinfo=KST)


@pytest.mark.parametrize("value, with_period, expected", [
    ("2026-09-26T02:52:00+09:00", False, "오늘 02:52"),
    ("2026-09-26T02:52:00+09:00", True, "오늘 오전 2:52"),
    ("2026-09-26T15:05:00+09:00", True, "오늘 오후 3:05"),
    ("2026-09-26T00:07:00+09:00", True, "오늘 오전 12:07"),
    ("2026-09-26T12:07:00+09:00", True, "오늘 오후 12:07"),
    ("2026-09-25T23:59:00+09:00", False, "어제 23:59"),
    ("2026-09-25T15:05:00+09:00", True, "어제 오후 3:05"),
    ("2026-09-24T15:05:00+09:00", False, "9월 24일"),
    ("2026-09-24T15:05:00+09:00", True, "9월 24일"),
    ("2025-09-24T15:05:00+09:00", True, "2025. 9. 24."),
    ("2026-09-25T15:00:00+00:00", False, "오늘 00:00"),
    ("2026-09-25T14:59:00+00:00", False, "어제 23:59"),
])
def test_relative_day_time(value, with_period, expected):
    ts = datetime.fromisoformat(value).timestamp()
    assert format_relative_day_time(ts, now=NOW, with_period=with_period) == expected


@pytest.mark.parametrize("value", [None, 0, "", "invalid", [], {}, float("nan"), float("inf"), -float("inf"), 10**1000])
def test_invalid_timestamp(value):
    assert format_relative_day_time(value, now=NOW) == "-"


@pytest.mark.parametrize("now", [NOW, NOW.timestamp(), NOW.astimezone(timezone.utc), NOW.replace(tzinfo=None)])
def test_injected_clock_forms(now):
    assert format_relative_day_time(NOW.timestamp(), now=now) == "오늘 16:00"


def test_yesterday_across_year_boundary():
    now = datetime(2026, 1, 1, 0, 1, tzinfo=KST)
    ts = datetime(2025, 12, 31, 23, 59, tzinfo=KST).timestamp()
    assert format_relative_day_time(ts, now=now) == "어제 23:59"


def test_home_and_recent_share_item_labels_and_name_resolution(monkeypatch):
    rows = [
        {"board": "shared", "name": None, "kind": "minor", "visited_at": NOW.timestamp()},
        *[{"board": f"board{i}", "name": f"이름{i}", "visited_at": NOW.timestamp() - i} for i in range(8)],
        {"board": "shared", "name": "저장 이름", "kind": "minor", "visited_at": NOW.timestamp() - 9},
        {"board": "lookup", "name": None, "visited_at": NOW.timestamp() - 10},
        {"board": "fallback", "name": None, "visited_at": None},
    ]
    monkeypatch.setattr(routes, "load_recent_entries", lambda: rows)
    monkeypatch.setattr(routes, "get_heung_galleries", lambda: ([
        {"board_id": "shared", "name": "조회 이름", "board_kind": "minor"},
        {"board_id": "lookup", "name": "조회 이름"},
    ], NOW.timestamp()))
    monkeypatch.setattr(routes, "render_template", lambda template, **context: context)
    with create_app().test_request_context("/"):
        home = routes.index()
        history = routes.recent()
    assert home["recent_items"] == history["recent_items"][:8]
    assert len(home["recent_items"]) == 8
    assert history["recent_max_items"] == routes.RECENT_MAX_ITEMS
    assert home["recent_items"][0]["display_name"] == "저장 이름"
    assert history["recent_items"][-2]["display_name"] == "조회 이름"
    assert history["recent_items"][-1]["display_name"] == "fallback"
    assert history["recent_items"][-1]["visited_at_label"] == "-"
    assert home["recent_items"][0]["visited_at_str"] == "2026-09-26 16:00"
    assert home["heung_updated_label"] == format_relative_day_time(NOW.timestamp(), with_period=True)
