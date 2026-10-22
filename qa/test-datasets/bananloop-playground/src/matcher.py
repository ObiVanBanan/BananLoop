"""Baseline catalog matcher for the BananLoop playground."""
import re

def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9]+", text.casefold()))

def best_match(query: str, products: list[dict[str, str]]) -> str | None:
    query_tokens = tokens(query)
    if not query_tokens:
        return None
    scored = []
    for product in products:
        product_tokens = tokens(product["name"])
        score = len(query_tokens & product_tokens) / max(1, len(query_tokens | product_tokens))
        scored.append((score, product["product_id"]))
    return max(scored, default=(0.0, None))[1]
