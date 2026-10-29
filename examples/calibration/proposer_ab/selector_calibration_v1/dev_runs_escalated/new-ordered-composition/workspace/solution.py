"""Minimal public baseline for the technical position matcher."""

import re


def normalize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def ordered_phrases(text: str) -> set[tuple[str, str]]:
    """Return adjacent normalized word pairs to retain local word order."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return set(zip(words, words[1:]))


def match(query: str, choices: dict[str, str]) -> str:
    query_terms = normalize(query)
    query_phrases = ordered_phrases(query)

    # Keep token overlap as the main signal. Adjacent phrase overlap resolves
    # ties where the same technical terms appear in different compositions.
    return max(
        choices,
        key=lambda key: (
            len(query_terms & normalize(choices[key])),
            len(query_phrases & ordered_phrases(choices[key])),
        ),
    )
