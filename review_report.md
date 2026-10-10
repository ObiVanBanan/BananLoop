# Issue #33 review report

Date: 2026-10-10
Reviewed implementation HEAD: `47f04f1f522c35d0ed51c4d0854583923bb4c72d` on `feat/issue-33-evidence-aware-research`.
PR: [#35](https://github.com/ObiVanBanan/BananLoop/pull/35) (open draft)

## Result

The current implementation separates technical campaign outcome, evaluator trust, and scientific assessment; records readiness and runtime provenance; checks smoke/full per-case equivalence; supports DEV replay; and provides durable explanation and stage accounting. Research selection uses evidence strength, failure class, available budget, mechanism/scope deduplication, and explicit causal records. Existing deterministic fixtures cover ACCEPTED → REJECTED → ACCEPTED, resume, export, and source-checkout preservation.

The operator reference path now accepts an exact-byte SHA-256-pinned JSON manifest with operator identity. Core verification matches protocol, full DEV case/repetition/input identity, baseline values, and repeated smoke values; verification and readiness receipts are retained. Reports re-check the persisted manifest, receipt, and readiness hashes and downgrade tampered/missing evidence to `UNVERIFIED`. Additive evidence fields remain excluded from approved spec hashes. A vertical bootstrap integration test exercises this complete path and a report test verifies downgrade on receipt mutation.

## Validation

- The four focused replay/readiness regression tests pass locally. `compileall` and `git diff --check` pass. A local attempt at engine/report integration tests cannot start Git Bash child processes in this sandbox (`NtCreateDirectoryObject` permission failure during `git clone`); the same full suite is covered by CI.
- GitHub Actions [run 38039245343](https://github.com/ObiVanBanan/BananLoop/actions/runs/38039245343) passed Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12 at `47f04f1`. The preceding [run 38039112619](https://github.com/ObiVanBanan/BananLoop/actions/runs/38039112619) at `498e1ce` exposed a test-fixture initialization error (258 passed, two failed); the fixture was fixed and the complete matrix passed on the next commit. The earlier full matrix [run 38038341831](https://github.com/ObiVanBanan/BananLoop/actions/runs/38038341831) also passed at `4ea8155`.
- M5 includes `bananloop trace export`, a read-only and deterministic portable JSONL/JSON/Markdown package with hash manifest, safe allowlisting, redaction, legacy-schema and campaign-state coverage, and refusal to overwrite exports containing extra files or malformed manifests. The summary includes metric deltas, budgets, and path-free evaluator/runtime fingerprints. Role-dispatch tests verify strong/high and fast/low selection, identical launch receipts after resume, unknown effective model reporting, and bootstrap metadata projection without raw prompts.
- E0–E3 evidence and limits are recorded in `plan/issue-33-experiment-evidence.md`; E2 includes bounded paired DEV, separate product-group validation, replay, and exported-patch verification.
- An existing 56-generation real GPU AnimeDiff smoke is now recorded as partial E4 evidence: it proves the generic campaign path and rejection of a null candidate, not image-quality improvement. The protocol only measures coarse RGB fidelity on neutral geometric cases.

## Follow-up review findings

- Replay relative tolerance is anchored to the frozen/original metric, and readiness disables relative tolerance when the measured center is zero or within the absolute A/A noise range. Regressions exercise extreme drift from both ordinary and near-zero baselines; smoke/full metric drift also has a failing-readiness test.
- Scientific outcome uses `scientific_assessment`, so a `GOAL_MET` technical result with only DEV-tier independent evidence and an unverified holdout is reported as `UNVERIFIED_EVIDENCE`, not scientific `GOAL_MET`.
- Readiness evaluator attempts have durable per-invocation status records. Report accounting counts attempts and separates completed, failed, and unknown calls; regression coverage includes failures in smoke and full preflight.
- Portable trace export has a large-history regression with 1,000 durable attempts, monotonically ordered sequences, unique event identities, and byte-identical repeat export.

## Remaining acceptance gaps

- No real operator-approved reference manifest has been supplied for E1–E3. The test manifest verifies the mechanics only and does not upgrade those campaigns' trust.
- E2 still lacks human-reject labels and continues to have poor generic no-match behavior; its negatives are constructed controls.
- E3 is a single synthetic CPU workload on one runtime, not broad performance generalization.
- E4 has a historical real GPU run, but no current-head rerun or semantic/prompt-adherence/safety/human-review evidence. Per operator direction, AnimeDiff is currently in use and was not touched for this implementation.
- The issue requires actual domain campaigns and independent evidence. This report does not claim full DoD completion; issue #33 and PR #35 remain open, and PR #35 remains draft.
