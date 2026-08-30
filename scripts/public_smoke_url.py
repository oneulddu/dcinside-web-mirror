#!/usr/bin/env python3
import os
from pathlib import Path
import sys
from urllib.parse import urljoin, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from env_loader import load_dotenv


def build_public_smoke_url(value=None):
    base_url = str(value or "").strip()
    if not base_url:
        return ""
    try:
        parsed = urlparse(base_url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("MIRROR_PUBLIC_BASE_URL must be a public https base URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in base_url)
        or port not in (None, 443)
    ):
        raise ValueError("MIRROR_PUBLIC_BASE_URL must be a public https base URL")
    return urljoin(base_url.rstrip("/") + "/", "recent")


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        smoke_url = build_public_smoke_url(os.getenv("MIRROR_PUBLIC_BASE_URL", ""))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if smoke_url:
        print(smoke_url)


if __name__ == "__main__":
    main()
