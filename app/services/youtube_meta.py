"""YouTube 임베드 보조 메타데이터 조회.

oEmbed와 표준 썸네일은 항상 16:9 값을 돌려줘서 세로 영상을 판별할 수 없다.
반면 `i.ytimg.com/vi/<id>/frame0.jpg`는 영상 첫 프레임을 실제 비율 그대로
제공하므로(쇼츠 268x480, 일반 세로 직캠 270x480, 4:3 320x240 실측) JPEG
헤더만 Range로 읽어 표시 비율을 얻는다. frame0이 실패하면 유튜브가 일반
영상의 /shorts/<id> 접근을 /watch로 리다이렉트하고 진짜 쇼츠만 200을 주는
특성으로 세로 여부만이라도 판별한다. 결과는 영상 id별로 캐시한다.
"""
import re
import threading
import time

import requests

from .cache_utils import cache_get, cache_set_after_insert, env_int

YOUTUBE_FRAME0_URL = "https://i.ytimg.com/vi/{}/frame0.jpg"
YOUTUBE_SHORTS_PROBE_URL = "https://www.youtube.com/shorts/{}"
YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PROBE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

SIZE_CACHE_TTL = env_int("MIRROR_YT_SIZE_CACHE_TTL", 7 * 24 * 3600)
SIZE_UNKNOWN_CACHE_TTL = env_int("MIRROR_YT_SIZE_UNKNOWN_CACHE_TTL", 300)
SIZE_CACHE_MAX_ITEMS = env_int("MIRROR_YT_SIZE_CACHE_MAX_ITEMS", 4096)
SIZE_PROBE_TIMEOUT = env_int("MIRROR_YT_SIZE_TIMEOUT", 5)
SIZE_MAX_IDS_PER_REQUEST = 12
FRAME0_RANGE_BYTES = 16383
# 공개 엔드포인트의 outbound 증폭 방어: 전역 프로브 예산과 요청당 시간 예산.
# 예산 초과 시 프로브 없이 None을 반환하고 캐시하지 않는다(화면은 16:9 폴백).
PROBE_RATE_WINDOW_SECONDS = env_int("MIRROR_YT_PROBE_RATE_WINDOW", 10)
PROBE_RATE_MAX_CALLS = env_int("MIRROR_YT_PROBE_RATE_MAX", 40)
REQUEST_TIME_BUDGET_SECONDS = env_int("MIRROR_YT_REQUEST_TIME_BUDGET", 6)

# 캐시에 저장하는 판별 실패 표식. video_size()는 실패를 None으로 되돌려 준다.
SIZE_UNKNOWN = "unknown"

_size_cache = {}
_size_lock = threading.Lock()

_probe_rate = {"window_start": 0.0, "used": 0}
_probe_rate_lock = threading.Lock()


def _acquire_probe_slot():
    """outbound HTTP 프로브 1회 자격을 얻는다. 전역 윈도우 예산 초과면 False."""
    now = time.monotonic()
    with _probe_rate_lock:
        if now - _probe_rate["window_start"] >= PROBE_RATE_WINDOW_SECONDS:
            _probe_rate["window_start"] = now
            _probe_rate["used"] = 0
        if _probe_rate["used"] >= PROBE_RATE_MAX_CALLS:
            return False
        _probe_rate["used"] += 1
        return True


def is_valid_video_id(video_id):
    return bool(YOUTUBE_VIDEO_ID_RE.match(str(video_id or "")))


def parse_jpeg_dimensions(data):
    """JPEG SOF 마커에서 (width, height)를 읽는다. 실패하면 None."""
    if not data or len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        # 길이 필드가 없는 독립 마커는 건너뛴다.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment_length = int.from_bytes(data[index + 2:index + 4], "big")
        if segment_length < 2:
            return None
        is_sof = 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)
        if is_sof:
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            if width > 0 and height > 0:
                return width, height
            return None
        index += 2 + segment_length
    return None


def probe_frame0_size(video_id):
    try:
        response = requests.get(
            YOUTUBE_FRAME0_URL.format(video_id),
            timeout=SIZE_PROBE_TIMEOUT,
            headers={
                "User-Agent": PROBE_USER_AGENT,
                "Range": f"bytes=0-{FRAME0_RANGE_BYTES}",
            },
        )
    except requests.RequestException:
        return None
    # 존재하지 않는 영상은 404여도 120x90 플레이스홀더 JPEG 본문을 준다.
    if response.status_code not in (200, 206):
        return None
    dimensions = parse_jpeg_dimensions(response.content)
    if not dimensions:
        return None
    width, height = dimensions
    return {"width": width, "height": height}


def probe_shorts_orientation(video_id):
    """frame0 실패 시 폴백. 쇼츠(200)면 9:16 명목 크기, 그 외에는 None."""
    try:
        response = requests.head(
            YOUTUBE_SHORTS_PROBE_URL.format(video_id),
            allow_redirects=False,
            timeout=SIZE_PROBE_TIMEOUT,
            headers={"User-Agent": PROBE_USER_AGENT},
        )
    except requests.RequestException:
        return None
    if response.status_code == 200:
        return {"width": 9, "height": 16}
    return None


def video_size(video_id, deadline=None):
    """영상 표시 비율용 크기 {"width","height"} 또는 None을 캐시와 함께 반환.

    deadline(monotonic 초)을 넘겼거나 전역 프로브 예산이 소진되면 프로브 없이
    None을 반환하고, 그 결과는 캐시하지 않아 다음 기회에 다시 시도할 수 있다.
    """
    if not is_valid_video_id(video_id):
        return None
    cached = cache_get(_size_cache, _size_lock, video_id)
    if cached is not None:
        return None if cached == SIZE_UNKNOWN else cached
    if deadline is not None and time.monotonic() >= deadline:
        return None
    if not _acquire_probe_slot():
        return None
    size = probe_frame0_size(video_id)
    if not size:
        if not _acquire_probe_slot():
            return None
        size = probe_shorts_orientation(video_id)
    ttl = SIZE_CACHE_TTL if size else SIZE_UNKNOWN_CACHE_TTL
    cache_set_after_insert(
        _size_cache,
        _size_lock,
        video_id,
        size if size else SIZE_UNKNOWN,
        ttl,
        SIZE_CACHE_MAX_ITEMS,
    )
    return size


def sizes_for_ids(raw_ids):
    unique_ids = []
    for raw_id in raw_ids:
        video_id = str(raw_id or "").strip()
        if is_valid_video_id(video_id) and video_id not in unique_ids:
            unique_ids.append(video_id)
        if len(unique_ids) >= SIZE_MAX_IDS_PER_REQUEST:
            break
    deadline = time.monotonic() + REQUEST_TIME_BUDGET_SECONDS
    return {video_id: video_size(video_id, deadline=deadline) for video_id in unique_ids}
