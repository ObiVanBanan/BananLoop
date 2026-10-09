from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "slug.py").write_text(
        "def slugify(value):\n"
        "    # Bug: consecutive spaces become consecutive separators.\n"
        "    return '-'.join(value.lower().split(' '))\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_slug.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from slug import slugify\n\n"
        "def test_collapses_repeated_spaces():\n"
        "    assert slugify('Alpha  Beta') == 'alpha-beta'\n",
        encoding="utf-8",
    )
    run("git", "init", cwd=root)
    run("git", "config", "user.email", "issue33-e1@example.invalid", cwd=root)
    run("git", "config", "user.name", "Issue 33 E1 Fixture", cwd=root)
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-m", "reproducible slug whitespace bug", cwd=root)
    print(run("git", "rev-parse", "HEAD", cwd=root))


if __name__ == "__main__":
    main()
