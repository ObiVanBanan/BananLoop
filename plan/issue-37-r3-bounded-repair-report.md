# R3 bounded repair implementation report

## Scope

This increment adds one campaign-level implementation repair attempt after Python syntax/API preflight failures. It does not implement the broader issue #37 recovery planner, approval manager, or dashboard.

## Behavior

- The repair agent receives only the goal, approved hypothesis, editable/protected paths, current snapshot identifier, and sanitized preflight errors.
- A stable `exp:<id>:repair:1` agent action is subject to the existing global agent-call budget and replay rules.
- Codex and Pi can execute the `repair` role with the implementation response schema and write access to the disposable agent workspace.
- After the repair, BananLoop snapshots the candidate and repeats scope, frozen evaluation asset, syntax, and API compatibility checks. Only then can it reach the existing independent DEV evaluator.
- Exhaustion, no material change, or remaining preflight errors produce an implementation-blocked rejection. Validation/holdout results are never included in the repair request.

## Verification

- `python -m py_compile src/bananloop/engine.py src/bananloop/agents/codex.py src/bananloop/agents/pi.py` passed.
- `git diff --check` passed.
- Runtime campaign and adapter integration tests have not been run for this increment.

## Remaining R3 work

Failure taxonomy, recovery planning beyond this single bounded repair, broader fault-injection coverage, and the remaining #37 approval/dashboard scope are still open. This report does not claim R3 complete.
