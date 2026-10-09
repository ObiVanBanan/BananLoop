# Issue #33 M3 review: explainable experiment history and accounting

## Implemented

- `bananloop explain [--json]` produces a deterministic summary from durable campaign records. It separates technical outcome, scientific assessment, evaluator trust, baseline/champion measurements, guardrails, attempt counts, resources, recent hypotheses, and limitations. It labels the output as record-derived and does not invent an LLM interpretation.
- `bananloop inspect --experiment N [--json]` exposes one experiment with its causal record and durable action trace IDs.
- Reports now include a structured causal record for each experiment: planning mode and research state used for selection, predecessor references and relation, expected effect, changed paths, measurement and checks, rejection stage/reason, observation, suspected cause/confidence, proposed regression test, and evidence/action IDs.
- Scientific outcomes are reported separately from existing durable statuses, including `SCIENTIFIC_REJECT`, `DEV_ACCEPT_VALIDATION_REJECT`, `NO_CHANGES`, `BLOCKED`, `INFRA_FAILED`, `BUDGET_EXHAUSTED`, `GOAL_MET`, `UNVERIFIED_EVIDENCE`, and `NO_EXPERIMENTS` where applicable. Legacy campaign statuses are unchanged.
- Per-stage agent/evaluator call counts and elapsed time are derived from durable reservations. Bootstrap agent calls, repairs, readiness runs, and durations are recorded before/after campaign initialization in `bootstrap-plan.json`; its receipt hash is anchored in the campaign event. Replay receipts record wall time separately and do not consume campaign counters.
- The Markdown report begins with a compact decision summary and keeps operator-only measurements and detailed history below it.

## Verification

- `python -m compileall -q src tests` passed.
- Full `pytest -q` passed on the local runtime after implementation. Focused replay, CLI, report/accounting, completion, and bootstrap tests also passed after the final receipt-compatibility changes. Cross-platform CI is pending.
- Tests verify deterministic JSON command behavior, stage accounting, causal fields, bootstrap receipt integrity, and replay time accounting without campaign-state mutation.

## Remaining

This is an M3 implementation slice, not issue completion. M2 adversarial gaps and full campaign runtime guarantees need continued audit; M4 evidence-aware selection/deduplication remains open. E0–E4 dogfood results have not been claimed. USD metering remains intentionally unreported; limits and work are expressed as calls, work units, duration, and explicit unknown outcomes.

## Security boundary

`explain` and `inspect` redact validation payloads and return action metadata/IDs without raw request or response bodies. The existing operator report remains the detailed operator artifact; no validation or holdout payload is added to agent planning context.
