# Integrated campaign completion

Date: 2026-09-26. Continuation of `integration/stochastic-artifact-dogfood-v1`
at `5d815995b2d72d7b5607969fa095f720fe4bf05b`, which already incorporates
PR #27 (staged evaluation), #29 (research state) and #30 (DEV reasoning).

## What is completed in this change

| Boundary | Result |
| --- | --- |
| Final success | `GOAL_MET` requires the configured holdout to meet the target. Failed quality ends as `FINAL_HOLDOUT_REJECTED`, with the champion retained. |
| Final recovery | Finalization intent is durable before the call. Recovery cannot reopen planning after a holdout observation; an existing receipt replays at the exact call limit without an agent. |
| Budgets | Every evaluator launch/retry leaves the final call and work reservation available. Failed reservation transactions leave no partial action or reset failure state. |
| Agent replay | Original request JSON is persisted and hash checked. Changing remaining counters cannot invalidate a completed/retried request, and replayed hypotheses do not reject themselves as duplicates. |
| Promotion | Champion update, promotion event and next stage commit together. Recording a completed attempt twice cannot double scientific counters. |
| Stop/deadline | The owner polls a durable stop request and the absolute campaign deadline during subprocess/RPC work. Cancellation settles actual reservations and does not promote partial work or start more final work. |
| Agent commits | Both committed and uncommitted edits transfer from the supervisor-recorded sanitized baseline. Hidden-path checks cover the whole transferred diff. |
| Paired guards | Multiple bounds on one metric retain separate conjunctive predicates. |
| Delivery | `bananloop export` produces the current champion patch, SHA-256 manifest and operator reports, with no automatic apply/push/merge. |

The evaluator contract, approved-spec hashes, DEV-only adaptive memory,
validation/holdout privacy and generic artifact contract remain compatible.
SQLite adds nullable `actions.request_json` and default-false
`campaigns.stop_requested`. Old action records retain their existing hash checks;
the new persisted-input recovery applies to actions launched by this version.
Already terminal historical reports are not silently rewritten.

## Verification and evidence

The upstream suite passed 90 tests before editing. Added regressions exercise
actual command processes and fresh engine instances at receipt, promotion and
finalization boundaries, including subprocess-tree cancellation and a separate
CLI `stop` process. Export is verified by applying its patch to a disposable
clone and checking that the rejected candidate is absent.

Final local result: **106 passed in 87.81 s** on Linux/Python 3.12. The CLI smoke
completed `ACCEPTED -> REJECTED -> ACCEPTED`; terminal resume issued zero calls,
the exported patch reproduced the champion, and the source checkout was unchanged.

Run the complete suite:

```powershell
uv sync --extra test
uv run python -m pytest -o addopts='' -q
```

CI covers Ubuntu/Python 3.11–3.13 and Windows/Python 3.12. The local execution
environment for this change is Linux/Python 3.12; a configured Windows CI job is
not a claim that a Windows run has passed.

`dogfood-proof.json` is the previous companion-adapter **fixture** run:
one scientific experiment rejected, 56 retained artifacts/work units, unchanged
champion, rejected final assessment. It is not a live GPU benchmark.
`completion-smoke.json` records the reproducible local CLI verification of this
change; it also uses the command fixture, not a paid or live coding model.

## Short local run

Use fresh directories so existing campaigns keep their approved inputs/budgets.
The following CPU fixture calls no model or remote evaluator:

```powershell
uv run python examples/fixture/create_fixture.py ../bananloop-fixture
uv run bananloop init --repo ../bananloop-fixture --state-dir ../bananloop-fixture-state --goal "Fixture quality 3, no regressions" --eval-cmd "python evaluator.py" --metric quality --target 3 --min-improvement 0.5 --guard "regressions<=0" --check tests --editable value.txt --editable guard.txt --protect agent.py --agent command --agent-cmd "python agent.py" --max-experiments 3 --max-attempts 3 --max-agent-calls 6 --max-evaluator-calls 4
uv run bananloop run --repo ../bananloop-fixture --state-dir ../bananloop-fixture-state
uv run bananloop resume --repo ../bananloop-fixture --state-dir ../bananloop-fixture-state
uv run bananloop export --repo ../bananloop-fixture --state-dir ../bananloop-fixture-state --output ../bananloop-fixture-export
```

Expected: `ACCEPTED -> REJECTED -> ACCEPTED`, quality `0 -> 3`, six agent calls,
four evaluator calls. Terminal resume makes no further calls. This fixture has
no holdout; `DEV_ONLY` must not be presented as held-out quality evidence.

For an existing project, keep its existing evaluator and adapter configuration.
Use `--max-experiments 1` in a **new** campaign for a bounded first run; reserve
baseline DEV/VALIDATION, candidate DEV/VALIDATION and final holdout calls/work.
See [artifact evaluation](artifact-evaluation-v1.md) for the seven-case protocol.

## Remaining evidence gates

- Real image-generation dogfood still needs the operator's GPU/backend, frozen
  model/VAE/runtime identities and seven fixed cases/seeds. This change does not
  claim measured image-quality improvement or execute a long optimization run.
- The product-goal performance and bugfix campaigns, plus a live additional
  coding-agent campaign, require separate real evidence. Existing fixture/adapter
  tests do not substitute for those results.
- PR #32 adds a bounded repo+goal bootstrap path that may reuse an existing evaluator or create a thin adapter under `.bananloop_eval/**`, then persists project context for the campaign. It still does not claim that an agent can invent a trustworthy evaluation objective when the project supplies no measurable evidence/reference at all; such cases stop with explicit `PAUSED_NEEDS_INPUT`. Hard USD caps, hostile-code sandboxing, remote-job cancellation and automatic merge are not claimed.

Keep the original PRs/main unchanged until the applicable dogfood gates are
reviewed. The integrated branch is suitable for the short verification above.
