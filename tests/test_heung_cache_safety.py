import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from requests import Response, Session

from app.services import heung, youtube_meta


@pytest.mark.parametrize("source", ["heung", "youtube"])
def test_metadata_lookups_reuse_transport_not_cookies(monkeypatch, source):
    adapters = []
    proxy = "http://metadata-proxy.invalid:8080"
    monkeypatch.setenv("https_proxy", proxy)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")

    def send(session, request, **kwargs):
        adapters.append(session.get_adapter(request.url))
        assert kwargs["proxies"]["https"] == proxy
        assert "Cookie" not in request.headers
        session.cookies.set("probe", "discard")
        response = Response()
        response.status_code = 200
        response._content = b"<div id='heung_gall_all_lyr'></div>"
        response._content_consumed = True
        return response

    monkeypatch.setattr(Session, "send", send)
    for _ in range(2):
        if source == "heung":
            heung._fetch_heung_galleries()
        else:
            youtube_meta.probe_shorts_orientation("abcdefghijk")
    assert adapters[0] is adapters[1]


def test_empty_heung_refresh_preserves_stale_memory_and_file_cache(monkeypatch):
    monkeypatch.setattr(heung, "HEUNG_NEXT_RETRY_AT", 0.0)
    stale_items = [{"rank": 1, "name": "기존 목록", "board_id": "stale"}]
    writes = []

    monkeypatch.setattr(heung, "HEUNG_CACHE", {"updated_at": 100.0, "items": stale_items})
    monkeypatch.setattr(heung, "HEUNG_CACHE_LOCK", threading.Lock())
    monkeypatch.setattr(heung, "_fetch_heung_galleries", lambda: [])
    monkeypatch.setattr(
        heung,
        "_write_heung_cache_file",
        lambda updated_at, items: writes.append((updated_at, items)),
    )

    with pytest.raises(RuntimeError, match="empty heung gallery result"):
        heung._refresh_heung_galleries()

    assert heung._heung_cache_snapshot() == (stale_items, 100.0)
    assert writes == []


def test_heung_cache_writers_use_unique_atomic_temp_files(monkeypatch, tmp_path):
    cache_file = tmp_path / "heung.json"
    replace_barrier = threading.Barrier(2)
    replace_sources = []
    real_replace = os.replace

    def synchronized_replace(src, dst):
        replace_sources.append(src)
        replace_barrier.wait(timeout=5)
        real_replace(src, dst)

    monkeypatch.setattr(heung, "HEUNG_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(heung.os, "replace", synchronized_replace)
    payloads = [
        (100.0, [{"rank": 1, "name": "첫 번째", "board_id": "first"}]),
        (200.0, [{"rank": 1, "name": "두 번째", "board_id": "second"}]),
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(heung._write_heung_cache_file, updated_at, items)
            for updated_at, items in payloads
        ]
        for future in futures:
            future.result(timeout=5)

    assert len(set(replace_sources)) == 2
    assert all(Path(source).parent == tmp_path for source in replace_sources)
    assert all(not Path(source).exists() for source in replace_sources)
    with cache_file.open("r", encoding="utf-8") as file_obj:
        final_payload = json.load(file_obj)
    assert final_payload in [
        {"updated_at": updated_at, "items": items}
        for updated_at, items in payloads
    ]
