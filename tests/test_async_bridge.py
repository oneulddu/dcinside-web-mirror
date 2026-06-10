import asyncio
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
