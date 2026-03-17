import os
import time
import random
import asyncio
import threading
from dataclasses import dataclass


@dataclass
class ThrottleConfig:
    enabled: bool
    min_interval_ms: int
    max_concurrency: int
    jitter_ms: int
    rate_limit_backoff_ms: int
    rate_limit_max_backoff_ms: int
    log_events: bool


class ThrottleState:
    def __init__(self, max_concurrency):
        self.lock = threading.Lock()
        self.next_request_at = 0.0
        self.blocked_until = 0.0
        self.consecutive_rate_limits = 0
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.sync_semaphore = threading.Semaphore(max_concurrency)


_config = None
_state = None


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def init_from_env():
    global _config, _state
    _config = ThrottleConfig(
        enabled=os.getenv("MIRROR_UPSTREAM_THROTTLE_ENABLED", "true").lower() == "true",
        min_interval_ms=_safe_int(os.getenv("MIRROR_UPSTREAM_MIN_INTERVAL_MS"), 150),
        max_concurrency=_safe_int(os.getenv("MIRROR_UPSTREAM_MAX_CONCURRENCY"), 2),
        jitter_ms=_safe_int(os.getenv("MIRROR_UPSTREAM_JITTER_MS"), 50),
        rate_limit_backoff_ms=_safe_int(os.getenv("MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS"), 5000),
        rate_limit_max_backoff_ms=_safe_int(os.getenv("MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS"), 15000),
        log_events=os.getenv("MIRROR_UPSTREAM_LOG_EVENTS", "false").lower() == "true",
    )
    _state = ThrottleState(_config.max_concurrency)


def _header_value(headers, name, default=None):
    if headers is None:
        return default
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return value
    lowered_name = str(name).lower()
    if hasattr(headers, "items"):
        for key, value in headers.items():
            if str(key).lower() == lowered_name:
                return value
    return default


def _header_int(headers, name):
    value = _header_value(headers, name)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def wait_for_turn_sync():
    if not _config.enabled:
        return

    with _state.lock:
        now = time.monotonic()

        if _state.blocked_until > now:
            wait_time = _state.blocked_until - now
            if _config.log_events:
                print(f"[throttle] blocked, waiting {wait_time:.2f}s")
        else:
            wait_time = max(0, _state.next_request_at - now)

        jitter = random.uniform(0, _config.jitter_ms / 1000.0) if _config.jitter_ms > 0 else 0
        total_wait = wait_time + jitter

        _state.next_request_at = time.monotonic() + total_wait + (_config.min_interval_ms / 1000.0)

    if total_wait > 0:
        time.sleep(total_wait)


async def wait_for_turn_async():
    if not _config.enabled:
        return

    with _state.lock:
        now = time.monotonic()

        if _state.blocked_until > now:
            wait_time = _state.blocked_until - now
            if _config.log_events:
                print(f"[throttle] blocked, waiting {wait_time:.2f}s")
        else:
            wait_time = max(0, _state.next_request_at - now)

        jitter = random.uniform(0, _config.jitter_ms / 1000.0) if _config.jitter_ms > 0 else 0
        total_wait = wait_time + jitter

        _state.next_request_at = time.monotonic() + total_wait + (_config.min_interval_ms / 1000.0)

    if total_wait > 0:
        await asyncio.sleep(total_wait)


def register_rate_limit(retry_after_seconds=None):
    if not _config.enabled:
        return

    with _state.lock:
        _state.consecutive_rate_limits += 1

        if retry_after_seconds:
            backoff = min(retry_after_seconds * 1000, _config.rate_limit_max_backoff_ms)
        else:
            backoff = min(
                _config.rate_limit_backoff_ms * _state.consecutive_rate_limits,
                _config.rate_limit_max_backoff_ms
            )

        _state.blocked_until = time.monotonic() + (backoff / 1000.0)

        if _config.log_events:
            print(f"[throttle] rate limit detected, backoff {backoff}ms")


def get_retry_after_seconds(headers):
    retry_after = _header_int(headers, "Retry-After")
    if retry_after is not None and retry_after >= 0:
        return retry_after

    reset_at = _header_int(headers, "X-RateLimit-Reset")
    if reset_at is not None:
        remaining = max(0, reset_at - int(time.time()))
        if remaining > 0:
            return remaining
    return None


def apply_rate_limit_headers(headers):
    if not _config.enabled:
        return False

    remaining = _header_int(headers, "X-RateLimit-Remaining")
    if remaining is None or remaining > 0:
        return False

    register_rate_limit(get_retry_after_seconds(headers))
    return True


def is_rate_limited_response(status, text, headers):
    if status == 429:
        return True

    if text and any(
        phrase in text
        for phrase in [
            "Too Many Requests",
            "Too Many Attempts",
            "너무 많은 요청",
            "penalty-box",
        ]
    ):
        return True

    return False


def clear_rate_limit_state():
    if not _config.enabled:
        return

    with _state.lock:
        _state.consecutive_rate_limits = 0
        _state.blocked_until = 0.0


# Initialize on import
init_from_env()


class AsyncThrottleGuard:
    """Context manager for async throttle with semaphore"""
    async def __aenter__(self):
        if _config.enabled:
            await _state.semaphore.acquire()
            await wait_for_turn_async()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if _config.enabled:
            _state.semaphore.release()


class SyncThrottleGuard:
    """Context manager for sync throttle with semaphore"""
    def __enter__(self):
        if _config.enabled:
            _state.sync_semaphore.acquire()
            wait_for_turn_sync()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if _config.enabled:
            _state.sync_semaphore.release()
