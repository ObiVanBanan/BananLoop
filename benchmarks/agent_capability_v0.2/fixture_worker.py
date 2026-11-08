"""Pure-stdlib, no-network-intended deterministic DEV fixture for the role runner.

Receives only the public role ID and agent-visible input on stdin. This process
is NOT a security sandbox and cannot replace the RPDI-14 isolated provider.
"""
from __future__ import annotations

import json
import sys


def respond(role: str, public: dict) -> dict:
    if role == "researcher":
        return {"selected": next((value for value in public["candidates"]
                                  if value not in public["refuted"]), None)}
    if role == "optimizer":
        if public["operation"] != "sum":
            raise ValueError("unknown public DEV operation")
        return {"result": sum(public["values"])}
    if role == "independent_verifier":
        return {"decision": "PASS" if public["pins_valid"] and public["isolation_valid"] else "REJECT"}
    if role == "integration_verifier":
        return {"decision": "ACCEPT" if all(public[name] for name in (
            "candidate_valid", "tests_pass", "provenance_valid")) else "REJECT"}
    raise ValueError("unsupported fixture role")


def main() -> int:
    payload = json.load(sys.stdin)
    print(json.dumps(respond(payload["role_id"], payload["agent_input"]), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
