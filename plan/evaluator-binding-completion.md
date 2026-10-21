# EvaluatorBinding milestone completion

## Outcome

Implemented the P1 EvaluatorBinding and evaluation receipt milestone on `codex/evaluator-binding-v1`, based on PR #32. The existing `CampaignSpec` schema/hash and action request hashes remain unchanged. SQLite stores the immutable campaign binding and one receipt per newly completed evaluator action; canonical sidecars are written alongside the action artifacts. Reports distinguish valid, invalid, orphaned, and legacy unreceipted records.

Replay/report validation recomputes the receipt, binding, candidate proof, case/input, result, and artifact manifest hashes and checks their links to the completed action and tier. Evaluator asset integrity is rechecked after execution. A runtime mismatch pauses as infrastructure.

## Verification

- Targeted post-review regression selection: `8 passed, 29 deselected` (`tests/test_engine.py`, evaluator binding/receipt/replay/tamper/runtime/asset-drift cases).
- Implementer’s post-fix engine/report/accounting selection: `9 passed`.
- Before final review fixes, the full suite reported `150 passed, 2 failed`; both failures were the existing Windows child-process tree timeout/stop tests in `tests/test_process.py`. The same two failures were present at the P0 baseline.
- Reviewer found two provenance validation gaps and one Markdown delimiter defect; all three were repaired and covered by regression tests. No second review pass was run.
- Focused security gate: no blocking or major findings. Advisory: binding DB/sidecar duplicates raw evaluator argv and absolute resolved runner paths, which may expose secrets embedded in argv or local usernames/paths. Avoid embedding secrets in command arguments; this should be revisited by redacting or fingerprinting command/runtime paths in a later hardening change.
- `git diff --check` passed. Python compile check passed per implementer.

## Rollback

Revert the feature commit when one exists, or discard the milestone files/changes on this branch. The change is additive to SQLite schema and does not rewrite existing campaign spec hashes or action request hashes. Preserve the pre-existing user changes in `src/bananloop/agents/codex.py`, `.tmp/`, `bananloop_experiments_rag-tender-night2.zip`, and `uv.lock` during any rollback.

## Remaining gaps

Stochastic readiness/calibration remains the next roadmap stage. No commit or push was requested or performed. A final rerun of the complete suite after the last review repairs was not performed; the post-repair targeted regressions passed.
