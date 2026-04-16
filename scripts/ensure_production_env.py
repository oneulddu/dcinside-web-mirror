#!/usr/bin/env python3
import os
from pathlib import Path
import secrets
import stat


ENV_PATH = Path(".env")


def _clean_value(value):
    return (value or "").strip().strip("'\"").strip()


def _has_non_empty(lines, name):
    prefix = f"{name}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and _clean_value(stripped[len(prefix):]):
            return True
    return bool(_clean_value(os.environ.get(name, "")))


def ensure_production_env(path=ENV_PATH):
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    if not _has_non_empty(lines, "MIRROR_ENV"):
        lines.append("MIRROR_ENV=production")
    if not _has_non_empty(lines, "MIRROR_SECRET_KEY"):
        lines.append(f"MIRROR_SECRET_KEY={secrets.token_urlsafe(48)}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


if __name__ == "__main__":
    ensure_production_env()
