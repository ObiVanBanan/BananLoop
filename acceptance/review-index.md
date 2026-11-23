# BL-015 · Review index (draft)

**Update 2026-11-23:** Docker is accessible and all 12 image IDs matched their
catalogue pins in the [fresh preflight](../../BananLoop-autonomous-benchmark-v01-prep/benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-23.json).
It remains `PREP_BLOCKED` on nine unsafe overlays. Docker permission claims
below describe the older 2026-11-22 evidence, not the current environment.

**Disposition: BLOCKED / draft only.** This index is an evidence locator, not
an acceptance decision for a live paired campaign. Paths are relative to
`/home/sonik/Проекты/BananLoop`, unless prefixed `prep:` for
`/home/sonik/Проекты/BananLoop-autonomous-benchmark-v01-prep`.

## Evidence sets and provenance

| Set | Evidence | Identity / SHA | Classification |
|---|---|---|---|
| Main checkout | `acceptance/single-campaign-report.md` | Main HEAD recorded inside report: `559621dfa0e9e230b3dd5257399b6baa88bfbade`; file SHA-256 `b27112a39d84fe23000aaefc8795279dc0e665de1ca29c261fcecabd5afcfd47` | Historical legacy Titanic report, dated 2026-11-21. |
| Prep preflight | `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json` | `source_commit_sha=be24a260615179f9437859e87fe8a8b2b5b12377`; file SHA-256 `adb01e62e2e7d15d3994be2066ef712bf39698654b3a13f448f395b4042ebfaf` | Offline preflight, `observed_date=2026-11-22`, `PREP_BLOCKED`. |
| Prep synthetic archive index | `prep:benchmarks/autonomous_v01/fake-24-arm-e2e-index.json` | `manifest_sha256=079f63ad07ae36e886d82e7319793f301ed0bc02ea9c2411e9b7bb915a9204cf`; index file SHA-256 `772eee947307a32a6bf54018f3be672d23d015226aa8be93f4092e6bd51acfaf` | Synthetic 24-arm archive index, 122 file entries. |
| Prep archival task acceptance record | `prep:benchmarks/autonomous_v01/12-task-acceptance.json` | reviewed source `be24a260615179f9437859e87fe8a8b2b5b12377`; archived harness `ada1f18b7e468bffcba1ed6265a7b99a73d4e481`; file SHA-256 `df8a77367e9a81cc50eebe70b9ee6ed839db4d760202716beff2687e7f132351` | `PREP_BLOCKED`; references historical archive and three task receipts. |
| Prep prior Full review | `prep:docs/reviews/autonomous-full-e2e-acceptance.md` | Describes archive harness `ada1f18`; file hash not recorded here. | Historical archive summary plus current live-gate blocker. |
| Prep deadline review | `prep:docs/reviews/time-only-3600s-acceptance.md` | Base revision `be24a260615179f9437859e87fe8a8b2b5b12377`; file hash not recorded here. | Offline watchdog evidence and remaining production/container cleanup gaps. |
| Prep failure matrix | `prep:docs/reviews/harbor-are-failure-matrix.md` | Review states PR #146 at `be24a260615179f9437859e87fe8a8b2b5b12377`; file hash not recorded here. | Offline failure review; controlled DEV cycle remains blocked. |

The main and prep checkout HEADs are different, and both working trees were
dirty at inspection. The preflight is bound to the prep checkout SHA, not the
main checkout. Dirty files are not identified by HEAD alone. Artifact SHA-256
values above identify the bytes that were read at index preparation; they do
not make the whole working trees reproducible.

## Run manifest

- **Current/prep offline:** the preflight records catalogue digest
  `b27bc81a5d335923846e21674a968dba7bd4e77508f3f0e25145686622dbf17b`,
  catalogue-file digest
  `5cf6f01b44eacb992c534acb81048f7e788b2aed3a271893298dcdd5d1ebd98a`, 12
  planned tasks, 24 arms, and `arm_deadline_seconds=3600`.
  `source_commit_sha` is prep SHA `be24a260615179f9437859e87fe8a8b2b5b12377`.
  The preflight JSON is a report, not a frozen launch authorization.
- **Synthetic fake 24 arms:** the index records a manifest digest, but the
  corresponding canonical manifest file/path and its contents are
  **UNVERIFIED** from the indexed file alone. `prep:benchmarks/autonomous_v01/README.md`
  describes injected fake workers and says this path does not start research
  agents, score via Harbor, or call AI Judge.
