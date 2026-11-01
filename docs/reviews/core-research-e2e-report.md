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
- No H2H was run. The matched-resource benchmark release gate remains `BLOCKED`/`INELIGIBLE` the unverified $3 USD cap accounting and evaluator isolation.


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

**Candidate:** PR #87 code/test commit `65b3f57be185183ff355db952cc3e6a40209c012`, stacked on PR #86 head `14c383a49a1ebf46e8448534f0b09c6c4333b6d4`; Linux / Python 3.14.7. No model calls, paid calls, benchmark treatment or HOLDOUT scoring were used.

- The seven CPU-only research scenarios listed above passed again on this worktree, including improvement/promotion, failed-hypothesis reflection, plateau, composite acceptance/rejection, durable resume, and DEV-only adaptive history. The staged validation gate and one-time terminal HOLDOUT test also passed separately: `tests/test_engine.py::test_staged_validation_gates_promotion_and_final_holdout_runs_once`.
- The full suite completed with exit code 0: 454 passed, 1 skipped. The skip is the unavailable pinned selector preregistration source snapshot.
- A subprocess regression abruptly exited during a first unmetered bootstrap call; the pre-call marker remained `IN_FLIGHT`, and a retry with a cap was rejected before adapter creation.
- The SIGINT lifecycle runner sent a real signal during an active local agent action, observed `PAUSED_INFRA` and persisted cancellation, confirmed the child process exited, then resumed to `GOAL_MET`: 3 experiments, 7 agent calls, 4 evaluator calls, quality 0→3, `DEV_ONLY`. No model calls or HOLDOUT were configured.
- Reproduction: `/tmp/bananloop-plan-sigint-e2e-20261101-pr87/result.json`; logs are under `/tmp/bananloop-plan-sigint-e2e-20261101-pr87/logs/`.

PR #87 head `8bd6987f7ebc7a4223c6ef0ba4993212074b50ac` passed hosted run [#41236491009](https://github.com/ObiVanBanan/BananLoop/actions/runs/41236491009) on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. PR #88 run [#41237333538](https://github.com/ObiVanBanan/BananLoop/actions/runs/41237333538) passed its Windows full suite with 456 passed, 1 skipped. PR #89 run [#41237375210](https://github.com/ObiVanBanan/BananLoop/actions/runs/41237375210) passed its Windows full suite with 457 passed, 1 skipped, including the clean-install smoke on hosted CI. The local evidence does not prove hostile-code isolation, H2H eligibility, or a release recommendation.

## Current stacked verification — 2026-11-01

**PR #89 head:** `472f695a6263d80d8d62875febaf394e73122f2e`, stacked on PR #88, #87, #86 and #85.

- Full local suite: **456 passed, 2 skipped** on Linux/Python 3.14.7. The skips are the unavailable pinned selector source snapshot and the clean-install test's network-dependent build-isolation step. The hosted Ubuntu/Python 3.11/3.12/3.13 and Windows/Python 3.12 matrix passed, including wheel install and CPU fixture execution.
- A fresh real-SIGINT run of `examples/fixture/run_lifecycle_e2e.py` exited the CLI with code 130, persisted `PAUSED_INFRA` and the cancelled `exp:1:propose` action, confirmed the child had exited, then resumed to `GOAL_MET` with 3 experiments, 7 agent calls, 4 evaluator calls, quality 0→3, and `DEV_ONLY`. It made zero model calls and configured no HOLDOUT.
- Reproduction: `PYTHONPATH=src /tmp/bananloop-plan-venv/bin/python examples/fixture/run_lifecycle_e2e.py --sigint --output-dir /tmp/bananloop-sigint-e2e-current-20261101`.
- Logs and state: `/tmp/bananloop-sigint-e2e-current-20261101/result.json` and `/tmp/bananloop-sigint-e2e-current-20261101/logs/`.

The documentation-only PR #85 update on head `d7bf9d055da1557e9a5f584a5b9b834e82129894` also passed its hosted Ubuntu/Windows matrix (run `41238895406`; Windows: 448 passed, 1 skipped in 642.13s). No PR is merged and the release SHA remains unselected.


## Final HOLDOUT recovery E2E — 2026-11-01

**PR #91 head:** `3ab301ad499f17f1a9abb4ac7813743b6d4f1d1a`, stacked on PR #89 head `472f695a6263d80d8d62875febaf394e73122f2e`; Linux / Python 3.14.7. No model or paid calls.

