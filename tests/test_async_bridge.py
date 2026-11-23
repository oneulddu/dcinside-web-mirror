import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import async_bridge


async def _value(value):
    return value


def test_run_async_returns_value_without_running_loop():
    assert async_bridge.run_async(_value("ok")) == "ok"


@pytest.mark.asyncio
async def test_run_async_uses_thread_fallback_inside_running_loop():
    assert async_bridge.run_async(_value("inside-loop")) == "inside-loop"


async def _loop_id():
    return id(asyncio.get_running_loop())


def test_run_async_reuses_background_loop():
    try:
        first_loop_id = async_bridge.run_async(_loop_id())
        second_loop_id = async_bridge.run_async(_loop_id())

        assert first_loop_id == second_loop_id
    finally:
        async_bridge.shutdown_async_bridge()


def test_run_async_starts_one_background_loop_for_concurrent_first_calls():
    async_bridge.shutdown_async_bridge()

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            loop_ids = list(executor.map(lambda _: async_bridge.run_async(_loop_id()), range(8)))

        assert len(set(loop_ids)) == 1
    finally:
        async_bridge.shutdown_async_bridge()


def test_background_loop_is_ready_only_after_it_starts(monkeypatch):
    async_bridge.shutdown_async_bridge()
    original_new_loop = asyncio.new_event_loop
    entered = threading.Event()
    release = threading.Event()
    created = []

    def delayed_new_loop():
        loop = original_new_loop()
        original_run = loop.run_forever

        def delayed_run():
            entered.set()
            assert release.wait(timeout=5)
            original_run()

        loop.run_forever = delayed_run
        created.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", delayed_new_loop)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(async_bridge.get_background_loop)
            assert entered.wait(timeout=5)
            try:
                # A caller cannot receive a loop that is not accepting work yet.
                assert not async_bridge._BACKGROUND_READY.done()
                others = [executor.submit(async_bridge.get_background_loop) for _ in range(7)]
            finally:
                release.set()
            loops = [future.result(timeout=5) for future in [first, *others]]
            assert len(created) == 1
            assert all(loop is loops[0] and loop.is_running() for loop in loops)
            assert async_bridge.run_async(_value("ready")) == "ready"
    finally:
        release.set()
        async_bridge.shutdown_async_bridge()


def test_loop_creation_failure_reaches_caller_and_allows_restart(monkeypatch):
    async_bridge.shutdown_async_bridge()
    def fail():
        raise OSError("no loop")

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "new_event_loop", fail)
        with pytest.raises(OSError, match="no loop"):
            async_bridge.get_background_loop()
    async_bridge.shutdown_async_bridge()
    try:
        assert async_bridge.run_async(_value("new loop")) == "new loop"
    finally:
        async_bridge.shutdown_async_bridge()


def test_close_failure_still_joins_worker(monkeypatch):
    async_bridge.get_background_loop()
    thread = async_bridge._BACKGROUND_THREAD
    async def fail():
        raise RuntimeError("close failed")

    monkeypatch.setattr(async_bridge, "_close_shared_dc_api", fail)
    with pytest.raises(RuntimeError, match="close failed"):
        async_bridge.shutdown_async_bridge()
    assert not thread.is_alive()


def test_late_shutdown_does_not_abandon_live_worker(monkeypatch):
    loop = async_bridge.get_background_loop()
    shared = async_bridge.run_async(async_bridge._get_shared_dc_api())
    entered = threading.Event()
    release = threading.Event()

    def block():
        entered.set()
        release.wait(timeout=5)

    loop.call_soon_threadsafe(block)
    try:
        assert entered.wait(timeout=5)
        with monkeypatch.context() as patch:
            patch.setattr(async_bridge, "_BRIDGE_TIMEOUT", 0.01)
            with pytest.raises(TimeoutError):
                async_bridge.shutdown_async_bridge()
        with pytest.raises(RuntimeError, match="stopping"):
            async_bridge.get_background_loop()
    finally:
        release.set()
        async_bridge.shutdown_async_bridge()
    assert shared.session.closed
