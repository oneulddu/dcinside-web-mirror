import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.singleflight import claim_flight


@pytest.mark.asyncio
async def test_flight_shares_completion_across_threads_and_event_loops():
    flights, lock = {}, threading.Lock()
    joined = threading.Event()

    def wait_in_other_loop():
        async def wait():
            with claim_flight(flights, lock, "key") as (flight, owner):
                assert not owner
                joined.set()
                return await flight.wait(timeout=2)
        return asyncio.run(wait())

    with ThreadPoolExecutor(max_workers=1) as executor:
        with claim_flight(flights, lock, "key") as (flight, owner):
            assert owner
            waiter = executor.submit(wait_in_other_loop)
            assert await asyncio.to_thread(joined.wait, 2)
            flight.value = {"answer": 42}

        assert await asyncio.wrap_future(waiter) == ({"answer": 42}, None)
    assert flights == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", ["cancel", "timeout"])
async def test_abandoned_waiter_does_not_cancel_shared_completion(abandon):
    flights, lock = {}, threading.Lock()
    with claim_flight(flights, lock, "key") as (flight, _):
        waiter = asyncio.create_task(flight.wait(timeout=0.01 if abandon == "timeout" else None))
        await asyncio.sleep(0)
        if abandon == "cancel":
            waiter.cancel()
        with pytest.raises(asyncio.CancelledError if abandon == "cancel" else asyncio.TimeoutError):
            await waiter
        remaining = asyncio.create_task(flight.wait(timeout=2))
        flight.value = "completed"

    assert await remaining == ("completed", None)
    assert flights == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("failed"), asyncio.CancelledError()])
async def test_owner_failure_notifies_waiters_and_allows_retry(error):
    flights, lock = {}, threading.Lock()
    with pytest.raises(type(error)):
        with claim_flight(flights, lock, "key") as (flight, _):
            waiter = asyncio.create_task(flight.wait(timeout=2))
            raise error

    value, shared_error = await waiter
    assert value is None
    assert shared_error is error
    assert flights == {}
    with claim_flight(flights, lock, "key") as (retry, owner):
        assert owner
        retry.value = "recovered"
    assert await retry.wait() == ("recovered", None)
