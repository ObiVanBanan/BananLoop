from __future__ import annotations

import json
from pathlib import Path

from .workspace import current_commit


_ENTRYPOINT_CANDIDATES = (
    "generate.py",
    "main.py",
    "app.py",
    "server.py",
    "cli.py",
    "manage.py",
)
_DEPENDENCY_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "package.json",
    "Cargo.toml",
    "go.mod",
)
_TEST_PATHS = (
    "tests",
    "test",
    "pytest.ini",
    "tox.ini",
)
_BUILD_FILES = (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Makefile",
)


def discover_repository(repo: Path, goal: str | None = None) -> dict:
    """Return a deterministic, non-mutating repository inventory.

    Discovery intentionally does not invent an evaluator. It exposes enough
    facts for a human or a campaign-architect agent to propose a CampaignSpec
    without changing the target repository.
    """

    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"not a Git repository: {repo}")

    files = {path.name for path in repo.iterdir() if path.is_file()}
    dirs = {path.name for path in repo.iterdir() if path.is_dir()}

    entrypoints = [name for name in _ENTRYPOINT_CANDIDATES if name in files]
    dependencies = [name for name in _DEPENDENCY_FILES if name in files]
    tests = [name for name in _TEST_PATHS if name in files or name in dirs]
    build = [name for name in _BUILD_FILES if name in files]

    suggested_commands: list[list[str]] = []
    if "pyproject.toml" in files or "pytest.ini" in files or "tests" in dirs:
        suggested_commands.append(["uv", "run", "pytest", "-q"])
        suggested_commands.append(["python", "-m", "pytest", "-q"])
    if "package.json" in files:
        suggested_commands.append(["npm", "test"])
    if "generate.py" in files:
        suggested_commands.append(["python", "generate.py"])
    elif "main.py" in files:
        suggested_commands.append(["python", "main.py"])

    likely_protected = [
        item
        for item in (
            "tests/**" if "tests" in dirs else None,
            "test/**" if "test" in dirs else None,
            ".github/**" if ".github" in dirs else None,
        )
        if item is not None
    ]

    return {
        "schema_version": 1,
        "repo": str(repo),
        "base_commit": current_commit(repo),
        "goal": goal,
        "entrypoints": entrypoints,
        "dependency_files": dependencies,
        "test_surfaces": tests,
        "build_surfaces": build,
        "suggested_commands": suggested_commands,
        "suggested_editable_scope": ["**"],
        "suggested_protected_paths": likely_protected,
        "evaluator_status": "needs_explicit_trusted_evaluator",
        "notes": [
            "Discovery is read-only.",
            "The evaluator must be supplied/reviewed and frozen before adaptive experiments.",
            "Domain-specific scoring stays outside BananLoop core.",
        ],
    }


def render_discovery(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
