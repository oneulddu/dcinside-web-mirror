import os
import time


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def cache_get(cache, lock, key):
    now = time.time()
    with lock:
        entry = cache.get(key)
        if not entry:
            return None
        if entry["expires_at"] < now:
            cache.pop(key, None)
            return None
        return entry["value"]


def cache_prune(cache, now, max_items):
    expired_keys = [key for key, entry in cache.items() if entry["expires_at"] < now]
    for key in expired_keys:
        cache.pop(key, None)
    overflow = len(cache) - max(max_items, 0)
    if overflow <= 0:
        return
    oldest_keys = sorted(cache, key=lambda key: cache[key]["expires_at"])[:overflow]
    for key in oldest_keys:
        cache.pop(key, None)


def cache_set_after_insert(cache, lock, key, value, ttl, max_items, prune_func=cache_prune):
    expires_at = time.time() + max(safe_int(ttl, 0), 0)
    with lock:
        cache[key] = {"value": value, "expires_at": expires_at}
        if len(cache) > max(max_items, 0):
            prune_func(cache, time.time(), max_items)


def cache_delete(cache, lock, key):
    with lock:
        cache.pop(key, None)
