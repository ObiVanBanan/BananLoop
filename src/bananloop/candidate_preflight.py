"""Cheap, deterministic candidate checks before expensive DEV evaluation.

This check is deliberately narrow: syntax failures are implementation errors, not
scientific evidence against a hypothesis. It does not run imports, project tests,
or the frozen evaluator, and it never writes to the candidate workspace.
"""
from __future__ import annotations

import tokenize
import subprocess
from pathlib import Path


def python_syntax_errors(workspace: Path, changed_paths: list[str]) -> list[str]:
    root = workspace.resolve()
    errors: list[str] = []
    for relative in sorted(set(changed_paths)):
        if not relative.endswith(".py"):
            continue
        file = workspace / relative
        if not file.exists():
            continue  # Deletions are evaluated by normal checks, not as syntax errors.
        if not file.is_file() or not file.resolve().is_relative_to(root):
            errors.append(f"{relative}: candidate path is not an internal regular file")
            continue
        try:
            with tokenize.open(file) as handle:
                source = handle.read()
            compile(source, relative, "exec", dont_inherit=True)
        except (SyntaxError, UnicodeError, OSError, ValueError) as exc:
            if isinstance(exc, SyntaxError):
                detail = f"{exc.msg} (line {exc.lineno})"
            else:
                detail = type(exc).__name__
            errors.append(f"{relative}: {detail}")
    return errors


def _legacy_one_hot_encoder_lines(source: str) -> list[int]:
    """Locate direct scikit-learn OneHotEncoder(sparse=...) calls via AST.

    Do not flag local functions named OneHotEncoder or execute candidate imports.
    This is intentionally a narrow, high-confidence check.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # The existing Python syntax gate handles this.

    direct: set[str] = set()
    module_prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "sklearn.preprocessing":
                direct.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name == "OneHotEncoder"
                )
            elif node.module == "sklearn":
                module_prefixes.update(
                    (alias.asname or alias.name) + ".OneHotEncoder"
                    for alias in node.names if alias.name == "preprocessing"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sklearn.preprocessing":
                    module_prefixes.add(
                        (alias.asname + ".OneHotEncoder") if alias.asname
                        else "sklearn.preprocessing.OneHotEncoder"
                    )

    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not any(
            kw.arg == "sparse" for kw in node.keywords
        ):
            continue
        if isinstance(node.func, ast.Name):
            matched = node.func.id in direct
        else:
            matched = ast.unparse(node.func) in module_prefixes
        if matched:
            lines.append(node.lineno)
    return sorted(set(lines))


def python_api_compatibility_errors(
    workspace: Path,
    changed_paths: list[str],
    *,
    evaluator_command: list[str],
) -> list[str]:
    """Reject known unsupported API calls against the *evaluator* interpreter.

    The dependency probe runs isolated Python metadata discovery, not candidate
    code, modules, imports, tests or scoring. Unknown runtime => no hard reject.
    This is a narrow preflight, not a substitute for integration smoke tests.
    """
    import re

    root = workspace.resolve()
    locations: list[tuple[str, int]] = []
    for relative in sorted(set(changed_paths)):
        if not relative.endswith(".py"):
            continue
        file = (workspace / relative).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            continue
        try:
            with tokenize.open(file) as handle:
                locations.extend(
                    (relative, line)
                    for line in _legacy_one_hot_encoder_lines(handle.read())
                )
        except (OSError, UnicodeError, ValueError):
            continue
    if not locations or not evaluator_command:
        return []

    # Never infer the runtime version from the BananLoop supervisor Python.
    # When the evaluator uses a wrapper (uv, bash, etc.), skip this gate.
    interpreter = str(evaluator_command[0])
    name = interpreter.replace("\\", "/").rsplit("/", 1)[-1]
    launcher_match = re.fullmatch(
        r"py([0-9]+(?:\.[0-9]+)*)?(?:\.exe)?", name, flags=re.IGNORECASE,
    )
    direct_python = re.fullmatch(
        r"pythonw?(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?",
        name, flags=re.IGNORECASE,
    )
    if not launcher_match and not direct_python:
        return []

    probe_command = [interpreter]
    if launcher_match:
        # Preserve an explicit Windows Python Launcher selector. Probing plain
        # `py` without `-3.12` can inspect a different runtime than the evaluator.
        selector = launcher_match.group(1)
        if selector:
            probe_command.append(f"-{selector}")
        elif len(evaluator_command) > 1 and re.fullmatch(
            r"-(?:[0-9]+(?:\.[0-9]+)*(?:-(?:32|64))?|V:.+)",
            str(evaluator_command[1]), flags=re.IGNORECASE,
        ):
            probe_command.append(str(evaluator_command[1]))
    try:
        result = subprocess.run(
            [
                *probe_command, "-I", "-c",
                "import importlib.metadata as m; print(m.version('scikit-learn'))",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    match = re.match(r"^(\d+)\.(\d+)", result.stdout.strip())
    if not match or (int(match.group(1)), int(match.group(2))) < (1, 4):
        return []

    version = result.stdout.strip()
    return [
        f"{relative}: OneHotEncoder(sparse=...) at line {line} is unsupported "
        f"by evaluator scikit-learn {version}; use sparse_output= instead"
        for relative, line in locations
    ]
