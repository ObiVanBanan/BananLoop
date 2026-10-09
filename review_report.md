# Issue #33 review report

Date: 2026-10-09
Review base: `0690db48065cc91479836e3b7ac73980ddb2355f` on `feat/issue-33-evidence-aware-research`; this report covers the receipt-persistence follow-up in the current diff.
PR: [#35](https://github.com/ObiVanBanan/BananLoop/pull/35) (open draft)

## Result

The current implementation separates technical campaign outcome, evaluator trust, and scientific assessment; records readiness and runtime provenance; checks smoke/full per-case equivalence; supports DEV replay; and provides durable explanation and stage accounting. Research selection uses evidence strength, failure class, available budget, mechanism/scope deduplication, and explicit causal records. Existing deterministic fixtures cover ACCEPTED → REJECTED → ACCEPTED, resume, export, and source-checkout preservation.

The operator reference path now accepts an exact-byte SHA-256-pinned JSON manifest with operator identity. Core verification matches protocol, full DEV case/repetition/input identity, baseline values, and repeated smoke values; verification and readiness receipts are retained. Reports re-check the persisted manifest, receipt, and readiness hashes and downgrade tampered/missing evidence to `UNVERIFIED`. Additive evidence fields remain excluded from approved spec hashes. A vertical bootstrap integration test exercises this complete path and a report test verifies downgrade on receipt mutation.

## Validation

- Full local `pytest -q` passes on the current worktree; `python -m compileall -q src/bananloop` and `git diff --check` pass.
- GitHub Actions [run 37893403783](https://github.com/ObiVanBanan/BananLoop/actions/runs/37893403783) passed Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12 at the reviewed HEAD.
- E0–E3 evidence and limits are recorded in `plan/issue-33-experiment-evidence.md`; E2 includes bounded paired DEV, separate product-group validation, replay, and exported-patch verification.

## Remaining acceptance gaps

- No real operator-approved reference manifest has been supplied for E1–E3. The test manifest verifies the mechanics only and does not upgrade those campaigns' trust.
- E2 still lacks human-reject labels and continues to have poor generic no-match behavior; its negatives are constructed controls.
- E3 is a single synthetic CPU workload on one runtime, not broad performance generalization.
- E4 cannot run here: no GPU/A1111 runtime or frozen AnimeDiff benchmark is present.
- The issue requires actual domain campaigns and independent evidence. This report does not claim full DoD completion; issue #33 and PR #35 remain open, and PR #35 remains draft.
