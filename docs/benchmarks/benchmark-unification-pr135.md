# BananLoop v0.2 — benchmark branch reconciliation for PR #135

**State:** combined implementation/design branch; **no new model calls, task assignment, scoring or benchmark result**. The integrated runtime base is PR #135 (`codex/v02-full-integration` head `9c5965d4072f3431ef4d487ebe3bb25e35df62cf`). This document does not change the preregistration approval gates.

## Source branches

| Source | What was carried into this branch | Why |
| --- | --- | --- |
| [PR #135](https://github.com/ObiVanBanan/BananLoop/pull/135) | Entire integrated v0.2 runtime, offline E2E, `benchmarks/v02_full`, RPDI-14 docs and tests | Latest integration baseline, not replaced by an older branch |
| [PR #125](https://github.com/ObiVanBanan/BananLoop/pull/125) | All ten `benchmarks/agent_capability_v0.2/` design files; updated crosswalk, manifest semantics, case layout, schemas and pilot gates | Separate role-capability test design for the current `RoleRegistryV1` |
| [PR #133](https://github.com/ObiVanBanan/BananLoop/pull/133) | Historical owner-gate record, public licensed DiscoveryBench diagnostic fixture and tests; missing hardening of Optimization verifier role/conclusion, Windows network trust roots, Windows AF_UNIX broker path | Retain non-conflicting diagnostic provenance and restore selected fail-closed checks absent from PR #135 |

The two original PR heads are **not fast-forward compatible** with PR #135. Raw Git merges would bring old application/test changes back over a newer integrated runtime. Instead, this branch is a semantic integration: copy missing resources and apply narrowly reviewed missing guards, without overwriting the full runtime.

## Current actual role IDs

The integrated PR #135 acceptance exercises `researcher`, `optimizer`, `independent_verifier`, and `integration_verifier` through pinned role registry / Team Manifest data. The old PR #125's `proposer`, `selector`, `implementer`, `diagnostic`, `campaign_bootstrapper`, and `pivot_advisor` remain **capability names**, not independent declared runtime roles. The `role-catalog.yaml` file is a benchmark crosswalk, **not** the authoritative role registry.

The role-capability suite is only a design with offline validation tests. It does **not** implement a complete case dispatch runner, trusted provider usage verifier, or independent scorer for agent-wise results.

## RPDI-14 conflict decisions

- Keep the PR #135 refactored `_create_protected_evidence`, `verify_protected_file`, worker, evidence and dispatch flows; do **not** swap in PR #133's older copies.
- Restore explicit `EXPLORATION_GATE_VERIFIER` role and `PASS` conclusion verification for Optimization run gates (in addition to the signed artifact check).
- Restore GetDriveTypeW-based Windows remote/unknown drive rejection, including mapped network shares, and protected Windows broker ACL/reparse checks; Linux paths retain their original behavior. **These changes still require exact-head CI and Windows independent verification before eligibility.**
- Include the public DiscoveryBench board-games dataset strictly as an **unscored diagnostic** with attribution. Public upstream answers mean it is not a sealed independent RPDI-14 task.
- Keep PR #133 owner-input document as a dated historical snapshot. Preferences there are **not** authorization.

## Verification and release gates

On PR #135's **previous** code/test SHA `728a386dc2e3185c936cd9502d5466200bd0a25f`, offline Linux acceptance reported 954 passed, 2 skipped, 7 subtests; **that result does not verify this new branch.** The current combined SHA must receive its own CI and code review.

Run offline:

```bash
python -m pytest tests/test_agent_capability_benchmark_spec.py tests/test_exploration_discoverybench_diagnostic.py tests/test_v02_full_prereg.py tests/test_v02_full_evidence.py tests/test_v02_full_dispatch.py -q
```

Before **any scored run**: freeze the exact integrated SHA, full feature profile, task/rights pins, split/oracle boundaries, per-arm model and budgets, independent signed attestations, verified sandbox and provider usage receipts, sample size and permitted scoring. Keep Optimization and Exploration comparisons separate. Codex Direct must be allowed iterative DEV feedback. Historical v0.1 or DEV-only scores do not satisfy this gate.

**No benchmark launch, scored claim, merge to `main`, release or billing permission is implied by this integration.**
