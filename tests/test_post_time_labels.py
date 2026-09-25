from datetime import datetime, timezone

import pytest

from app.services.core import format_display_time
from app.services.time_labels import KST, post_time_info

NOW = datetime(2026, 1, 1, 0, 30, tzinfo=KST)


@pytest.mark.parametrize(
    "value, style, label",
    [
        ("2026-01-01 00:05", "list", "00:05"),
        ("2025-12-31 23:50", "list", "어제 23:50"),
        ("2025-12-30 10:00", "list", "2025. 12. 30."),
        ("2025-12-31 23:50", "full", "어제 오후 11:50"),
        ("2025-12-30 10:00", "full", "2025년 12월 30일 오전 10:00"),
        ("12.31", "list", "어제"),
        ("12.30", "full", "2025년 12월 30일"),
        ("25.12.29", "list", "2025. 12. 29."),
        ("-", "list", "-"),
        ("알 수 없음", "list", "알 수 없음"),
    ],
)
def test_post_time_labels_follow_kst_calendar(value, style, label):
    assert post_time_info(value, style=style, now=NOW)["label"] == label


def test_date_only_value_does_not_gain_a_clock():
    info = post_time_info("12.31", now=NOW)

    assert info["iso"] == "2025-12-31"
    assert info["title"] == "2025년 12월 31일"
    assert ":" not in info["label"]


def test_parser_date_only_marker_is_not_shown_as_a_clock():
    # 파서는 날짜만 있는 원문(예: 댓글 "25.12.31")에 23:59:59를 붙인다.
    raw = format_display_time(datetime(2025, 12, 31, 23, 59, 59))

    assert raw == "2025-12-31"
    assert post_time_info(raw, now=NOW)["label"] == "어제"


def test_aware_time_is_converted_to_kst_before_labeling():
    raw = format_display_time(datetime(2025, 12, 31, 15, 5, tzinfo=timezone.utc))

    assert raw == "2026-01-01 00:05"
    assert post_time_info(raw, now=NOW)["iso"] == "2026-01-01T00:05+09:00"
