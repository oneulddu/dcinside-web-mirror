import os
from pathlib import Path
import re


ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _clean_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_env_line(raw_line):
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None, None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    key, value = line.split("=", 1)
    key = key.strip()
    if not ENV_KEY_RE.match(key):
        return None, None
    return key, _clean_env_value(value)


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
        key, value = _parse_env_line(raw_line)
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
    return True


def env_int(name, default, minimum=None, maximum=None):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(_clean_env_value(str(raw)))
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value
