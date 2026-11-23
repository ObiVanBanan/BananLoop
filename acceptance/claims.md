# BL-015 · Claims ↔ evidence register (draft)

**Update 2026-11-23:** C5 describes the earlier snapshot. Docker is now
accessible and all 12 image IDs matched catalogue pins in the
[fresh preflight](../../BananLoop-autonomous-benchmark-v01-prep/benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-23.json).
The fresh report remains `PREP_BLOCKED` with nine unsafe overlay descriptors.

Paths in the first column are relative to
`/home/sonik/Проекты/BananLoop`. Paths prefixed `prep:` are relative to
`/home/sonik/Проекты/BananLoop-autonomous-benchmark-v01-prep`. Status is for
the claim as written, not for BL-015 overall.

| ID | Claim | Status | Evidence and limits |
|---|---|---|---|
| C1 | Main repository identity at inspection was `559621dfa0e9e230b3dd5257399b6baa88bfbade`; prep repository identity was `be24a260615179f9437859e87fe8a8b2b5b12377`. Both trees were dirty. | PASS | Direct read of `git rev-parse HEAD` and `git status --short` in each checkout on 2026-11-22. Main tree included modified code/tests/docs and untracked `acceptance/`, `benchmarks/`, `docs/reviews/`, `openspec/`; prep tree also included modified runner/CLI/tests and untracked evidence files. This is a workspace observation, not a clean-checkout reproduction. |
| C2 | A synthetic fake-series package archives 24 arm outputs and indexes their hashes. | PASS | `prep:benchmarks/autonomous_v01/fake-24-arm-e2e-index.json`; described by `prep:benchmarks/autonomous_v01/README.md`. Synthetic injected backends only; no agent/model/Harbor/grader outcome. Not live campaign evidence. |
| C3 | Offline Full backend orchestration and campaign safeguards have documented fake-test evidence. | PASS | `prep:docs/reviews/autonomous-full-e2e-acceptance.md`, `prep:benchmarks/autonomous_v01/README.md`. Supports fake orchestration and offline CLI gates only. Reported test results are documentary historical evidence and were not rerun for this packet. |
| C4 | A current prep preflight supports launch readiness. | FAIL | `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json` records `status: PREP_BLOCKED`, binds `source_commit_sha` to prep SHA `be24a260615179f9437859e87fe8a8b2b5b12377`, and says `harbor_started:false`, `live_models_started:false`. This artifact is from prep SHA, not main SHA. |
| C5 | Current local Docker image identity is known and matches all 12 catalogue pins. | BLOCKED | Same preflight JSON: all 12 probes denied by Docker socket permission; status is `unknown_permission_denied`. Actual IDs are unknown. This is not evidence that the images are absent or mismatch. |
| C6 | All current task runtime descriptors are safe and launchable. | FAIL | Same preflight JSON and `prep:benchmarks/autonomous_v01/README.md`: nine overlays have unsafe `environment` symlinks exposing `are-grader-ctx`; README says the offline preflight remains blocked. The runtime archive with 12 validated descriptors is a distinct archived runtime and does not erase current-tree preflight failures. |
| C7 | There is a historical Full archive with some completed nodes and public DEV receipts. | PASS, ARCHIVAL ONLY | `prep:docs/reviews/autonomous-full-e2e-acceptance.md` describes an earlier archive pinned to harness `ada1f18`, with shortest-CI `ALL_NODES_COMPLETE`; Newton/SOPCC are `OPTIMIZATION_FAILED`. `prep:benchmarks/autonomous_v01/12-task-acceptance.json` reports archived Full statuses and archived final public DEV receipts for three tasks. These do not constitute a current-tree or complete live-series PASS. |
| C8 | A fresh current-code real Harbor Full run is accepted. | BLOCKED | `prep:docs/reviews/autonomous-full-e2e-acceptance.md` explicitly says the clean Full run remains blocked, Docker socket denied, current preflight `PREP_BLOCKED`, and live gate closed. No fresh run was started for this packet. |
| C9 | BL-042's one-hour deadline and watchdog behavior is implemented and fully verified for production workloads. | BLOCKED | `prep:docs/reviews/time-only-3600s-acceptance.md`: offline supervision and POSIX subprocess cleanup have evidence; production paired launcher must still use watchdog/recovery policy; Harbor-managed Docker processes and cleanup remain unverified and Docker socket is denied. Thus implementation tests do not establish production lifecycle acceptance. |
| C10 | BL-043 production paired launcher is accepted. | BLOCKED | `prep:benchmarks/autonomous_v01/README.md` states the complete production series launcher is tracked in BL-043; `prep:docs/reviews/time-only-3600s-acceptance.md` says it still needs to use watchdog and recovery policy. No live launcher acceptance artifact was found in the inspected paths. |
| C11 | BL-044's Harbor/pivot/evaluator failure matrix is fully closed. | BLOCKED | `prep:docs/reviews/harbor-are-failure-matrix.md` records offline overlay rebuilds, unresolved CARPS original zero-dispatch cause, fixture/descriptor review still needed, and controlled DEV cycle blocked by Docker permission/current preflight. |
| C12 | BL-012, BL-013 and BL-014 dependencies are proven closed by current-tree evidence. | UNVERIFIED | The inspected local artifacts do not identify closure evidence bound to the current main SHA for these three IDs. Do not infer their criteria from BL-015. Require the owning ticket definitions and pinned evidence before upgrading status. |
| C13 | All 12 tasks can be called blind/independent holdout evidence. | FAIL | `prep:benchmarks/autonomous_v01/README.md` and `prep:benchmarks/autonomous_v01/12-task-acceptance.json` disclose prior `are-grader-ctx` snippet exposure for ACT Tensor, CausalPFN and HiCard and explicitly disallow pristine/blind/independent holdout claims. |
| C14 | Main repository BL-011 manual Titanic evidence demonstrates BL-015 campaign acceptance. | FAIL | `acceptance/single-campaign-report.md` is a separate legacy CLI offline report at main SHA `559621d`; it explicitly says BL-011 remains open and did not use the v2 Goal-to-report flow. It does not establish the paired ARE live campaign. |
| C15 | BL-015 is a fully accepted live paired campaign. | FAIL | No such current live artifact was found. Contradicted by `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json` (`PREP_BLOCKED`, no Harbor/models started), `prep:docs/reviews/autonomous-full-e2e-acceptance.md` (live gate closed), and `prep:docs/benchmarks/paired-final-artifact-contract.md` (offline/fake outputs do not establish benchmark scores or complete readiness). |

