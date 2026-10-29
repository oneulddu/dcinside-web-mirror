import asyncio

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
    assert "host=m.dcinside.com path=/board/test status=200 elapsed=" in messages[0]
    assert messages[0].endswith("scope=none")
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
