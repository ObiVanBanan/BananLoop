# Core research E2E evidence — CPU-only fixtures

**Base under test:** `b4abbf1488296ff43a4f4c3c639ed24e54a3764e` plus the local event/snapshot consistency patch in `codex/core-stabilization-v0.1`.
**Environment:** Windows 11, Python 3.13, local fake command agent and temporary git repositories. No model API calls, paid calls, benchmark HOLDOUT, or generated untrusted code were used.

## Scenarios

| Plan case | Test | Result |
| --- | --- | --- |
| A. Improvement, reject, then promote | `tests/test_engine.py::test_end_to_end_accept_reject_accept_and_source_unchanged` | PASS |
| B. Failed hypothesis informs later proposal | `tests/test_engine.py::test_aggregate_dev_rejection_stores_deterministic_reflection` | PASS |
| C. Plateau reframe and stop | `tests/test_engine.py::test_plateau_allows_one_budgeted_reframe_then_stops` | PASS |
| D. Composite promotion | `tests/test_composite.py::test_composite_final_acceptance_validates_and_promotes` | PASS |
| D. Composite rejection preserves champion | `tests/test_composite.py::test_composite_final_rejection_preserves_champion` | PASS |
| E. Recovery resumes durable work | `tests/test_engine.py::test_resume_after_durable_stage_does_not_restart_experiment` | PASS |
| DEV-only adaptive history | `tests/test_engine.py::test_agent_history_and_research_state_use_only_dev_evidence` | PASS |

Command:

```powershell
$env:PYTHONPATH='src'
python -m pytest --basetemp=.pytest-regression -q --tb=short tests/test_engine.py::test_end_to_end_accept_reject_accept_and_source_unchanged tests/test_engine.py::test_aggregate_dev_rejection_stores_deterministic_reflection tests/test_engine.py::test_plateau_allows_one_budgeted_reframe_then_stops tests/test_composite.py::test_composite_final_acceptance_validates_and_promotes tests/test_composite.py::test_composite_final_rejection_preserves_champion tests/test_engine.py::test_resume_after_durable_stage_does_not_restart_experiment tests/test_engine.py::test_agent_history_and_research_state_use_only_dev_evidence
```

