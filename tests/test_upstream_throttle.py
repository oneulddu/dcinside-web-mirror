import time
import asyncio
import pytest
from app.services import upstream_throttle


def test_throttle_disabled():
    upstream_throttle._config.enabled = False
    start = time.monotonic()
    upstream_throttle.wait_for_turn_sync()
    upstream_throttle.wait_for_turn_sync()
    elapsed = time.monotonic() - start
    assert elapsed < 0.01


@pytest.mark.asyncio
async def test_throttle_min_interval():
    upstream_throttle._config.enabled = True
    upstream_throttle._config.min_interval_ms = 100
    upstream_throttle._config.jitter_ms = 0
    upstream_throttle._state.next_request_at = 0
    upstream_throttle._state.blocked_until = 0

    start = time.monotonic()
    await upstream_throttle.wait_for_turn_async()
    await upstream_throttle.wait_for_turn_async()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.1


def test_rate_limit_backoff():
    upstream_throttle._config.enabled = True
    upstream_throttle._config.rate_limit_backoff_ms = 1000
    upstream_throttle._state.consecutive_rate_limits = 0

    upstream_throttle.register_rate_limit()

    assert upstream_throttle._state.blocked_until > time.monotonic()
    assert upstream_throttle._state.consecutive_rate_limits == 1


def test_is_rate_limited_response():
    assert upstream_throttle.is_rate_limited_response(429, "", {})
    assert upstream_throttle.is_rate_limited_response(200, "Too Many Requests", {})
    assert upstream_throttle.is_rate_limited_response(200, "Too Many Attempts.", {})
    assert upstream_throttle.is_rate_limited_response(200, "너무 많은 요청", {})
    assert not upstream_throttle.is_rate_limited_response(200, "OK", {})


def test_get_retry_after_seconds_from_headers():
    headers = {"Retry-After": "54"}
    assert upstream_throttle.get_retry_after_seconds(headers) == 54


def test_apply_rate_limit_headers_blocks_when_remaining_is_zero():
    upstream_throttle._config.enabled = True
    upstream_throttle._state.blocked_until = 0
    upstream_throttle._state.consecutive_rate_limits = 0

    blocked = upstream_throttle.apply_rate_limit_headers(
        {"X-RateLimit-Remaining": "0", "Retry-After": "12"}
    )

    assert blocked is True
    assert upstream_throttle._state.consecutive_rate_limits == 1
    assert upstream_throttle._state.blocked_until > time.monotonic()


def test_apply_rate_limit_headers_is_case_insensitive():
    upstream_throttle._config.enabled = True
    upstream_throttle._state.blocked_until = 0
    upstream_throttle._state.consecutive_rate_limits = 0

    blocked = upstream_throttle.apply_rate_limit_headers(
        {"x-ratelimit-remaining": "0", "retry-after": "7"}
    )

    assert blocked is True
    assert upstream_throttle._state.consecutive_rate_limits == 1
    assert upstream_throttle._state.blocked_until > time.monotonic()
