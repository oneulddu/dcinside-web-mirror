import asyncio
import atexit
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from .dc import api as dc_api


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ASYNC_FALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _env_int("MIRROR_ASYNC_BRIDGE_WORKERS", 2)))

_BACKGROUND_LOOP = None
_BACKGROUND_THREAD = None
_BACKGROUND_STARTING = False
_BACKGROUND_READY = threading.Event()
_BACKGROUND_LOCK = threading.Lock()
_SHARED_DC_API = None


def _background_loop_worker():
    global _BACKGROUND_LOOP, _BACKGROUND_STARTING
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with _BACKGROUND_LOCK:
        _BACKGROUND_LOOP = loop
        _BACKGROUND_STARTING = False
    _BACKGROUND_READY.set()
    try:
        loop.run_forever()
    finally:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def get_background_loop():
    global _BACKGROUND_THREAD, _BACKGROUND_STARTING
    should_start = False
    with _BACKGROUND_LOCK:
        if _BACKGROUND_LOOP is not None and _BACKGROUND_LOOP.is_running():
            return _BACKGROUND_LOOP
        if not _BACKGROUND_STARTING:
            _BACKGROUND_STARTING = True
            _BACKGROUND_READY.clear()
            _BACKGROUND_THREAD = threading.Thread(
                target=_background_loop_worker,
                name="mirror-async-loop",
                daemon=True,
            )
            should_start = True
        thread = _BACKGROUND_THREAD
    if should_start:
        thread.start()
    _BACKGROUND_READY.wait()
    with _BACKGROUND_LOCK:
        return _BACKGROUND_LOOP


def _is_background_loop(loop):
    with _BACKGROUND_LOCK:
        return loop is not None and loop is _BACKGROUND_LOOP


async def _get_shared_dc_api():
    global _SHARED_DC_API
    if _SHARED_DC_API is None or getattr(_SHARED_DC_API.session, "closed", False):
        _SHARED_DC_API = dc_api.API()
    return _SHARED_DC_API


@asynccontextmanager
async def dc_api_context():
    loop = asyncio.get_running_loop()
    if _is_background_loop(loop):
        yield await _get_shared_dc_api()
        return

    async with dc_api.API() as api:
        yield api


async def _close_shared_dc_api():
    global _SHARED_DC_API
    api = _SHARED_DC_API
    _SHARED_DC_API = None
    if api is not None:
        await api.close()


def shutdown_async_bridge():
    global _BACKGROUND_LOOP, _BACKGROUND_THREAD, _BACKGROUND_STARTING
    with _BACKGROUND_LOCK:
        loop = _BACKGROUND_LOOP
        thread = _BACKGROUND_THREAD

    if loop is None:
        return

    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_close_shared_dc_api(), loop)
        future.result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)

    if thread is not None:
        thread.join(timeout=5)

    with _BACKGROUND_LOCK:
        if _BACKGROUND_LOOP is loop:
            _BACKGROUND_LOOP = None
        if _BACKGROUND_THREAD is thread:
            _BACKGROUND_THREAD = None
        _BACKGROUND_STARTING = False


def _run_coro_in_new_loop(coro):
    return asyncio.run(coro)


def run_async(coro):
    """Run an async service call from Flask's sync route boundary."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = get_background_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return ASYNC_FALLBACK_EXECUTOR.submit(_run_coro_in_new_loop, coro).result()


atexit.register(shutdown_async_bridge)
