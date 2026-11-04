import asyncio
from types import SimpleNamespace

import pytest

from app.services.dc import api as dc_api
from app.services.dc.api import API

URL = "https://m.dcinside.com/board/test?id=secret&no=1"


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, body_delay, body_error=None):
        self.body_delay = body_delay
        self.body_error = body_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def text(self):
        await asyncio.sleep(self.body_delay)
        if self.body_error:
            raise self.body_error
        return "ok"


class FakeSession:
    def __init__(self, response=None, request_delay=0, request_error=None):
        self.response = response
        self.request_delay = request_delay
        self.request_error = request_error

    def request(self, *args, **kwargs):
        session = self

        class Context:
            async def __aenter__(self):
                await asyncio.sleep(session.request_delay)
                if session.request_error:
                    raise session.request_error
                return await session.response.__aenter__()

            async def __aexit__(self, *args):
                return None

        return Context()


def make_api(session, threshold, monkeypatch):
    monkeypatch.setattr(dc_api, "DC_SLOW_REQUEST_SECONDS", threshold)
    api = API.__new__(API)
    api.session = session
    return api


def slow_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("slow upstream")]


@pytest.mark.asyncio
async def test_slow_success_logs_once_without_query(monkeypatch, caplog):
    api = make_api(FakeSession(FakeResponse(body_delay=0.06)), 0.05, monkeypatch)
    caplog.set_level("WARNING", logger="app.services.dc.api")

    status, _, text = await api._API__request_text("GET", URL)

    assert (status, text) == (200, "ok")
    messages = slow_messages(caplog)
    assert len(messages) == 1
    assert "host='m.dcinside.com' status=200 elapsed=" in messages[0]
    assert "queue=" in messages[0] and "upstream=" in messages[0]
    assert messages[0].endswith("outcome=complete")
    assert "secret" not in messages[0] and "?" not in messages[0]


@pytest.mark.asyncio
async def test_fast_request_does_not_log(monkeypatch, caplog):
    api = make_api(FakeSession(FakeResponse(body_delay=0)), 5, monkeypatch)
    caplog.set_level("WARNING", logger="app.services.dc.api")

    await api._API__request_text("GET", URL)

    assert slow_messages(caplog) == []


@pytest.mark.asyncio
async def test_slow_body_read_failure_logs_captured_status_and_propagates(monkeypatch, caplog):
    error = ValueError("boom https://m.dcinside.com/board/test?id=secret")
    api = make_api(FakeSession(FakeResponse(body_delay=0.06, body_error=error)), 0.05, monkeypatch)
    caplog.set_level("WARNING", logger="app.services.dc.api")

    with pytest.raises(ValueError) as raised:
        await api._API__request_text("GET", URL)

    assert raised.value is error
    messages = slow_messages(caplog)
    assert len(messages) == 1
    assert "status=200" in messages[0]
    assert "boom" not in messages[0] and "secret" not in messages[0]


@pytest.mark.asyncio
async def test_slow_failure_without_response_logs_dash_status(monkeypatch, caplog):
    error = asyncio.TimeoutError()
    api = make_api(FakeSession(request_delay=0.06, request_error=error), 0.05, monkeypatch)
    caplog.set_level("WARNING", logger="app.services.dc.api")

    with pytest.raises(asyncio.TimeoutError) as raised:
        await api._API__request_text("GET", URL)

    assert raised.value is error
    messages = slow_messages(caplog)
    assert len(messages) == 1
    assert "status=-" in messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_queue_time_is_reported_even_when_cancelled_before_admission(monkeypatch, caplog, cancel):
    clock = [0.0]
    entered = asyncio.Event()
    release = asyncio.Event()

    class Slots:
        async def __aenter__(self):
            entered.set()
            await release.wait()

        async def __aexit__(self, *args):
            return None

    class Response(FakeResponse):
        async def text(self):
            clock[0] += 1
            return "ok"

    api = make_api(FakeSession(Response(0)), 2, monkeypatch)
    api._API__request_slots = Slots()
    monkeypatch.setattr(dc_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    caplog.set_level("WARNING", logger="app.services.dc.api")
    task = asyncio.create_task(api._API__request_text(
        "POST", "https://user:password@m.dcinside.com/private-path?secret=query",
        headers={"Authorization": "secret-header"}, data={"password": "secret-body"},
        cookies={"session": "secret-cookie"},
    ))
    await entered.wait()
    clock[0] = 3
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        assert await task == (200, {}, "ok")

    messages = slow_messages(caplog)
    assert len(messages) == 1
    assert "queue=3.00" in messages[0]
    assert ("upstream=0.00 outcome=cancelled" if cancel else "upstream=1.00 outcome=complete") in messages[0]
    for secret in ("user", "password", "private-path", "secret", "query"):
        assert secret not in messages[0]