- **Historical archived Full:** `prep:benchmarks/autonomous_v01/12-task-acceptance.json`
  points to `/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p2-prepared-i4fjpx2i/final-archive`
  and runtime descriptors beneath it. It records harness SHA
  `ada1f18b7e468bffcba1ed6265a7b99a73d4e481`. The record says 12 descriptors
  validated, but its acceptance status is `PREP_BLOCKED`; do not treat it as a
  current live manifest.
- **Legacy Titanic:** `acceptance/single-campaign-report.md` identifies its
  recorded main SHA as `559621dfa0e9e230b3dd5257399b6baa88bfbade` and says
  dataset OpenML Titanic 40945, seed 2026. A machine-readable manifest and
  complete immutable input hashes are **UNVERIFIED** in the cited report.

## Timeline

- **2026-11-21:** the main repo's BL-011 report documents two offline legacy
  CLI campaigns. Exact campaign start/end timestamps and a durable full event
  timeline are **UNVERIFIED**; the report points to temporary `/tmp` files
  which may not persist.
- **2026-11-22:** prep preflight report records `observed_date=2026-11-22` and
  `PREP_BLOCKED`. It records no live models or Harbor start. Exact preflight
  start/end times are **UNVERIFIED** in the JSON fields cited here.
- **2026-11-22:** the fake index and `12-task-acceptance.json` exist as local
  artifacts. Exact arm execution timestamps are **UNVERIFIED** from the index
  alone. File modification time is not used as run time.
- **Historical Full archive:** `prep:docs/reviews/autonomous-full-e2e-acceptance.md`
  calls the archive prior and pins harness `ada1f18`. Exact per-arm timeline
  is **UNVERIFIED** in this review index; consult the external archive only
  with its exact records and checksums.

## Epochs and time budget

- **Prep/offline plan:** stated policy is 3,600 wall-clock seconds per arm;
  24 arms represent 12 tasks × 2 arms. Preflight estimates one concurrent slot
  and a minimum 24 hours at that capacity. These are planning values, not
  observed campaign epochs or elapsed time.
- **Actual start/deadline epoch values for a live paired campaign:**
  **UNVERIFIED**; there is no accepted live campaign manifest in these sources.
- **BL-042 deadline behavior:** `prep:docs/reviews/time-only-3600s-acceptance.md`
  documents offline monotonic deadline/watchdog behavior. Production launcher
  recovery and Harbor-owned container cleanup remain **BLOCKED / UNVERIFIED**.
- **Fake 24 arms and historical archive epochs:** per-arm epoch values are
  **UNVERIFIED** in the index/review summaries cited above. Do not reconstruct
  epochs from file timestamps.
- **Legacy Titanic:** elapsed time and epoch timestamps are **UNVERIFIED** in
  `acceptance/single-campaign-report.md`.

## Scores and decisions

- **Current/prep offline preflight:** no benchmark score. Status
  `PREP_BLOCKED`; six implementation gate booleans are true, but environment
  gates prevent launch readiness. These are engineering checks, not score
  outcomes. `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json`.
- **Synthetic fake 24 arms:** no meaningful task benchmark scores or model
  decisions are established by `prep:benchmarks/autonomous_v01/fake-24-arm-e2e-index.json`.
  Any fixture/reference outputs belong to synthetic contract testing, not an
  experimental arm comparison. Actual live arm scores are **UNVERIFIED**.
- **Historical Full archive:** `prep:benchmarks/autonomous_v01/12-task-acceptance.json`
  records three prior public DEV receipts, each `GRADED`, after all 12 Full
  arms. ACT Tensor score `-0.000438181838185947` (8 DEV cells; archived Full
  status `EXPLORATION_FAILED`); CausalPFN score `4.130251584660836` (100
  cells; `EXPLORATION_FAILED`); HiCard score `-4.481569170868128` (24 cells;
  `EXPLORATION_FAILED`). These are task-scoped archival public DEV values, not
  paired-arm winners, not current-tree results, and not blind holdout scores.
  The same record has `fresh_provider_or_grader_calls=0` for the acceptance
  review and fresh bad-candidate checks `BLOCKED_DOCKER_SOCKET_PERMISSION_DENIED`.
- **Other historical Full outcomes:**
  `prep:docs/reviews/autonomous-full-e2e-acceptance.md` reports shortest-CI
  `ALL_NODES_COMPLETE`, while Newton and SOPCC were `OPTIMIZATION_FAILED`.
  Numeric score values and a complete per-task/per-arm score table are
  **UNVERIFIED** in that summary.
