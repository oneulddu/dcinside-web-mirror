"""Measure service-call fan-out and waiter latency without network traffic."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--callers", type=int, default=32)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    if args.callers < 2 or args.rounds < 1:
        parser.error("--callers must be >= 2 and --rounds must be >= 1")
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ.setdefault("MIRROR_ENV", "development")
    from app.services import core
    from app.services.dc.models import DocumentIndex

    async def measure(name):
        calls = 0
        ready_at = 0.0
        burst_calls, burst_ms, waiter_ms = [], [], []
        row = DocumentIndex(
            id="123", board_id="benchmark", title="benchmark", has_image=False,
            author="익명", author_id=None, time="2026-09-05 12:00",
            view_count=0, comment_count=0, voteup_count=0, document=None,
            comments=None, subject=None, isimage=False, isrecommend=False,
            isdcbest=False, ishit=False,
        )

        async def simulate_fetch():
            nonlocal calls, ready_at
            calls += 1
            await asyncio.sleep(0.005)
            ready_at = time.perf_counter()

        class FakeAPI:
            async def board(self, **kwargs):
                await simulate_fetch()
                yield row

            async def board_precise_times(self, **kwargs):
                await simulate_fetch()
                return {"123": "2026-09-05 12:00"}

        @asynccontextmanager
        async def api_context():
            yield FakeAPI()

        async def read_payload(*args, **kwargs):
            await simulate_fetch()
            return {"html": "body", "related_posts": [], "_comments_complete": True}, [], []

        async def request():
            if name == "board_index":
                await core.async_index_with_head_categories(1, "benchmark", 0)
            elif name == "related_page":
                await core._fetch_board_page(FakeAPI(), 1, "benchmark", 0)
            elif name == "precise_times":
                await core.async_board_precise_times(1, "benchmark", 0, target_ids=["123"])
            else:
                await core.async_read("123", "benchmark")
            return time.perf_counter()

        with patch.object(core, "dc_api_context", api_context), patch.object(core, "_load_read_payload", read_payload):
            for _ in range(args.rounds):
                for cache in (core._BOARD_INDEX_CACHE, core._BOARD_PAGE_CACHE, core._BOARD_TIME_CACHE,
                              core._READ_CACHE, core._READ_STALE_CACHE):
                    cache.clear()
                calls = 0
                start = time.perf_counter()
                completed = await asyncio.gather(*(request() for _ in range(args.callers)))
                burst_calls.append(calls)
                burst_ms.append((max(completed) - start) * 1000)
                if name == "read":
                    waiter_ms.extend((finished - ready_at) * 1000 for finished in completed[1:])
        result = {
            "calls_per_burst": statistics.median(burst_calls),
            "median_burst_ms": round(statistics.median(burst_ms), 3),
        }
        if waiter_ms:
            result["median_waiter_notification_ms"] = round(statistics.median(waiter_ms), 3)
        return result

    async def run():
        results = {}
        for name in ("board_index", "related_page", "precise_times", "read"):
            results[name] = await measure(name)
        return results

    print(json.dumps({
        "python": sys.version.split()[0], "callers": args.callers, "rounds": args.rounds,
        "simulated_fetch_ms": 5, "results": asyncio.run(run()),
    }, indent=2))


if __name__ == "__main__":
    main()
