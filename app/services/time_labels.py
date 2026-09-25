"""게시글·댓글 시각을 KST 기준으로 읽기 쉬운 문구로 바꾼다.

캐시에는 원래 시각(YYYY-MM-DD HH:MM 또는 날짜만 있는 원문)을 그대로 두고,
화면을 그리는 순간의 시각을 기준으로 문구를 만든다. 그래야 캐시된 목록도
자정이 지나면 "오늘"이 "어제"로 바르게 바뀐다.
"""

import re
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

_FULL_RE = re.compile(
    r"^(\d{4})[-.](\d{1,2})[-.](\d{1,2})\.?(?:[ T](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?$"
)
_SHORT_YMD_RE = re.compile(r"^(\d{2})\.(\d{1,2})\.(\d{1,2})\.?$")
_MD_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})\.?(?: (\d{1,2}):(\d{2})(?::\d{2})?)?$")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


def now_kst():
    return datetime.now(KST)


def _current(now):
    if now is None:
        return now_kst().replace(tzinfo=None)
    if isinstance(now, datetime):
        return now.astimezone(KST).replace(tzinfo=None) if now.tzinfo else now
    return datetime.fromtimestamp(float(now), tz=KST).replace(tzinfo=None)


def _infer_year(month, day, current):
    candidate = date(current.year, month, day)
    if candidate > current.date() + timedelta(days=1):
        candidate = candidate.replace(year=current.year - 1)
    return candidate.year


def parse_post_time(value, now=None):
    """시각을 (KST 기준 naive datetime, 시:분 포함 여부)로 해석한다. 실패하면 None."""
    current = _current(now)
    if isinstance(value, datetime):
        parsed = value.astimezone(KST).replace(tzinfo=None) if value.tzinfo else value
        return parsed.replace(second=0, microsecond=0), True
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    try:
        match = _FULL_RE.match(text)
        if match:
            year, month, day, hour, minute = match.groups()
            if hour is None:
                return datetime(int(year), int(month), int(day)), False
            return datetime(int(year), int(month), int(day), int(hour), int(minute)), True
        match = _SHORT_YMD_RE.match(text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(2000 + year, month, day), False
        match = _MD_RE.match(text)
        if match:
            month, day, hour, minute = match.groups()
            year = _infer_year(int(month), int(day), current)
            if hour is None:
                return datetime(year, int(month), int(day)), False
            return datetime(year, int(month), int(day), int(hour), int(minute)), True
        match = _CLOCK_RE.match(text)
        if match:
            hour, minute = (int(part) for part in match.groups())
            return current.replace(hour=hour, minute=minute, second=0, microsecond=0), True
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _clock_with_period(value):
    period = "오전" if value.hour < 12 else "오후"
    return f"{period} {value.hour % 12 or 12}:{value.minute:02d}"


def post_time_info(value, style="list", now=None):
    """화면 문구(label), 전체 시각(title), 기계용 값(iso)을 돌려준다.

    style="list": 목록·댓글용. 오늘은 14:05, 어제는 "어제 14:05",
    올해는 "9월 24일", 그 전은 "2025. 9. 24.".
    style="full": 글 보기 머리말용. "오늘 오후 2:05", "9월 24일 오후 2:05",
    "2025년 9월 24일 오후 2:05".
    날짜만 아는 값에는 시각을 만들어 붙이지 않는다.
    """
    current = _current(now)
    parsed = parse_post_time(value, now=current)
    if parsed is None:
        raw = str(value or "").strip()
        return {"label": raw or "-", "title": "", "iso": ""}

    moment, has_clock = parsed
    days = (current.date() - moment.date()).days
    same_year = moment.year == current.year
    full_date = f"{moment.year}년 {moment.month}월 {moment.day}일"

    if days == 0:
        day_label = "오늘"
    elif days == 1:
        day_label = "어제"
    elif same_year:
        day_label = f"{moment.month}월 {moment.day}일"
    else:
        day_label = None

    if has_clock:
        title = f"{full_date} {_clock_with_period(moment)}"
        iso = moment.strftime("%Y-%m-%dT%H:%M") + "+09:00"
        if style == "full":
            label = f"{day_label or full_date} {_clock_with_period(moment)}"
        elif days == 0:
            label = moment.strftime("%H:%M")
        elif days == 1:
            label = f"어제 {moment.strftime('%H:%M')}"
        else:
            label = day_label or f"{moment.year}. {moment.month}. {moment.day}."
    else:
        title = full_date
        iso = moment.strftime("%Y-%m-%d")
        if style == "full":
            label = day_label or full_date
        else:
            label = day_label or f"{moment.year}. {moment.month}. {moment.day}."

    return {"label": label, "title": title, "iso": iso}


def format_post_time(value, style="list", now=None):
    return post_time_info(value, style=style, now=now)["label"]