- **Legacy Titanic:** `acceptance/single-campaign-report.md` records two runs,
  both `STOPPED / BUDGET_EXHAUSTED`, one experiment budget each. First scenario:
  DEV `61.73% → 77.55%`, VALIDATION `61.83% → 70.23%`, HOLDOUT `78.57%`,
  candidate accepted; stated target 75% VALIDATION was not reached. Second:
  DEV `61.73% → 77.55%`, candidate did not reach VALIDATION, HOLDOUT `61.73%`,
  candidate rejected and baseline retained. Report says BL-011 remains open;
  these results do not decide BL-015.

## Artifacts and provenance gaps

- **Fake arm artifacts:** 122 relative-path entries and file hashes are listed
  in `prep:benchmarks/autonomous_v01/fake-24-arm-e2e-index.json`; index SHA and
  manifest digest are in the first table. This index provides per-file
  integrity values, not proof that any arm used a real model or evaluator.
- **Preflight report:** `prep:benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json`;
  artifact SHA in the first table. Reports 22 blockers, including nine unsafe
  overlays and 12 Docker image probes denied by socket permissions. Actual
  local image IDs are unknown, not proven absent or mismatched. It also records
  auth-probe warning and linked CI pytest failures.
- **Historical task records:** `prep:benchmarks/autonomous_v01/12-task-acceptance.json`;
  file SHA in the first table; per-task result SHA values are embedded there.
  The referenced external final archive is not copied into this repo.
- **Legacy Titanic:** `acceptance/single-campaign-report.md`; file SHA in the
  first table. It points to `/tmp/bananloop-bl011-titanic.py` and
  `/tmp/bl011-titanic-tp50bhpc/`, with availability only on the machine where
  it ran explicitly qualified as temporary. Their present availability and
  checksums are **UNVERIFIED**.
- **Exceptions:** prep report discloses source-context exposure for
  `act-tensor-sparse-panel-imputation-r2`,
  `causalpfn-cate-pehe-ihdp-surfaceb`, and `hicard-latent-encoder` at
  `prep:benchmarks/autonomous_v01/source-access-declaration.json` (digest
  `4e37162f863412ee42a04dfddf1d2b7cfb41a266ab058a474fbb97df534bdca9`). The
  report explicitly disallows pristine/blind/independent holdout claims.
  Harbor container cleanup is unverified; CARPS original zero-dispatch cause
  is unresolved per `prep:docs/reviews/harbor-are-failure-matrix.md`.

## Current blockers

- **BL-012 / BL-013 / BL-014:** **UNVERIFIED** here. The local evidence reviewed
  does not map their owner-defined criteria to current-tree proof. Their ticket
  criteria and evidence must be independently supplied/reconciled.
- **Docker:** **BLOCKED** by socket permission denial; image identities remain
  unknown.
- **BL-042:** offline deadline evidence exists; production watchdog integration
  and Harbor container cleanup are **BLOCKED / UNVERIFIED**.
- **BL-043:** production paired launcher acceptance is **BLOCKED**.
- **BL-044:** fresh controlled DEV/fixture approval is **BLOCKED**; original
  CARPS zero-dispatch cause remains unresolved.
- Overall current live campaign: **BLOCKED**. No live model, Harbor, Docker,
  or grader workloads were launched for this index.

## Reviewer checklist

- [ ] **PASS** only when manifest, source revision, task pins, and artifacts
      reconcile on the exact reviewed tree and each hash is verified.
- [ ] **FAIL** if a cited artifact contradicts the claim or violates an
      acceptance criterion; record the specific path/field.
- [ ] **BLOCKED** when a required gate is prevented by Docker, launcher,
      Harbor cleanup, or unresolved BL-042/043/044 dependencies; do not treat
      permission denial as an image mismatch.
- [ ] **UNVERIFIED** when timestamps/epochs, missing manifests, BL-012/013/014
      criteria, per-arm scores, or artifact bytes cannot be established from
      cited records.
- [ ] Keep prep offline, synthetic fake arms, historical archive, and legacy
      Titanic as separate evidence classes; do not merge their scores or SHAs.
- [ ] Record reviewer, date, exact tree SHA, checked hashes, exceptions, and a
      separate acceptance decision. Until then, BL-015 remains **BLOCKED**.
