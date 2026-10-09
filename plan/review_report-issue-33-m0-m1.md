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

## Verification

- `python -m compileall -q src tests` passed.
- `pytest -q` passed on the local Python 3.14 runtime, including replay and bootstrap/readiness coverage.
- `git diff --check` passed before final review.
- GitHub Actions matrix passed on commit `a758f0229ced536db743ff736b9061550b05a276`: 224 passed, zero skipped on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12.
- Deterministic ACCEPTED → REJECTED → ACCEPTED fixture, resume, export, and source-checkout invariants are covered by the existing full suite; no separate dogfood campaign was run for this change.

## Remaining M1, M2, and later work

Replay is implemented as a local diagnostic, but independent operator-approved reference provenance, smoke/full evaluator equivalence, and evaluator bundle trust controls remain open. Cross-platform CI for this replay increment is pending. E0–E4 dogfood evidence is tracked separately and has not been claimed here.
