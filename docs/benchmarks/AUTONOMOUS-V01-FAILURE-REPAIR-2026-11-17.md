# Series failure repair, 2026-11-17

Historical series: `/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-live-20261116-c0966ea`.
The historical archive remains unchanged. These repairs require a new candidate/harness revision for any rerun.

Confirmed defects and repairs:

- ActTensor, CausalPFN, HiCard Full: the pinned candidate revision omitted their entrypoints. Add the existing public smoke baselines to the candidate tree; test all 12 catalogue entrypoints. A missing initial file now produces an explicit terminal artifact record instead of an unhandled FileNotFoundError.
- Newton and SOPCC Full: independent verifier receipts in campaign.sqlite3 say APPROVE. Auto-pivot then decoded an opaque evaluator ZIP as JSON and raised V2CampaignCLIError. Decode JSON only for the three public-task adapters whose bundle contract is JSON. Regression passes a real ZIP through the production pivot checks.
- Failed Exploration: the backend returned on nonzero exit before parsing request_history and usage. Preserve dispatched calls and tokens from a valid failure report. The 24-arm fake acceptance matrix also runs with two dispatched calls and a nonzero Exploration exit.
- Earlier iteration bound (100 versus 1000) and untracked instruction git-archive failures were already repaired in dad99a5 and d7f1ef9; their regression checks are rerun.

Remaining unresolved evidence:

- CARPS stopped before dispatch with empty stdout/stderr. The zero-dispatch audit establishes that fact, not the underlying cause. Do not claim this is repaired.
- CARPS final assessment imported NumPy in the project interpreter, which lacks it. The broker uses Harbor's prepared Python, but the external continuation script scores final artifacts in-process. That script must use the same prepared interpreter for final scoring.
- DCTAB's original rejection included validation requiring external grader evidence; subsequent Full arms use a DEV-only effective fixture.
- Label-risk and shortest-CI frozen evaluator bundles disagree with catalogue/model-proposed metric semantics. Regenerate/reconcile and pin the evaluator artifacts before spending model calls; do not suppress protocol rejection or mutate historical fixtures.
- No successful live Optimization lifecycle has yet been demonstrated for this series. Fake acceptance proves orchestration with substituted subprocesses only.

No real models, Harbor runs, or scorer evaluations are invoked by the regression tests. RPDI-14 is unchanged.
