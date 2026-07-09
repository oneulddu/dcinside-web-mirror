import threading

from app.services import cache_utils


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
