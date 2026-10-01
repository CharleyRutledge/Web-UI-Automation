from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def resolve_env(value: Any) -> Any:
    """Replace ${VAR_NAME} placeholders with environment variables."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env(v) for v in value]
    return value


_DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(path: Path) -> list[str]:
    """Read KEY=value lines from a private .env file into the environment (for runs on your own computer).

    Variables already set in the environment win. Returns the names loaded (never the values).
    The file is git-ignored; see .env.example for the names this app uses.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    loaded = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _DOTENV_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()  # trailing comment on an unquoted value
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
