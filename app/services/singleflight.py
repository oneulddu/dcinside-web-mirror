"""Share an in-progress fetch across threads and asyncio event loops.

The caller owns cache policy and copies shared values before returning them.
Only the owner publishes an outcome; cancelling a waiter never cancels it.
"""

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Flight:
    value: object = None
    error: Optional[BaseException] = None
    _completion: Future = field(default_factory=Future, init=False, repr=False)

    async def wait(self, timeout=None):
        # Store errors as data so an abandoned waiter cannot leave an
        # unobserved exception on its loop-local Future.
        completion = asyncio.wrap_future(self._completion)
        return await asyncio.wait_for(asyncio.shield(completion), timeout)


@contextmanager
def claim_flight(flights, lock, key):
    """Yield (flight, is_owner), publishing and removing the owner's flight."""
    with lock:
        flight = flights.get(key)
        is_owner = flight is None
        if is_owner:
            flight = Flight()
            flights[key] = flight
    try:
        yield flight, is_owner
    except BaseException as exc:
        if is_owner and flight.error is None:
            flight.error = exc
        raise
    finally:
        if is_owner:
            flight._completion.set_result((flight.value, flight.error))
            with lock:
                if flights.get(key) is flight:
                    del flights[key]
