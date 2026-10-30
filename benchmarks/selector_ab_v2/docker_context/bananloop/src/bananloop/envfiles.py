from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_assignments(path: Path) -> dict[str, str]:
    """Parse simple dotenv assignments without logging or persisting values."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {}

    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def env_keys_from_repo(repo: Path) -> list[str]:
    """Return dotenv assignment names without retaining their values."""
    keys: set[str] = set()
    for name in (".env", ".env.local"):
        keys.update(_env_assignments(repo / name))
    return sorted(keys)


def project_environment(
    repo: Path,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build trusted evaluator environment; explicit process env wins over dotenv."""
    file_values: dict[str, str] = {}
    for name in (".env", ".env.local"):
        file_values.update(_env_assignments(repo / name))
    inherited = dict(os.environ if base is None else base)
    return {**file_values, **inherited}
