import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

try:
    from asgiref.sync import async_to_sync
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    async_to_sync = None


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ASYNC_FALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _env_int("MIRROR_ASYNC_BRIDGE_WORKERS", 2)))


async def _await_coro(coro):
    return await coro


def _run_coro_in_new_loop(coro):
    return asyncio.run(coro)


def run_async(coro):
    """Run an async service call from Flask's sync route boundary.

    Normal WSGI requests do not have a running event loop, so asgiref can
    provide the sync-to-async bridge. If this helper is called from tests or
    future code that already has a running event loop, asgiref cannot be used
    in the same thread; keep the existing thread fallback for that case.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        if async_to_sync is not None:
            return async_to_sync(_await_coro)(coro)
        return _run_coro_in_new_loop(coro)
    return ASYNC_FALLBACK_EXECUTOR.submit(_run_coro_in_new_loop, coro).result()
