"""Minimal public baseline for the technical position matcher."""

import re


def normalize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def match(query: str, choices: dict[str, str]) -> str:
    query_terms = normalize(query)
    return max(choices, key=lambda key: len(query_terms & normalize(choices[key])))
