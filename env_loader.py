import os
from pathlib import Path
import re


ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquote_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(path=None, override=False):
    """Load simple KEY=VALUE pairs from a dotenv file without extra deps."""
    env_path = Path(path) if path else Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return False

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.match(key):
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _unquote_env_value(value)
    return True
