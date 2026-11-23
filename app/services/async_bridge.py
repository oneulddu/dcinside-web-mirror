import asyncio
import atexit
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
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
_BACKGROUND_STOPPING = False
_BACKGROUND_READY = None
_BRIDGE_TIMEOUT = 5
_BACKGROUND_LOCK = threading.Lock()
_SHARED_DC_API = None


def _background_loop_worker(ready):
    loop = None
    def mark_ready():
        global _BACKGROUND_LOOP
        with _BACKGROUND_LOCK:
            if _BACKGROUND_STOPPING:
                ready.set_exception(RuntimeError("async bridge stopping"))
                loop.stop()
            else:
                _BACKGROUND_LOOP = loop
                ready.set_result(loop)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.call_soon(mark_ready)
        loop.run_forever()
    except BaseException as exc:
        if ready.done():
            raise
        ready.set_exception(exc)
    finally:
        if not ready.done():
            ready.set_exception(RuntimeError("async bridge stopped during startup"))
        if loop is not None:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            try:
                # A blocked loop can reach stop before the scheduled close
                # coroutine ever starts. Never carry its session to a new loop.
                if _SHARED_DC_API is not None:
                    loop.run_until_complete(_close_shared_dc_api())
            finally:
                loop.close()


def get_background_loop():
    global _BACKGROUND_THREAD, _BACKGROUND_READY, _BACKGROUND_STOPPING
    with _BACKGROUND_LOCK:
        if _BACKGROUND_THREAD is None or not _BACKGROUND_THREAD.is_alive():
            _BACKGROUND_STOPPING = False
            _BACKGROUND_READY = Future()
            _BACKGROUND_THREAD = threading.Thread(
                target=_background_loop_worker,
                args=(_BACKGROUND_READY,),
                name="mirror-async-loop",
                daemon=True,
            )
            try:
                _BACKGROUND_THREAD.start()
            except BaseException:
                _BACKGROUND_THREAD = None
                raise
        elif _BACKGROUND_STOPPING:
            raise RuntimeError("async bridge stopping")
        ready = _BACKGROUND_READY
    return ready.result(timeout=_BRIDGE_TIMEOUT)


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
    global _BACKGROUND_LOOP, _BACKGROUND_THREAD, _BACKGROUND_STOPPING
    with _BACKGROUND_LOCK:
        loop = _BACKGROUND_LOOP
        thread = _BACKGROUND_THREAD
        if thread is None:
            return
        initiate = not _BACKGROUND_STOPPING
        _BACKGROUND_STOPPING = True
    try:
        if initiate and loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(_close_shared_dc_api(), loop)
                future.result(timeout=_BRIDGE_TIMEOUT)
            finally:
                loop.call_soon_threadsafe(loop.stop)
    finally:
        thread.join(timeout=_BRIDGE_TIMEOUT)
        if thread.is_alive():
            raise TimeoutError("async bridge thread did not stop")
        with _BACKGROUND_LOCK:
            if _BACKGROUND_THREAD is thread:
                _BACKGROUND_LOOP = _BACKGROUND_THREAD = None
                _BACKGROUND_STOPPING = False


def _run_coro_in_new_loop(coro):
    return asyncio.run(coro)


def run_async(coro):
    """Run an async service call from Flask's sync route boundary."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = get_background_loop()
            with _BACKGROUND_LOCK:
                if _BACKGROUND_STOPPING or loop is not _BACKGROUND_LOOP:
                    raise RuntimeError("async bridge stopping")
                future = asyncio.run_coroutine_threadsafe(coro, loop)
        except BaseException:
            coro.close()
            raise
        return future.result()
    return ASYNC_FALLBACK_EXECUTOR.submit(_run_coro_in_new_loop, coro).result()


atexit.register(shutdown_async_bridge)
