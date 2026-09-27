#!/usr/bin/env python3
"""Check public Pokergosu parsers without creating a Flask application.

Run from the repo with:
uv run --no-project --python 3.12 --with-requirements requirements-dev.txt python scripts/poker_smoke.py
Uses MIRROR_POKER_STATE_FILE and the same pacing/cooldown as production.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.poker_boards import BOARDS
from app.services.pokergosu import PokerError, Reader


def main():
    reader = Reader()
    failed = False
    for board_id, spec in BOARDS.items():
        if spec.get('login_required'):
            continue
        try:
            time.sleep(0.4)
            listing = reader.board(1, board_id)
            if not listing['posts']:
                raise PokerError('첫 페이지 글이 없어요.')
            pid = listing['posts'][0]['id']
            time.sleep(0.4)
            reader.post(pid, board_id)
            print(f'{board_id}: OK posts={len(listing["posts"])} pid={pid}', flush=True)
        except PokerError as exc:
            failed = True
            print(f'{board_id}: FAIL status={exc.status} {exc}', flush=True)
        except Exception as exc:
            failed = True
            print(f'{board_id}: FAIL {type(exc).__name__}', flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
