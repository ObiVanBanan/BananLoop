# BL-003 diagnosis and acceptance evidence

**Acceptance state: PARTIAL / NOT READY TO CLOSE.** There is a successfully completed historical Newton Harbor run and a later clean Full run with a concrete recorded blocker. The blocker is localized to the BananLoop Optimization child's provider response handling, before any public grader submission; it is not evidence of a Harbor runtime or task image failure. The new failure is not yet covered by a regression test and its detailed response/error payload is absent from the durable report, so the complete BL-003 criteria are not met.

## Evidence separation

### Successful single Newton Harbor campaign (Nov. 14–15, 2026)

- Durable Harbor trial: `/home/sonik/bananloop-benchmark-env/runs/sketched-newton-new-campaign-20261114-004/harbor-jobs/are-newton-b3a4557627e9/are-v1.1-sketched-newton-cov-est__xYvLNQP/result.json`
- Durable BananLoop report: `/home/sonik/bananloop-benchmark-env/runs/sketched-newton-new-campaign-20261114-004/state/campaigns/c-b3a4557627e91e0e51da2a72495210db/reports/v2-optimization-c-b3a4557627e91e0e51da2a72495210db.md`
- Status `ALL_NODES_COMPLETE`; Harbor wrapper completed 1/1 trial with 0 errored trials. The bridge recorded 8 provider attempts, 4 candidate iterations, and 5 public submissions (baseline + 4 candidates), all `GRADED`, with no MCP failure category. Public validation reward rose from 0.0 to 0.182413884516; final report records no HOLDOUT.
- Harbor was 0.25.0. Task lock pins `are-v1.1-sketched-newton-cov-estimator`, digest `sha256:bae34e3731778a7a58766c590437323ed6810634b0d8cd6b358a4bd95179313f`; public DEV image ID `sha256:50b2c2cf5ae3009161edcd000c6485d1f0c99445cb8de86d3c410552aab750c8`. Archived fixture SHA-256 is `1e885e106072e15eab46ad7ddb20ef0d392228a90d79964a571998c85ef0886a`; Harbor config SHA-256 `dcb0f2a944aac7be078c10da8d7b309a8bbe655b14e8d2850644f8809165677b`; BananLoop config SHA-256 `190be5650a1b44d6690374b8cd86a4c4e23a3b6a82831d45b592844743525894`.
- The archived manifest identifies source branch `codex/are-newton-launch-handoff`, pre-commit HEAD `0232a77ed1ef0ea429b256b6da0f7c799b303ade`, campaign `c-b3a4557627e91e0e51da2a72495210db`, and child report digest `ab771322c7ebd43bdd6adc285a8a9e1a86c513b81def2d2fd88bfc5b897796ca`. It explicitly classifies this as `REPLAY_INPUTS_ARCHIVED_RUN_NOT_YET_REPEATED`; exact inputs are archived, but this is not a repeated clean-state reproduction.

### Latest clean Full Newton run (Nov. 22, 2026)

- Experiment: `/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p2-prepared-i4fjpx2i/sketched-newton-cov-estimator--bananloop_full/result.json`
- Child report: `/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p2-prepared-i4fjpx2i/sketched-newton-cov-estimator--bananloop_full/sketched-newton-cov-estimator--bananloop_full/campaigns/c-77c32d68968df7963001fc467017720d/reports/v2-optimization-cli-c-77c32d68968df7963001fc467017720d.json`
- The run created one child (`c-77c32d68968df7963001fc467017720d`) and started Harbor. Harbor environment setup and agent setup completed; Harbor trial wrapper status was `COMPLETED`, but the BananLoop agent status was `RUN_FAILED`. Its child report records `failure_type=ChatGPTPlanResponseError`, `failure_detail=null`, 1 dispatched/failed optimizer request, no Integration Verifier request, and `usage=UNKNOWN`. Harbor bridge receipt records `public_submission_count=0` and `public_submission_unknown=false`. Thus the error arose at Optimization provider response handling before Harbor public grading/submission, not in the evaluator or Harbor grader. The captured local public DEV result was score -1.0 for the final untouched artifact, not evidence of successful optimization.
- No repair can be asserted from these artifacts: the failing provider response body / parser diagnostic is not preserved. Provider request ID is `resp_0908c0bf340b70fb016b0295f7a89087d2994c12f0cd1a6a9f`.

## Pinned rerun inputs / constraints

- The Nov. 14 exact archived Harbor command is recorded in the manifest: `PYTHONPATH="/home/sonik/Проекты/BananLoop-ARE-newton/src:/home/sonik/Проекты/BananLoop-ARE-newton:/home/sonik/Проекты/BananLoop-ARE-newton/.venv/lib/python3.12/site-packages" /home/sonik/bananloop-benchmark-env/harbor-venv/bin/harbor run --config <run_root>/harbor-prepared/harbor.json --disable-verification`.
- It is a replay recipe only. Do not run it against its old state/ledger or old submission IDs. The archived task materials prescribe fresh state/ledger paths for any rerun. The current Full attempt is a distinct run with new child identity and public DEV-only scope.
- The Nov. 14 run's exact input set is archived under `benchmarks/autoresearchexam/baselines/sketched-newton-bananloop-v02-20261114/`; manifest states candidate source, fixture, public MCP contract, Harbor/BananLoop config, role registry and receipts are included. Four iterations were funded, one hour, 8 Optimization provider attempts, 9 evaluator calls spent. Harbor job: one attempt, one concurrent trial, zero retries, 3600-second agent timeout, verification disabled. Image/task details above.
- Targeted offline tests were run against the clean separate PR snapshot `be24a260615179f9437859e87fe8a8b2b5b12377` at `/home/sonik/Проекты/BananLoop-autonomous-benchmark-v01-prep`, with bytecode and pytest cache disabled: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/test_prepare_sketched_newton_job.py tests/test_are_safe_harbor_overlay.py tests/test_are_public_task_evaluator.py tests/test_are_public_grader.py tests/test_are_optimization_runner.py -q` → **44 passed**. These cover task prep, overlay boundary, evaluator, public ledger, and Optimization runner; they do not reproduce or assert the Nov. 22 `ChatGPTPlanResponseError` path.

## Remaining acceptance work

1. Preserve a sanitized parser/provider failure diagnostic (or deterministic replay fixture) for `ChatGPTPlanResponseError`; do not expose provider payload secrets.
2. Add regression coverage showing the exact failure is classified and reported at Optimization, no public submission is made, and the owner report retains a useful failure category/detail.
3. After that fix and regression, repeat one Newton task from fresh state with the exact source SHA, Harbor/task image IDs, dataset fixture, and config digests recorded. Confirm handoff, inherited deadline, state persistence, and final artifacts. Keep this distinct from the successful Nov. 14 historical run.

No live model calls, Harbor dispatch, mutation, or GitHub operation was performed for this diagnosis.

## Diagnostic repair

The Optimization CLI now persists the same allowlisted parser categories and
HTTP status labels as Exploration. A synthetic provider-response failure
retains its request ID and produces `RUN_FAILED`, with no automatic replay.
Provider payload text is absent from both CLI output and the persisted report.
63 CLI tests passed. The November 22 provider response remains unavailable.
A fresh Harbor run remains blocked by Docker socket access and the current
preflight.
