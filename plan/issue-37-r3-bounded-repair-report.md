# R3 bounded repair implementation report

## Scope

This increment adds a machine-readable failure taxonomy and one campaign-level implementation repair attempt after Python syntax/API preflight failures. It does not implement the broader issue #37 recovery planner, approval manager, or dashboard.

## Behavior

- The repair agent receives only the goal, approved hypothesis, editable/protected paths, current snapshot identifier, and sanitized preflight errors.
- A stable `exp:<id>:repair:1` agent action is subject to the existing global agent-call budget and replay rules.
- Failure categories and DEV decision reasons map to implementation, infrastructure, scientific outcome, policy, planning, budget, or unknown classes, with retryability, risk, and a suggested next step. Classification is journaled and included in the operator report.
- Codex and Pi can execute the `repair` role with the implementation response schema and write access to the disposable agent workspace.
- After the repair, BananLoop snapshots the candidate and repeats scope, frozen evaluation asset, syntax, and API compatibility checks. Only then can it reach the existing independent DEV evaluator.
- Exhaustion, no material change, or remaining preflight errors produce an implementation-blocked rejection. Validation/holdout results are never included in the repair request.

## Verification

- `python -m py_compile src/bananloop/engine.py src/bananloop/agents/codex.py src/bananloop/agents/pi.py` passed.
- `git diff --check` passed.
- `PYTHONPATH=src pytest -q tests/test_failures.py tests/test_attempt_accounting.py tests/test_engine.py -k 'failure or api_preflight or implementation_blocker or noop_implementation'` passed.
- `PYTHONPATH=src pytest -q tests/test_engine.py::test_one_bounded_repair_rechecks_candidate_before_dev_evaluation` passed.
- Fault injection after a completed repair action but before the experiment snapshot update resumes from the stored repair snapshot without a second agent call.
- Full `PYTHONPATH=src pytest -q` passed after the taxonomy integration and reached `[100%]` without failures.

## Remaining R3 work

Recovery planning beyond this single bounded repair, fault-injection coverage for unsuccessful repairs and broader operational failures, and the remaining #37 approval/dashboard scope are still open. This report does not claim R3 complete.
