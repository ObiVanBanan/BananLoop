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
- Only Windows was exercised here. Linux install and lifecycle acceptance remain unverified.
- The OS does not isolate evaluator code as a hostile-code sandbox. This evidence does not authorize executing benchmark submissions on the host.
- No H2H was run. The matched-resource benchmark release gate remains `BLOCKED`/`INELIGIBLE` pending spend authorization and verified evaluator isolation.
