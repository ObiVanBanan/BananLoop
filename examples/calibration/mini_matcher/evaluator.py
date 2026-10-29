"""Fast, deterministic DEV-only evaluation for the matcher calibration task.

The agent workspace must not contain this file. The supervisor invokes it.
Do not use these cases as training examples or leak them into proposer context.
"""
from __future__ import annotations

import json

from matcher import find_analog


CATALOG = [
    {"article": "S20F16", "dn": 20, "pn": 16, "material": "steel", "joining_type": "flange"},
    {"article": "S25F16", "dn": 25, "pn": 16, "material": "steel", "joining_type": "flange"},
    {"article": "S20F25", "dn": 20, "pn": 25, "material": "steel", "joining_type": "flange"},
    {"article": "S20W16", "dn": 20, "pn": 16, "material": "steel", "joining_type": "weld"},
    {"article": "B20T16", "dn": 20, "pn": 16, "material": "brass", "joining_type": "thread"},
    {"article": "B15T16", "dn": 15, "pn": 16, "material": "brass", "joining_type": "thread"},
]

CASES = [
    ("Кран шаровой стальной Ду20 Ру16 фланцевый", "S20F16"),
    ("Кран шаровой Ду20 Ру16 сталь фланцевый", "S20F16"),
    ("Valve DN20 PN16 steel flange", "S20F16"),
    ("Кран стальной Ду25 Ру16 фланцевый", "S25F16"),
    ("Кран DN20 PN25 steel flange", "S20F25"),
    ("Кран латунный Ду20 Ру16 резьбовой", "B20T16"),
    ("Valve DN20 PN16 brass thread", "B20T16"),
    ("Кран латунный Ду15 Ру16 резьбовой", "B15T16"),
    ("Кран стальной Ду20 Ру16 сварной", "S20W16"),
    ("Кран стальной Ду32 Ру16 фланцевый", None),
    ("Кран Ду20 Ру16 фланцевый", None),
    ("Кран стальной Ду20 фланцевый", None),
    ("Кран стальной Ду20 Ру16", None),
    ("Кран стальной Ду25 Ру25 фланцевый", None),
    ("Кран латунный Ду20 Ру16 фланцевый", None),
    ("Кран латунный Ду20 Ру16 сварной", None),
]

correct = 0
valid = True
for query, expected in CASES:
    try:
        found = find_analog(query, [row.copy() for row in CATALOG])
    except Exception:
        valid = False
        continue
    if found is not None and not isinstance(found, str):
        valid = False
        continue
    if found == expected:
        correct += 1
print(json.dumps({
    "schema_version": 1,
    "status": "ok",
    "metrics": {"quality": correct / len(CASES)},
    "checks": {"cases_valid": valid},
    "diagnostics": {"correct": correct, "total": len(CASES)},
}))
