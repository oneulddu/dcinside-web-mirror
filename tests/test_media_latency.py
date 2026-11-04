from types import SimpleNamespace

import pytest
from flask import Flask

from app.services import media_proxy


@pytest.fixture
def media_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(media_proxy, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


class Upstream:
    status_code = 200
    is_redirect = False

    def __init__(self, clock, headers, fail=False):
        self.clock = clock
        self.headers = headers
        self.closed = False
        self.read = False
        self.fail = fail

    def iter_content(self, chunk_size):
        self.read = True
        self.clock[0] += 3
        if self.fail:
            raise OSError("private body error")
        yield b"private body"

    def close(self):
        self.clock[0] += 1
        self.closed = True


def install_upstream(monkeypatch, clock, upstream):
    def fetch(*args, **kwargs):
        clock[0] += 2
        return upstream

    monkeypatch.setattr(media_proxy, "_http_get", fetch)
    monkeypatch.setattr(media_proxy, "_http_head", fetch)


def messages(caplog):
    logs = [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("slow media:")]
    assert all("private" not in log and "secret" not in log and "?" not in log for log in logs)
    return logs


def build(**kwargs):
    return media_proxy.build_media_response(
        "https://dcimg4.dcinside.co.kr/private-path?secret=private-query",
        "private-board", "private-pid", **kwargs,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_buffered_media_separates_fetch_body_and_local_processing(monkeypatch, caplog, media_clock, fail):
    upstream = Upstream(media_clock, {"Content-Type": "image/png"}, fail=fail)
    install_upstream(monkeypatch, media_clock, upstream)
    response = build()

    assert response.status_code == (502 if fail else 200)
    if not fail:
        assert response.data == b"private body"
    assert upstream.closed
    logs = messages(caplog)
    assert len(logs) == 1
    assert "phase=prepare elapsed=6.00 fetch=2.00 body=3.00 local=1.00" in logs[0]


@pytest.mark.parametrize("spooled", [False, True])
@pytest.mark.parametrize("disconnect", [False, True])
def test_stream_timing_excludes_consumer_pause_from_upstream_body(monkeypatch, caplog, media_clock, spooled, disconnect):
    headers = {"Content-Type": "video/mp4"}
    if not spooled:
        headers["Content-Length"] = "12"
    upstream = Upstream(media_clock, headers)
    install_upstream(monkeypatch, media_clock, upstream)
    with Flask(__name__).test_request_context():
        response = build()
        assert upstream.read is spooled
        iterator = iter(response.response)
        assert next(iterator) == b"private body"
        media_clock[0] += 5
        if not disconnect:
            assert list(iterator) == []
        response.close()

    assert upstream.closed
    logs = messages(caplog)
    assert len(logs) == 2
    assert logs[0].split("operation=")[1].split()[0] == logs[1].split("operation=")[1].split()[0]
    expected = ("phase=spool_stream iterator=5.00 body=0.00" if spooled
                else "phase=upstream_stream iterator=9.00 body=3.00")
    assert expected in logs[1]


def test_head_reports_fetch_without_consuming_body(monkeypatch, caplog, media_clock):
    upstream = Upstream(media_clock, {"Content-Type": "image/png", "Content-Length": "12"})
    install_upstream(monkeypatch, media_clock, upstream)
    response = build(head_only=True)

    assert response.data == b""
    assert response.headers["Content-Length"] == "12"
    assert upstream.closed and not upstream.read
    logs = messages(caplog)
    assert len(logs) == 1
    assert "phase=prepare elapsed=3.00 fetch=2.00 body=0.00 local=1.00" in logs[0]


def test_fast_media_does_not_emit_latency_log(monkeypatch, caplog, media_clock):
    upstream = Upstream(media_clock, {"Content-Type": "image/png", "Content-Length": "12"})
    monkeypatch.setattr(media_proxy, "_http_head", lambda *args, **kwargs: upstream)
    assert build(head_only=True).status_code == 200
    assert messages(caplog) == []