## Evidence classes

- **Fake/synthetic:** `prep:benchmarks/autonomous_v01/fake-24-arm-e2e-index.json`
  and the fake test descriptions in
  `prep:benchmarks/autonomous_v01/README.md`. Demonstrates runner/archiver
  mechanics under injected substitutes only.
- **Current preflight (prep checkout only):**
  `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json`.
  Actual prep HEAD is the same commit SHA stated by the report, but its checkout
  is dirty; the SHA does not identify uncommitted file contents. It is not a
  preflight of main checkout SHA `559621d`.
- **Archival:** prior Full archive and task evidence described in
  `prep:docs/reviews/autonomous-full-e2e-acceptance.md` and
  `prep:benchmarks/autonomous_v01/12-task-acceptance.json`. Separate earlier
  harness/runtime; not current-code replay.
- **Main checkout current observation:**
  `acceptance/single-campaign-report.md` is historical offline evidence dated
  2026-11-21 and bound in its text to main SHA `559621d`. It does not close
  BL-015.

## Reviewer disposition

Draft author did not rerun tests or launch live components. Independent
reviewer should record each claim's accepted/rejected status, exact observed
tree and SHA, and any corrected evidence paths. The overall campaign remains
BLOCKED until the blockers in `README.md` have current, independently checked
evidence and all launch gates are separately satisfied.
