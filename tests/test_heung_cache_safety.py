import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services import heung


def test_empty_heung_refresh_preserves_stale_memory_and_file_cache(monkeypatch):
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
