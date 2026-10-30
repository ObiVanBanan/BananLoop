"""Cheap, deterministic candidate checks before expensive DEV evaluation.

This check is deliberately narrow: syntax failures are implementation errors, not
scientific evidence against a hypothesis. It does not run imports, project tests,
or the frozen evaluator, and it never writes to the candidate workspace.
"""
from __future__ import annotations

import tokenize
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
