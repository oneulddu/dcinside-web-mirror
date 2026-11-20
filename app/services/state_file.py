"""Private sidecars and durable atomic JSON replacement for local state."""
import json
import os
import tempfile
from pathlib import Path


def lock_file(path, suffix):
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.fdopen(os.open(str(path) + suffix, os.O_CREAT | os.O_RDWR, 0o600), "r+b")


def save(path, state):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        try:
            json.dump(state, tmp, separators=(",", ":"))
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