The command above was run with pytest node IDs using underscores (`then_stops`, `final_acceptance_validates_and_promotes`, and so on); all seven selected cases passed. The full suite collected 432 tests and exited 0 on the same worktree. Existing pytest configuration suppresses the pass/skip totals in quiet mode. The selector preregistration source-integrity test explicitly skips when its pinned source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` is unavailable; that commit was not present locally and GitHub returned no such commit. This skip does not validate the missing frozen source snapshot.

## Limits

- This is deterministic local-fixture evidence, not live acceptance with Codex or Pi.
- The original run above exercised Windows only. The current-main follow-up below now adds Linux/Python 3.14 fixture and lifecycle evidence; current-SHA Windows acceptance remains unverified.
- The OS does not isolate evaluator code as a hostile-code sandbox. This evidence does not authorize executing benchmark submissions on the host.
- No H2H was run. The matched-resource benchmark release gate remains `BLOCKED`/`INELIGIBLE` pending spend authorization and verified evaluator isolation.


## Current-main follow-up — 2026-10-31

**Code base:** `44614ea22bc5967adb7ee607bce2472cc66487ee`; Linux, Python 3.14.7; CPU-only local fixtures and temporary Git repositories. The same seven node IDs above were rerun on this code base: **7 passed in 3.11s**. The full suite on this code base completed **448 passed, 1 skipped in 38.45s**. The skip is the unavailable pinned selector source snapshot documented above.

No model or paid calls, benchmark treatment, validation or HOLDOUT scores were run. This follow-up gives current Linux fixture evidence, but does not establish Windows campaign acceptance, a hostile-code security boundary, or matched Codex Direct vs BananLoop results.

- Reusable pause/resume CLI run via `examples/fixture/run_lifecycle_e2e.py` passed on Linux/Python 3.14.7. A real local CLI supervisor process was paused during an active agent subprocess; the action was cancelled, then explicit resume completed three experiments and produced the final `DEV_ONLY` report (`GOAL_MET`, quality 0→3, 7 agent calls including the interrupted call, 4 evaluator calls). No model calls; no validation or holdout configured.
- Reproduction command: `python examples/fixture/run_lifecycle_e2e.py --output-dir /tmp/bananloop-plan-lifecycle-e2e-final`. It refuses to overwrite an existing output directory and saves JSON result plus stdout/stderr for init, pause, interrupted run, resume, status and report under `logs/`.
- Recorded run artifacts: `/tmp/bananloop-plan-lifecycle-e2e-final/result.json` and `/tmp/bananloop-plan-lifecycle-e2e-final/logs/`.

## Real-signal recovery follow-up — 2026-11-01

**Candidate source commit:** `c0e7f0aa79e373c51926be178854472cb71db5b4` (stacked on PR #85; main base `44614ea22bc5967adb7ee607bce2472cc66487ee`). Linux / Python 3.14.7, deterministic local command agent, zero model calls.

- `tests/test_cli.py`: **22 passed**, including deterministic checks that CLI interruption persists a resumable `PAUSED_INFRA` state and preserves pending final-HOLDOUT intent.
- Full local suite on the candidate worktree: **450 passed, 1 skipped in 37.25s**. The skip remains the unavailable pinned selector source commit; Linux/Python 3.14.7.
- Reproduction: `/tmp/bananloop-plan-venv/bin/python examples/fixture/run_lifecycle_e2e.py --sigint --output-dir /tmp/bananloop-sigint-e2e-20261101`.
- SIGINT exited the active CLI with code 130; the state reported `PAUSED_INFRA`; the database recorded `ACTION_CANCELLED` for `exp:1:propose`; the child-survival sentinel was absent. After resume the campaign reached `GOAL_MET`, three experiments, seven agent calls, four evaluator calls, quality 0→3 and `DEV_ONLY`. No validation/HOLDOUT was configured.
- Logs and state: `/tmp/bananloop-sigint-e2e-20261101/result.json` and `/tmp/bananloop-sigint-e2e-20261101/logs/`.
- This is Linux-only runtime evidence. Windows CTRL_BREAK/process-tree behavior remains unverified until hosted Windows CI finishes; it does not prove hostile-code isolation or H2H eligibility.

## Bootstrap-budget candidate follow-up — 2026-11-01

**Candidate:** PR #87 head `b0278de7e4e86f54e1ed4e5e8e331a9d24f09d3c`, stacked on PR #86 head `14c383a49a1ebf46e8448534f0b09c6c4333b6d4`; Linux / Python 3.14.7. No model calls, paid calls, benchmark treatment or HOLDOUT scoring were used.

- The seven CPU-only research scenarios listed above passed again on this worktree, including improvement/promotion, failed-hypothesis reflection, plateau, composite acceptance/rejection, durable resume, and DEV-only adaptive history.
- The full suite completed with exit code 0: 453 passed, 1 skipped. The skip is the unavailable pinned selector preregistration source snapshot.
- The SIGINT lifecycle runner sent a real signal during an active local agent action, observed `PAUSED_INFRA` and persisted cancellation, confirmed the child process exited, then resumed to `GOAL_MET`: 3 experiments, 7 agent calls, 4 evaluator calls, quality 0→3, `DEV_ONLY`. No model calls or HOLDOUT were configured.
- Reproduction: `/tmp/bananloop-plan-sigint-e2e-20261101-pr87/result.json`; logs are under `/tmp/bananloop-plan-sigint-e2e-20261101-pr87/logs/`.

This remains local Linux evidence. PR #87 hosted matrix is pending; this does not establish Windows behavior for the new bootstrap metering, hostile-code isolation, H2H eligibility, or a release recommendation.