- The deterministic fixture reached durable `FINAL_HOLDOUT` / `GOAL_MET`, interrupted the active evaluator subprocess, persisted cancellation, and resumed the same final action. The retry completed once: one accepted evaluation, receipt, and final record event.
- The child process exited; no experiment, agent call, or DEV evaluation ran after terminal intent. The unique HOLDOUT diagnostics canary did not appear in agent action requests or responses.
- Full local suite on the candidate worktree: **457 passed, 2 skipped in 59.56s**. Skips: unavailable pinned selector preregistration snapshot and network-dependent clean-install build isolation.
- Hosted run [#41246679803](https://github.com/ObiVanBanan/BananLoop/actions/runs/41246679803) passed on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows: **458 passed, 1 skipped in 602.63s**.
- Reproduction artifacts: `/tmp/bananloop-final-holdout-e2e-20261101-retry3/result.json` and the adjacent logs directory.

This verifies local fixture recovery and the test matrix. It does not establish hostile-code isolation, dataset rights, or H2H eligibility; no benchmark candidates were run.


## CLI HIL writer serialization — 2026-11-01

**PR #92 head:** `ebbc066ba7dddbe82290e9606c5fa2e0f8232801`, stacked on PR #91. A read-only review found that the terminal rejection transaction could race with a concurrent `run/resume` and be overwritten by a later RUNNING transition. The CLI now takes the same `campaign.lock` for `pivot-decision` that the campaign runner holds for its full run.

- Regression assertion: while the campaign lock is held, `pivot-decision` returns the lock error; the campaign remains `PAUSED_NEEDS_INPUT` and no `CAMPAIGN_STOPPED` event is written. The existing rejection path then commits its receipt and STOPPED transition atomically.
- Exact-head hosted run [#41250697408](https://github.com/ObiVanBanan/BananLoop/actions/runs/41250697408) passed Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows: **460 passed, 1 skipped in 697.26s**; the skip is the unavailable pinned selector preregistration snapshot.
- The pre-lock-fix local run reported 459 passed, 2 skipped and the focused pivot/HIL suite 22 passed. The current fetched CLI/test files compiled, and a local Linux lock primitive check confirmed a second writer is rejected; the exact updated head received the full hosted matrix above.
- No model calls, benchmark candidate, HOLDOUT score, merge, tag or release were performed. PR #92 remains draft.

## Current-base CLI lifecycle E2E matrix — 2026-11-01

**Exact candidate:** draft PR #93 head `f0f5c14a506a54a870bb62a9738884dc40a626c8`, based on PR #85 head `0809af87418a66c1eda67c402b3bfefa5320aec4`. No model calls, paid calls, benchmark candidates, or HOLDOUT scoring.

- Hosted run [#41257406221](https://github.com/ObiVanBanan/BananLoop/actions/runs/41257406221) passed Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows full suite: **452 passed, 1 skipped in 484.18s**; the skip is the unavailable pinned selector preregistration snapshot.
- The suite runs `tests/test_lifecycle_e2e.py` in both typed-pause and real SIGINT/CTRL_BREAK modes. Each mode reaches `GOAL_MET` and `DEV_ONLY`; the test asserts zero model calls, no configured HOLDOUT, and the expected persisted pause status.
- The runner checks cancellation of the active agent child, absence of its post-interruption sentinel, and successful resume. On Windows, the CLI maps CTRL_BREAK/SIGBREAK to its interruption handler only during `engine.run`, then restores the prior handler.
- The report renderer now uses ASCII arrows, satisfying the E2E CP-1252 encoding check. The first attempt failed on Unicode arrows; exact-head run #41257406221 passed after the fix.

This verifies the current-base deterministic CLI recovery fixture and full platform matrix. It does not prove cancellation of a live Codex/model process, evaluator isolation, dataset rights, H2H eligibility, or release readiness. PR #93 remains draft and unmerged.

## Bootstrap token-admission regression suite — 2026-11-01

**Candidate code head:** draft PR #94, `6d88f67eaca957c399533c63d00ac49397f78a8d`; based on current lifecycle head PR #93. Full hosted run [#41259572837](https://github.com/ObiVanBanan/BananLoop/actions/runs/41259572837) passed Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows: **458 passed, 1 skipped in 642.39s**; the skip is the unavailable pinned selector preregistration snapshot.

Deterministic fake-adapter tests verify that insufficient reserve prevents the model call; an empty ledger allows correcting reserve or removing the cap before any attempt; any reserved/unknown/consumed attempt locks settings; known usage imports atomically; and overshoot/unknown usage fail closed. The rollback regression confirms partial campaign initialization leaves campaign, action, reservation, and event tables empty. No real model, benchmark candidate, or HOLDOUT calls were made. This demonstrates local token accounting only, not USD spend enforcement or evaluator isolation.


## Final-HOLDOUT recovery and pivot HIL — 2026-11-01

PR #96 exact head `e4dd1ae094571ea72b73c3a514a745e6c1d723fd` passed full hosted run [#41262308357](https://github.com/ObiVanBanan/BananLoop/actions/runs/41262308357) across Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows: **460 passed, 1 skipped in 732.30s**; skip: unavailable pinned selector preregistration snapshot.

PR #97 rebases the v0.1 pivot HIL changes onto that exact lifecycle/bootstrap/clean-install/final-HOLDOUT stack. Exact head `61033cf7e4ae3b31f481a08ad5fd9b2f52e8d1c6` passed full hosted run [#41262536403](https://github.com/ObiVanBanan/BananLoop/actions/runs/41262536403) on the same matrix. Windows: **462 passed, 1 skipped in 523.60s**; same selector-snapshot skip.

Coverage includes actual local process interruption during terminal FINAL_HOLDOUT and single-action recovery without replanning or canary leakage; pivot decision lock contention; atomic rejection/STOPPED receipt; approved child preview/hash confirmation/idempotent READY creation; and recovery when a crash left the reserved child's SQLite schema but no campaign row. Tests use local deterministic fixtures. No model, paid benchmark, or official HOLDOUT was run. These runs validate candidate branches, not final `main` or release readiness; Issue #64 remains **BLOCKED / INELIGIBLE**.
