# Issue #33 review report: M0, first M1 slice, and M2 replay

## Implemented

- Fixed the Python 3.11 collection error in `tests/test_bootstrap.py` by building the fixture payload before interpolating it into generated source.
- Added the additive `EvaluationEvidence` contract with technical readiness, case and label sources, protocol and input fingerprints, runtime identity, readiness receipt hash, checked invariants, and explicit limitations.
- Bootstrap-generated case/label claims remain `UNVERIFIED`. A/A stability and signal separation are recorded as technical checks, not proof of business relevance.
- Campaign JSON and Markdown reports expose technical outcome, scientific assessment, and evaluator trust separately. A target-reaching `GOAL_MET` with unverified evidence is reported as scientific `UNKNOWN`.
- Evidence metadata is excluded from `CampaignSpec.approved_hash()` to preserve existing approved/action hashes.
- Added `bananloop replay --action ...` for completed DEV evaluator actions and bootstrap readiness smoke runs. Replay checks durable receipt linkage, candidate snapshot and proof, evaluator assets, runtime fingerprint, case/input identity, protocol/checks, aggregate and per-case metrics, and artifact hashes. A/A-derived metric tolerances are persisted with evidence.
- Bootstrap smoke runs now retain evaluator assets and result sidecars outside SQLite, with hashes and runtime metadata in readiness summaries. Runtime fingerprints include installed Python distributions and hashed executable-resolution environment variables.
- Replay writes a content-addressed receipt and result under the state directory, removes its disposable worktree, and does not alter campaign actions, evaluations, experiments, counters, or champion state. Validation and holdout actions are rejected.
- A/A readiness now records absolute and relative noise bounds for every aggregate metric, not only the primary metric. Campaign evidence carries both, and replay applies them to aggregate and per-case measurements. Legacy bootstrap receipts still fall back to the original primary/paired tolerances.
- Readiness now runs the full DEV evaluator once at the frozen baseline after repeated smoke runs. It requires the full case set to cover each smoke case with the same protocol, input identity, metrics, and per-case values within that case's measured A/A tolerance. A smoke subset is accepted when the shared cases match. Any per-case mismatch fails readiness and enters the bounded evaluator-repair path. When evaluators emit no per-case measurements, readiness records `INCONCLUSIVE` aggregate-contract evidence and keeps trust `UNVERIFIED`.
- Full baseline preflight has a separate receipt/result sidecar and runtime identity. Reports distinguish smoke repeats from full preflight evaluator calls so the extra bounded work is visible; bootstrap smoke replay verifies the combined smoke/DEV runtime.
- The E0 proxy control confirms that a scorer can separate bootstrap-labeled examples while remaining `UNVERIFIED`; reports preserve the diagnostic-only classification and a human-readable provenance limitation.

## Verification

- `python -m compileall -q src tests` passed.
- `pytest -q` passed on the local Python 3.14 runtime, including replay and bootstrap/readiness coverage.
- `git diff --check` passed before final review.
- GitHub Actions matrix passed on commit `940fa6e1220c014176b60c80bd64c127e7047434`: tests passed on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Smoke/full preflight changes are a later local follow-up and require a new matrix run before review.
- Full local `pytest -q`, `python -m compileall -q src tests examples/issue33`, and `git diff --check` pass for the smoke/full preflight follow-up.
- Deterministic ACCEPTED → REJECTED → ACCEPTED fixture, resume, export, and source-checkout invariants are covered by the existing full suite; no separate dogfood campaign was run for this change.

## Remaining M1, M2, and later work

DEV replay and strict smoke/full per-case equivalence are implemented. M1 remains partial: there is no core-verifiable, operator-approved reference manifest, and aggregate-only evaluators produce explicit `INCONCLUSIVE` equivalence. Evaluator bundle trust controls remain open. E0–E4 dogfood evidence is tracked separately; no independent-label trust upgrade is claimed.
