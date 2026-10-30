"""Frozen CPU-only public DEV scorer. Do not expose this file to proposer."""

from solution import match

DEV = [
    ("requests must be idempotent", {"retry": "safe repeated operation", "cache": "reuse stored result"}, "retry"),
    ("reject stale writes", {"optimistic_lock": "reject outdated update", "replication": "copy data between nodes"}, "optimistic_lock"),
    ("Use API contract", {"queue": "queue contract for workers", "interface": "application programming interface"}, "interface"),
    ("cancel obsolete credential", {"revoke": "disable old key", "rotate": "rotate active credential"}, "revoke"),
    ("must not cache secrets", {"cache": "cache secrets safely", "privacy": "prevent sensitive data caching"}, "privacy"),
    ("compress before encrypt", {"encrypt_first": "encrypt before compress", "compress_first": "compress then encrypt"}, "compress_first"),
    ("at least once delivery", {"exactly_once": "exactly once delivery", "minimum_once": "at least one delivery"}, "minimum_once"),
    ("replicate state to secondary nodes", {"replicate": "replicate state to secondary nodes", "snapshot": "save a periodic local image"}, "replicate"),
]


def evaluate() -> float:
    return sum(match(q, choices) == expected for q, choices, expected in DEV) / len(DEV)


if __name__ == "__main__":
    print(f"quality={evaluate():.4f}")
