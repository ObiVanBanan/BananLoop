from __future__ import annotations

import re


def added_diff_lines(diff_text: str) -> list[str]:
    """Return only candidate-added source lines from a unified Git diff."""
    lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        lines.append(line[1:])
    return lines


def scan_forbidden_diff_patterns(
    diff_text: str,
    patterns: list[str],
) -> list[str]:
    """Find configured anti-gaming patterns only in newly added diff lines."""
    if not patterns:
        return []
    added = added_diff_lines(diff_text)
    violations: list[str] = []
    for pattern in patterns:
        regex = re.compile(pattern)
        for line_number, line in enumerate(added, 1):
            if regex.search(line):
                excerpt = " ".join(line.strip().split())
                if len(excerpt) > 160:
                    excerpt = excerpt[:157] + "..."
                violations.append(
                    f"pattern={pattern!r} added_line={line_number}: {excerpt}"
                )
                break
    return violations
