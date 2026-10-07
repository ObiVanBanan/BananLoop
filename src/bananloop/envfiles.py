from __future__ import annotations

import re
from pathlib import Path


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_keys_from_repo(repo: Path) -> list[str]:
    """Return assignment names from the root .env without retaining values."""
    try:
        lines = (repo / ".env").read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return []
    keys: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, _ = line.partition("=")
        key = key.strip()
        if separator and _KEY.fullmatch(key):
            keys.add(key)
    return sorted(keys)
