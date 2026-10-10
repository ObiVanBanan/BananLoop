# Issue #33 review report

Date: 2026-10-10
Reviewed HEAD: `35222568973b260c73ffd334545197f0b22d52d1` on `feat/issue-33-review-fixes` (published to PR #35 head branch).
PR: [#35](https://github.com/ObiVanBanan/BananLoop/pull/35) (open draft)

## Result

The current implementation separates technical campaign outcome, evaluator trust, and scientific assessment; records readiness and runtime provenance; checks smoke/full per-case equivalence; supports DEV replay; and provides durable explanation and stage accounting. Research selection uses evidence strength, failure class, available budget, mechanism/scope deduplication, and explicit causal records. Existing deterministic fixtures cover ACCEPTED → REJECTED → ACCEPTED, resume, export, and source-checkout preservation.

The operator reference path now accepts an exact-byte SHA-256-pinned JSON manifest with operator identity. Core verification matches protocol, full DEV case/repetition/input identity, baseline values, and repeated smoke values; verification and readiness receipts are retained. Reports re-check the persisted manifest, receipt, and readiness hashes and downgrade tampered/missing evidence to `UNVERIFIED`. Additive evidence fields remain excluded from approved spec hashes. A vertical bootstrap integration test exercises this complete path and a report test verifies downgrade on receipt mutation.

## Validation

- The four affected local suites (`test_replay.py`, `test_completion.py`, `test_readiness.py`, `test_bootstrap.py`) passed after the three review fixes. The focused replay/trust/accounting regressions also passed. `git diff --check` passed before commit.
- CI for `35222568973b260c73ffd334545197f0b22d52d1` is running as GitHub Actions [run 38033649216](https://github.com/ObiVanBanan/BananLoop/actions/runs/38033649216); do not treat the previous green run at an older commit as validation of this head.
- E0–E3 evidence and limits are recorded in `plan/issue-33-experiment-evidence.md`; E2 includes bounded paired DEV, separate product-group validation, replay, and exported-patch verification.
- An existing 56-generation real GPU AnimeDiff smoke is now recorded as partial E4 evidence: it proves the generic campaign path and rejection of a null candidate, not image-quality improvement. The protocol only measures coarse RGB fidelity on neutral geometric cases.

## Remaining acceptance gaps

- No real operator-approved reference manifest has been supplied for E1–E3. The test manifest verifies the mechanics only and does not upgrade those campaigns' trust.
- E2 still lacks human-reject labels and continues to have poor generic no-match behavior; its negatives are constructed controls.
- E3 is a single synthetic CPU workload on one runtime, not broad performance generalization.
- E4 has a historical real GPU run, but no current-head rerun or semantic/prompt-adherence/safety/human-review evidence. A1111 is not currently listening and the GPU is occupied.
- The issue requires actual domain campaigns and independent evidence. This report does not claim full DoD completion; issue #33 and PR #35 remain open, and PR #35 remains draft.
