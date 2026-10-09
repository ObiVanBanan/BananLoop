# EvaluatorBinding security gate

## Outcome

No blocking or major security findings.

## Advisory

`EvaluatorBinding` stores evaluator command argv in SQLite and its JSON sidecar, and the runtime descriptor stores resolved executable paths. Since argv can contain inline credentials and absolute paths can reveal local usernames or installation layout, avoid secrets in command arguments and consider storing only redacted command fingerprints and path fingerprints in a later hardening step. The existing campaign spec already stores evaluator commands in SQLite, so the sidecar increases duplication/export exposure.

Receipts themselves contain identifiers and hashes only. Receipt/action/binding/result linkage is validated, sidecar paths are derived from action IDs, and evaluator input assets remain checked under the workspace boundary.
