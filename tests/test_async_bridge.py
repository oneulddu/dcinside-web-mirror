import pytest

from app.services.async_bridge import run_async


async def _value(value):
    return value


def test_run_async_returns_value_without_running_loop():
    assert run_async(_value("ok")) == "ok"


@pytest.mark.asyncio
async def test_run_async_uses_thread_fallback_inside_running_loop():
    assert run_async(_value("inside-loop")) == "inside-loop"
