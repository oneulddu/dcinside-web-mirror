import threading

from app.services import cache_utils
from app.services import core


def test_cache_entry_expires_exactly_at_deadline(monkeypatch):
    cache = {"key": {"value": "cached", "expires_at": 100.0}}
    lock = threading.Lock()
    monkeypatch.setattr(cache_utils.time, "time", lambda: 100.0)

    assert cache_utils.cache_get(cache, lock, "key") is None
    assert "key" not in cache


def test_cache_prune_expires_exact_deadline():
    cache = {
        "expired": {"value": "old", "expires_at": 100.0},
        "fresh": {"value": "new", "expires_at": 101.0},
    }

    cache_utils.cache_prune(cache, 100.0, 10)

    assert "expired" not in cache
    assert "fresh" in cache


def test_core_cache_set_never_exceeds_max_items(monkeypatch):
    cache = {}
    lock = threading.Lock()
    core._CACHE_PRUNE_STATE.clear()
    monkeypatch.setattr(core, "CACHE_PRUNE_EVERY", 1000)
    monkeypatch.setattr(core, "CACHE_PRUNE_MIN_INTERVAL", 1000)
    monkeypatch.setattr(core.time, "time", lambda: 100.0)

    for key in ("oldest", "middle", "newest"):
        core._cache_set(cache, lock, key, key, ttl=60, max_items=2)

    assert list(cache) == ["middle", "newest"]
