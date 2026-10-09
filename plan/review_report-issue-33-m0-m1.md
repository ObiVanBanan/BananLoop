# Issue #33 review report: M0 and first M1 slice

## Implemented

- Fixed the Python 3.11 collection error in `tests/test_bootstrap.py` by building the fixture payload before interpolating it into generated source.
- Added the additive `EvaluationEvidence` contract with technical readiness, case and label sources, protocol and input fingerprints, runtime identity, readiness receipt hash, checked invariants, and explicit limitations.
- Bootstrap-generated case/label claims remain `UNVERIFIED`. A/A stability and signal separation are recorded as technical checks, not proof of business relevance.
- Campaign JSON and Markdown reports expose technical outcome, scientific assessment, and evaluator trust separately. A target-reaching `GOAL_MET` with unverified evidence is reported as scientific `UNKNOWN`.
- Evidence metadata is excluded from `CampaignSpec.approved_hash()` to preserve existing approved/action hashes.

## Verification

- `python -m compileall -q src tests` passed.
- `pytest -q` passed on the local Python 3.14 runtime.
- `git diff --check` passed before final review.
- GitHub Actions Python 3.11/3.12/3.13 and Windows 3.12 matrix: pending on the updated pull request head.
- Deterministic ACCEPTED → REJECTED → ACCEPTED fixture, resume, export, and source-checkout invariants are covered by the existing full suite; no separate dogfood campaign was run for this change.

## Remaining M1 and later work

This is the first vertical slice, not completion of issue #33. Independent operator-approved reference provenance, smoke/full evaluator equivalence, evaluator bundle trust controls, and replay remain open. E0–E4 dogfood evidence is tracked separately and has not been claimed here.
