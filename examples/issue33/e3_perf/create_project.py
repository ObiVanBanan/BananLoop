from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "dedupe.py").write_text(
        "def unique_count(values):\n"
        "    seen = []\n"
        "    for value in values:\n"
        "        if value not in seen:\n"
        "            seen.append(value)\n"
        "    return len(seen)\n",
        encoding="utf-8",
    )
    run("git", "init", cwd=root)
    run("git", "config", "user.email", "issue33-e3@example.invalid", cwd=root)
    run("git", "config", "user.name", "Issue 33 E3 Fixture", cwd=root)
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-m", "baseline list-based duplicate counter", cwd=root)
    print(run("git", "rev-parse", "HEAD", cwd=root))


if __name__ == "__main__":
    main()
