"""Minimal public baseline for the technical position matcher."""

import re


_NEGATION_CUES = {"not", "no", "never", "without", "neither", "nor", "lack", "lacks", "lacking"}
_BOUNDARIES = {"but", "however", "although", "yet"}


def normalize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _term_polarities(text: str) -> dict[str, bool]:
    """Map terms to polarity, applying nearby negation cues to their scope."""
    tokens = re.findall(r"[a-z0-9]+|[^a-z0-9\s]", text.lower())
    polarities: dict[str, bool] = {}
    negated = False
    scope = 0
    for token in tokens:
        if token in _BOUNDARIES or (len(token) == 1 and not token.isalnum()):
            negated = False
            scope = 0
            continue
        if token in _NEGATION_CUES:
            negated = True
            scope = 3
            continue
        if token.isalnum():
            polarities[token] = polarities.get(token, False) or negated
            if scope:
                scope -= 1
                if not scope:
                    negated = False
    return polarities


def match(query: str, choices: dict[str, str]) -> str:
    query_terms = normalize(query)
    query_polarities = _term_polarities(query)

    def score(key: str) -> int:
        choice_terms = normalize(choices[key])
        shared = query_terms & choice_terms
        choice_polarities = _term_polarities(choices[key])
        # An opposite polarity on a shared content term outweighs that term's
        # ordinary overlap credit; same-polarity overlap keeps baseline credit.
        conflicts = sum(
            query_polarities.get(term, False) != choice_polarities.get(term, False)
            for term in shared
        )
        return len(shared) - 2 * conflicts

    return max(choices, key=score)
