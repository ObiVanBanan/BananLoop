"""Minimal public baseline for the technical position matcher."""

import re


# Collapse a small set of common technical paraphrases onto shared concepts.
# Keep this mapping explicit and conservative to limit accidental matches.
_SYNONYM_GROUPS = (
    ("build", "compile", "compilation"),
    ("debug", "troubleshoot", "diagnose"),
    ("deploy", "release", "rollout"),
    ("monitor", "observe", "observability"),
    ("database", "db"),
    ("authentication", "auth", "login"),
    ("authorization", "permissions", "access-control"),
    ("application-programming-interface", "api"),
    ("container", "docker"),
    ("orchestration", "kubernetes", "k8s"),
    ("version-control", "git"),
    ("continuous-integration", "ci"),
    ("continuous-delivery", "continuous-deployment", "cd"),
    ("reliability", "availability"),
    ("latency", "response-time"),
    ("throughput", "capacity"),
)
_CANONICAL_TOKEN = {
    token: group[0]
    for group in _SYNONYM_GROUPS
    for token in group
}


def normalize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    normalized = set(tokens)
    # Handle multiword forms after tokenization while retaining all original
    # tokens, so canonicalization adds matches without erasing useful detail.
    for group in _SYNONYM_GROUPS:
        for phrase in group:
            phrase_tokens = phrase.split("-")
            if len(phrase_tokens) > 1 and _contains_phrase(tokens, phrase_tokens):
                normalized.add(group[0])
    normalized.update(_CANONICAL_TOKEN[token] for token in tokens if token in _CANONICAL_TOKEN)
    return normalized


def _contains_phrase(tokens: list[str], phrase: list[str]) -> bool:
    width = len(phrase)
    return any(tokens[index:index + width] == phrase for index in range(len(tokens) - width + 1))


def match(query: str, choices: dict[str, str]) -> str:
    query_terms = normalize(query)
    return max(choices, key=lambda key: len(query_terms & normalize(choices[key])))
