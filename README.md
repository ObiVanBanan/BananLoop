# BananLoop v0.1

BananLoop is a local supervisor for bounded, measurable improvement campaigns over an existing Git repository:

```text
baseline -> hypothesis -> implement -> snapshot -> verify -> evaluate -> decide -> promote/reject -> repeat
```

The coding agent proposes and edits. The evaluator and deterministic policy decide. Rejected candidates never become the next starting point.

## What v0.1 implements

- immutable approved campaign spec + SHA-256 identity;
- SQLite source of truth and append-style lifecycle events;
- explicit durable stages: `PROPOSE -> EXECUTE -> SNAPSHOT -> VERIFY -> EVALUATE -> DECIDE -> PROMOTE/REJECT -> RECORD`;
- private local Git clone, leaving the user's checkout untouched;
- fixed baseline vs. moving champion;
- structured command evaluator with metric/check validation;
- deterministic min-improvement, target, guardrail and plateau policies;
- Codex CLI adapter and generic JSON-over-stdin/stdout command adapter;
- action IDs that prevent completed agent/evaluator calls from being repeated on resume;
- budgets for experiments, attempts, agent calls, evaluator calls and wall time;
- `init`, `run`, `status`, `resume`, `stop`, `report` CLI;
- Markdown/JSON reports built only from durable records;
- deterministic fixture demonstrating accept -> reject -> accept;
- documented `rag_tender` bridge boundary.

v0.1 targets trusted local projects. It is not a malicious-code sandbox and does not claim a hard USD budget.

## Install

Python 3.11+ and Git are required.

```bash
python -m pip install -e ".[test]"
pytest
```

With `uv`:

```bash
uv sync --extra test
uv run pytest
```

## 2-minute fixture demo

Create a tiny standalone Git project:

```bash
python examples/fixture/create_fixture.py /tmp/bananloop-fixture
```

Initialize a campaign. Use the same Python executable for the fixture scripts if `python` is named differently on your machine:

```bash
bananloop init \
  --repo /tmp/bananloop-fixture \
  --goal "Raise quality to 3 while regressions stay at zero" \
  --eval-cmd "python evaluator.py" \
  --metric quality \
  --direction maximize \
  --target 3 \
  --min-improvement 0.5 \
  --guard "regressions<=0" \
  --check tests \
  --editable "value.txt" \
  --editable "guard.txt" \
  --protect "evaluator.py" \
  --protect "agent.py" \
  --agent command \
  --agent-cmd "python agent.py" \
  --max-experiments 5 \
  --max-attempts 6 \
  --max-agent-calls 12 \
  --max-evaluator-calls 8

bananloop run --repo /tmp/bananloop-fixture
bananloop status --repo /tmp/bananloop-fixture
bananloop report --repo /tmp/bananloop-fixture
```

Expected scientific history:

1. quality `0 -> 1`, guard passes: **ACCEPTED**;
2. candidate quality `2`, but `regressions=1`: **REJECTED**, champion remains `1`;
3. quality `1 -> 3`, guard passes: **ACCEPTED**, campaign stops with `GOAL_MET`.

The source fixture repository itself remains at its original commit. All candidate commits live in BananLoop's private workspace under `~/.bananloop/projects/<repo-hash>/` unless `--state-dir` is supplied.

## Codex adapter

Use `--agent codex` (the default). BananLoop calls Codex non-interactively with a JSON Schema and `--output-last-message`, while the engine retains lifecycle/acceptance ownership.

```bash
bananloop init \
  --repo . \
  --goal "Improve benchmark score without breaking tests" \
  --eval-cmd "python scripts/evaluate.py" \
  --metric score \
  --direction maximize \
  --target 0.90 \
  --min-improvement 0.01 \
  --check tests \
  --editable "src/**" \
  --protect "scripts/evaluate.py" \
  --protect "tests/**" \
  --agent codex

bananloop run --repo .
```

`--codex-arg` can be repeated for installation-specific Codex flags.

## Evaluator contract

The evaluation command runs with the frozen candidate workspace as `cwd` and must print one JSON object:

```json
{
  "schema_version": 1,
  "status": "ok",
  "metrics": {
    "quality": 0.81,
    "latency_ms": {"value": 220, "unit": "ms", "n": 100}
  },
  "checks": {
    "tests": true
  }
}
```

A malformed/incomplete result is `INVALID_EVALUATION` and can never promote a candidate. A protected/out-of-scope Git diff becomes `SCOPE_VIOLATION` before measurement.

## Resume model

Every stage transition is committed to SQLite. Agent/evaluator calls have stable action IDs and request hashes:

- `COMPLETED` action: reuse durable result, never call again;
- stage completed but process interrupted: `resume` continues at the next durable stage;
- action still `RUNNING` after an unclean crash: outcome is unknown, so BananLoop pauses rather than blindly duplicating an external call.

Promotion is idempotent at the store level: a completed experiment has one durable decision and champion snapshot.

## State and reports

```bash
bananloop status --repo . --json
bananloop report --repo .
bananloop report --repo . --json --output campaign.json
bananloop stop --repo .
bananloop resume --repo .
```

Use `--state-dir PATH` on all commands to keep campaign state in an explicit directory (useful for CI/tests).

## Scope boundaries

Not in v0.1: parallel experiments, DAG orchestration, model routing, generated evaluators, vector memory, SaaS/web UI, auto push/merge, generic LLM judges, distributed workers, or a full security sandbox.

See `design/adr.md`, `standalone_harness_proposal.md`, and `examples/rag_tender/README.md` for rationale and integration guidance.
