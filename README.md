# BananLoop v0.1

Current integration and remaining release gates: [completion readiness](docs/completion-readiness.md).
The September 19 proposal/research documents describe the original extraction;
the repository already contains the standalone engine, experiments and adapters.

BananLoop is a local supervisor for bounded, measurable improvement campaigns over an existing Git repository:

```text
baseline -> hypothesis -> implement -> snapshot -> verify -> DEV evaluate -> decide -> validate -> promote/reject -> learn -> repeat -> final holdout
```

The coding agent proposes and edits. The evaluator and deterministic policy decide. Rejected candidates never become the next starting point.

## What v0.1 implements

- immutable approved campaign spec + SHA-256 identity;
- SQLite source of truth and append-style lifecycle events;
- explicit durable stages: `PROPOSE -> EXECUTE -> SNAPSHOT -> VERIFY -> EVALUATE -> DECIDE -> [VALIDATE] -> PROMOTE/REJECT -> RECORD`;
- private evaluator Git clone, leaving the user's checkout untouched;
- sanitized temporary agent workspace with frozen evaluator assets and selected hidden paths removed from both files and local Git history;
- fixed baseline vs. moving champion;
- structured command evaluators with first-class `dev -> validation -> final holdout` tiers;
- deterministic min-improvement, target, guardrail and plateau policies;
- Experimentation Intelligence v1: DEV-only research memory, hypothesis genealogy, duplicate-mechanism guard, budget-aware planning context, and exploit/explore/reframe modes;
- Codex CLI adapter, Pi RPC adapter, and generic JSON-over-stdin/stdout command adapter;
- action IDs that prevent completed agent/evaluator calls from being repeated on resume;
- budgets for experiments, attempts, agent calls, evaluator calls and wall time;
- `start` autopilot bootstrap plus expert `init`, `run`, `status`, `resume`, `stop`, `report` CLI;
- a repo-local campaign registry for multiple goals, idempotent goal retries, and campaign selection;
- Markdown/JSON reports built only from durable records;
- explicit champion patch export with a manifest and operator reports;
- deterministic fixture demonstrating accept -> reject -> accept;
- documented `rag_tender` bridge boundary.

v0.1 targets trusted local projects. Agent isolation prevents ordinary evaluator/GOLD leakage, but it is not an OS/chroot security sandbox against a deliberately adversarial local process and does not claim a hard USD budget.

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

## Autopilot MVP: repo + goal

The shortest product path is intentionally goal-shaped:

```bash
bananloop goal "Improve measured project quality without regressions"
```

Run it from the target repository. BananLoop discovers the project, prepares and
freezes an evaluator contract, then runs the independent campaign supervisor.
The existing explicit form remains available when the repository path or agent
backend must be overridden:

```bash
bananloop start --repo . --goal "Improve measured project quality without regressions"
```

Before spending an agent call, inspect exactly what BananLoop can see:

```bash
bananloop doctor --json
```

`start` runs a bounded bootstrap role before the ordinary campaign engine:

1. inspect the repository and detected test entrypoints;
2. reuse an existing evaluator or create a thin adapter under `.bananloop_eval/`;
3. freeze cases/randomness when possible and choose deterministic or paired measurement;
4. smoke-check the evaluator before declaring the setup ready;
5. persist a project-context summary for later proposer/implementer calls;
6. create the immutable `CampaignSpec`;
7. immediately run baseline and the experiment loop.

Bootstrap may write only `.bananloop_eval/**` inside BananLoop's private managed workspace. Any attempted production/test/config edit during setup is rejected and rolled back. The user's source checkout stays unchanged. If essential references or credentials genuinely do not exist, bootstrap returns `PAUSED_NEEDS_INPUT` with the exact missing inputs instead of inventing them.

For an inspection-only setup:

```bash
bananloop start --repo . --goal "Improve measured project quality" --prepare-only
```

Use the existing detailed `bananloop init ...` interface as expert mode when the evaluator contract is already known or externally governed.

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


For secondary metrics that should only avoid getting worse while the primary metric improves, use a champion-relative non-regression guardrail:

```bash
--non-regression public_human_reject_rate:minimize
--non-regression coverage:maximize:0.01
```

The optional third field is an allowed tolerance. This is different from an absolute `--guard`: non-regression compares each candidate to the current champion instead of forcing every intermediate candidate to satisfy a final target immediately.

## Experimentation Intelligence v1

Adaptive planning uses only DEV evidence. Validation and final-holdout payloads are supervisor-only and are not written into agent history or research state.

Each proposer task receives:

- a durable `research_state` derived from prior DEV experiments;
- failure-class and mechanism summaries;
- remaining experiment/attempt/agent/evaluator budget;
- a deterministic planning mode: `exploit`, `explore`, or `reframe`;
- recent causal lessons generated from DEV outcomes.

The planner also receives a bounded `failure_signals` summary built from
agent-visible DEV failure counters, allowlisted numeric diagnostics, and
sanitized failure capsules. `experiment_evidence` records how each previous
mechanism changed those signals and the visible primary metric. A proposer must
name a supplied `target_failure_signal`, explain its `failure_mechanism`, and
cite prior DEV experiment evidence when it exists. Raw case examples and
validation/holdout measurements remain outside this context.

A rejected candidate with a positive DEV primary delta is recorded as partial
progress. It does not become the champion, but it resets the no-progress
plateau streak so the remaining experiment budget can test a refinement.

Hypotheses carry genealogy fields: `relation`, `prior_experiment_refs`, and `novelty_rationale`. Reusing the same `mechanism_key` without declaring a refinement/retry/supersession is rejected before implementation and does not consume a scientific experiment.

Evaluators may expose numeric aggregate DEV diagnostics for planning. Diagnostics are hidden by default and must be explicitly allowlisted:

```bash
--agent-diagnostic wrong_not_found_rate
--agent-diagnostic material_constraint_failures
```

Do not use validation or holdout-derived diagnostics here.

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

## Pi adapter

Pi is integrated through its RPC JSONL mode, not through a one-shot shell wrapper. Pi proposes/implements inside the managed workspace; BananLoop still owns evaluation and ACCEPT/REJECT.

Example with Ollama Cloud:

```powershell
$env:PI_OLLAMA_WEB_TOOLS="0"

uv run bananloop init `
  --repo .tmp\pi-fixture `
  --state-dir .tmp\pi-state `
  --goal "Raise quality while keeping regressions at zero" `
  --eval-cmd "python evaluator.py" `
  --metric quality `
  --direction maximize `
  --target 1 `
  --min-improvement 0.5 `
  --guard "regressions<=0" `
  --check tests `
  --editable "value.txt" `
  --editable "guard.txt" `
  --protect "evaluator.py" `
  --protect "agent.py" `
  --agent pi `
  --provider ollama-cloud `
  --model "nemotron-3-nano:30b" `
  --thinking off `
  --max-experiments 2 `
  --max-agent-calls 4

uv run bananloop run --repo .tmp\pi-fixture --state-dir .tmp\pi-state
uv run bananloop report --repo .tmp\pi-fixture --state-dir .tmp\pi-state
```

The adapter starts `pi --mode rpc --no-session --no-approve`. `--provider`, `--model`, and `--thinking` are translated to Pi startup options. Use repeated `--pi-arg` for additional Pi CLI flags.

Pi's final assistant result must contain one unambiguous JSON object matching the BananLoop role contract. BananLoop accepts strict JSON directly, can recover one embedded JSON object from surrounding prose, and performs one in-session format-repair turn when needed. Ambiguous/malformed output remains an `AGENT_PROTOCOL_ERROR`.


## Agent/evaluator isolation

Evaluator assets are automatically removed from the temporary agent workspace. Use `--agent-hide` for additional sensitive files or directories such as GOLD labels, harness code, benchmark tests, or review artifacts. Patterns are repeatable:

```bash
bananloop init \
  ... \
  --agent-hide "harness_rag/**" \
  --agent-hide "data/**" \
  --agent-hide "tests/**"
```

The agent workspace is copied to the system temporary directory without the source repository's `.git` history, then initialized as a fresh Git repository. Implementer changes are transferred back to the evaluator workspace as a Git patch. `.bananloop_eval/**` and frozen `--eval-asset` paths are always hidden automatically.

Use repeated `--agent-metric` flags to restrict evaluation feedback visible in the agent task context:

```bash
--agent-metric public_hard_pass_rate \
--agent-metric public_false_match_rate \
--agent-metric public_human_reject_rate
```

When at least one agent-visible metric is configured, other metric values and guardrails are omitted from agent baseline/champion/history views.

## Staged evaluation protocol

BananLoop can own the full evaluation lifecycle instead of requiring a manual post-campaign holdout:

- `--eval-cmd` is the **dev** evaluator. It runs on the baseline and every candidate and drives the ordinary iterative decision.
- `--validation-eval-cmd` is a blind **validation** evaluator. It runs on the baseline and only on candidates that first pass the dev policy. A candidate is promoted only when the same metric/guardrail policy also improves against the current validation champion.
- `--holdout-eval-cmd` is the **final holdout** evaluator. It runs once on the terminal champion after normal campaign termination. Its detailed result is never added to agent context or experiment history.
- Validation/holdout commands may point at external datasets. Repository evaluator scripts/configs referenced by any tier are frozen and automatically hidden from the agent workspace.
- When a holdout is configured, BananLoop reserves the final evaluator-call budget slot for it.

`GOAL_MET` requires the final holdout to meet the target and mandatory guards.
A valid final measurement below target ends as `STOPPED / FINAL_HOLDOUT_REJECTED`;
it never reopens adaptive search. A failed final invocation pauses at
`FINAL_HOLDOUT`; `resume --retry-failed-action` retries only that assessment,
within the remaining call/work/time limits. A completed final receipt is reused
even when it consumed the last permitted call.

Example:

```bash
bananloop init \
  --repo . \
  --goal "Improve matching quality without false positives" \
  --eval-cmd "python scripts/evaluate.py --split dev" \
  --validation-eval-cmd "python scripts/evaluate.py --split validation --aggregate-only" \
  --holdout-eval-cmd "python scripts/evaluate.py --split holdout --aggregate-only" \
  --metric quality \
  --direction maximize \
  --target 0.80 \
  --min-improvement 0.01 \
  --guard "false_match_rate<=0" \
  --max-evaluator-calls 16
```

The three commands use the same evaluator JSON contract and metric names. Validation details remain supervisor-side; the agent can observe only the ordinary experiment outcome plus whatever dev metrics were explicitly allowed through `--agent-metric`. The final report renders dev, validation, and holdout results separately.

### Anti-gaming diff guard

Use repeatable `--forbid-diff-regex` rules to reject suspicious newly added production lines before any evaluator call. This is useful for blocking benchmark IDs, GOLD imports, known labels, or evaluator-module references:

```bash
bananloop init \
  ... \
  --forbid-diff-regex '\\b(?:harness_gold|deep_gold)\\b' \
  --forbid-diff-regex '\\bgold_q[0-9]+\\b' \
  --forbid-diff-regex '\\bknown_(?:positive|rejected)_ids\\b'
```

The scanner examines added diff lines only, so deleting an old forbidden reference is allowed. It is a deterministic anti-gaming tripwire, not an OS security boundary; keep evaluation assets hidden with `--agent-hide`/frozen evaluator assets as the primary isolation mechanism.

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
bananloop campaigns list --repo .
bananloop status --repo . --json
bananloop report --repo .
bananloop report --repo . --json --output campaign.json
bananloop stop --repo .
bananloop resume --repo .
bananloop export --repo . --output ../champion-export
bananloop trace export --repo . --campaign-id ID --output ../campaign-trace
```

Use `--campaign-id ID` with run/resume/status/stop/report/export/trace export to select an
indexed campaign. Without it, commands use the active campaign shown by
`campaigns list`. Repeating the same canonical goal resumes its indexed
campaign; use `--request-id ID` on `goal`/`start` to make retries explicit, or
use a new request ID to intentionally start another run for the same goal.

Use `--state-dir PATH` on all commands to keep campaign state in an explicit
single-campaign directory (useful for CI/tests). Existing state at the old
`~/.bananloop/projects/<repo-hash>/campaign.sqlite3` path stays in place and is
listed as `legacy`.

The portable trace is a read-only export for an operator or reviewer. It
contains `events.jsonl`, `summary.json`, `report.md`, and a checksum manifest.
To share a campaign, send `events.jsonl`, `summary.json`, and `report.md` (the
manifest is useful for integrity checks). The shareable export omits raw goal
text, agent rationales, evaluator output payloads, case inputs, labels, and
per-case validation/holdout results; it records only a goal hash and aggregate
metrics. Provider cost remains `unknown` unless the provider supplies metering.
Choose an output path outside the source checkout when you do not want the
export files in that checkout. BananLoop only refreshes a directory carrying
its own trace manifest; other non-empty directories are refused.

`stop` sends a durable cancellation request to the running supervisor. Managed
local processes are polled for cancellation and the campaign deadline. The
supervisor terminates the owned process group/tree, settles the reservation and
keeps the last champion. Neither user stop nor an expired deadline launches a
new holdout call; its final assessment remains pending if it has not run.
Ctrl+C interrupts the active action and allows `resume` to continue within the
original budget. Remote jobs and deliberately detached processes remain outside
these local cancellation guarantees.

`export` requires an idle campaign and a new output directory. It writes
`champion.patch` (including binary changes), `manifest.json`, `report.json` and
`report.md`. It never applies the patch or changes the source checkout. The
manifest names the required base commit and the final assessment, including a
rejected/pending holdout. Artifact files remain in the state directory; the
export contains their references, so preserve the complete state for full evidence.
Review these operator reports before sharing them or placing them in agent context.

## Codex plugin: chat UX, independent core

BananLoop now ships a Codex plugin bundle under `plugins/bananloop/` plus a
repo-local marketplace entry. The plugin deliberately does **not** move
accept/reject decisions into the Codex conversation. Its skill is the UX/router;
the BananLoop process remains the measurement authority.

From a checkout of this repository, add it as a Codex plugin marketplace and
install the plugin using the normal Codex plugin flow. Then a Codex conversation
can use `$bananloop-goal` with a human goal instead of reconstructing the CLI
surface.

The boundary is intentional:

```text
Codex chat / skill
      |
      | repo + human goal
      v
BananLoop bootstrap ----> project-specific evaluator adapter
      |                         |
      |                    smoke / repair
      |                         |
      +-------------------- freeze + hash
                                |
Codex proposer/implementer <----+---- hidden evaluator assets
      |
      v
BananLoop DEV -> VALIDATION -> FINAL HOLDOUT -> accept/reject
```

Codex's bundled Stop hook is only a lifecycle bridge: it reads the session's
durable BananLoop campaign state and can request one continuation if a campaign
is still RUNNING without a supervisor lock. The hook never evaluates candidates
or launches workers; BananLoop core remains the measurement authority. Review and
trust it in Codex's `/hooks` browser before relying on it. See
`docs/codex-plugin.md`.

Model selection also stays simple: when BananLoop does not receive `--model`,
the Codex adapter leaves model selection to the user's Codex configuration.
Advanced `start`/expert `init` flags remain available for explicit overrides.

For a repeatable campaign with different models per active role, save a TOML
profile and pass `--agent-profiles agents.toml` to `start` or `init`:

```toml
[default]
kind = "codex"
model = "your-default-model-id"
reasoning_effort = "high"

[roles.implement]
model = "your-coding-model-id"
reasoning_effort = "xhigh"
```

Supported role tables are `bootstrap`, `propose`, `implement`, and `analyze`.
Settings are frozen into the campaign; credentials remain in the normal CLI
environment. Do not put secrets in this file.

## Scope boundaries

Not in v0.1: parallel experiments, DAG orchestration, model routing, autonomous invention of a trustworthy evaluation objective from no evidence, vector memory, SaaS/web UI, auto push/merge, generic LLM judges, distributed workers, or a full security sandbox.

See `design/adr.md`, `standalone_harness_proposal.md`, and `examples/rag_tender/README.md` for rationale and integration guidance.


## Project preparation and inexpensive candidate preflight

For a new project, start with readiness instead of spending experiment calls:

```bash
bananloop prepare --repo . --goal "Improve the DEV metric without regressing validation"
bananloop run --repo .
bananloop report --repo .
```

`prepare` uses the existing repo+goal bootstrap, discovers the environment,
builds and freezes the evaluator contract, and runs the bootstrap readiness checks
without beginning scientific candidate trials. It is equivalent to the expert
`bananloop start --prepare-only --repo . --goal "..."` path; it does **not**
magically establish the evaluator's scientific validity. Inspect the readiness
receipt and signal-sanity trust status before relying on numerical improvements.
If the bootstrap needs independent reference data, provide it instead of
relaxing scoring checks.

The candidate VERIFY stage now statically compiles changed Python sources
**without executing or importing candidate code**. Syntax failures are recorded
as `IMPLEMENTATION_BLOCKED / CANDIDATE_PREFLIGHT_FAILED` and rejected before
spending a DEV evaluator call. This is not a replacement for version-specific
sklearn/API checks, runtime tests, dependency checks, or integration smoke tests.

A terminal `GOAL_MET` may mean the *baseline* already met the configured
target. The report therefore separates `BASELINE_ALREADY_MET_TARGET` from
`CHAMPION_IMPROVED` and `NO_VALIDATED_IMPROVEMENT`; do not infer genuine
quality gains from the terminal status alone.

Directory scope entries ending with a slash (such as
`src/nomenclature_matcher/`) are treated recursively. Sibling paths
(`src/nomenclature_matcher_extra/`) are not implicitly editable.

## DEV error reflection and new hypothesis selection

When the DEV evaluator exposes `failure_signals` **or** public
`failure_examples`, BananLoop runs its budget-bounded, read-only `ANALYZE`
stage. The analyst can inspect those DEV mistakes and the candidate checkout.
It records generalized failure capsules: observation, likely mechanism,
regression test, and confidence. The next proposer receives the sanitized
capsules, the DEV experiment history, and guidance to build a different,
falsifiable causal hypothesis. Case identities/expected answers must not appear
in those durable capsules or in subsequent proposal prompts.

The core also detects **stagnant mechanisms**: when the two most recent
scientific DEV trials for a mechanism have the same DEV outcome, primary deltas
that differ by less than the configured `min_improvement` (or both lie within
the symmetric no-effect band), and unchanged other allowlisted aggregate metrics,
it selects `reframe` and rejects another proposal with that exact
`mechanism_key`. A small DEV gain **or loss** below `min_improvement` is
`DEV_NO_EFFECT`: it is inconclusive, cannot reset the no-progress streak, and
cannot promote a champion. A decrease of at least that threshold is
`DEV_REGRESSION`. The threshold is a conservative decision rule rather than
proof of statistical insignificance; readiness separately checks that A/A
noise is smaller than this threshold. Do not assume different units/diagnostics
share the primary metric's noise calibration. The report shows the stuck mechanisms
and the failure analyst's observation, inference, and next DEV test. This
does not identify semantically identical mechanisms renamed by an agent, and
it cannot diagnose specific failures unless the project evaluator supplies DEV
signals or examples. Only frozen, allowlisted DEV evidence drives the rule;
validation/holdout results remain unavailable to the planner.

The `ANALYZE` stage uses an agent call if budget is available, so allow some
agent budget beyond proposal+implementation calls when testing failure
reflection. Missing diagnostic evidence is not a reason to hallucinate a
root cause.

### Metric-aware reflection, family-level reframe and dependency preflight

The DEV analyst/proposer now receive `primary_metric_failure_guidance`. For
PR-AUC and ROC-AUC, a false negative at a fixed decision cutoff does **not**
by itself explain the ranking metric, and modifying only the cutoff cannot
improve average precision or ROC-AUC while continuous scores remain fixed.
Ask the trusted DEV evaluator for public score-order / precision-recall
diagnostics if available; never invent such evidence or use hidden-tier labels.

Each new hypothesis may specify `mechanism_family` (default: `mechanism_key`
for previously saved campaigns). Families group variations of the *same
causal intervention*, e.g. multiple regularization strengths. On `reframe`
the core excludes `failed_dev_families` derived exclusively from recent
unsuccessful DEV trials, even if an agent renames the mechanism key. DEV
failure capsules and suggested discriminating regression tests are retained
in `dev_lessons` across recovery. Family names are declared by the agent;
this is not semantic-equivalence proof, and deliberately avoids leaking
validation/holdout results into adaptation.

After syntax and scope checks, candidate VERIFY runs a bounded static AST
check for direct scikit-learn `OneHotEncoder(sparse=...)` calls. It queries
**only package metadata** in the evaluator's isolated Python interpreter;
for scikit-learn 1.4 or later, incompatible candidates are rejected as
`IMPLEMENTATION_BLOCKED/CANDIDATE_PREFLIGHT_FAILED` without spending a
DEV evaluator call. Wrapper commands and unknown runtimes skip this check,
and other APIs are still subject to normal evaluator smoke/integration tests.
This check does not execute candidate code or import it.

Recommended dogfood: use `plateau_experiments=3` and sufficient budget for
three distinguishable scientific trials. Verify the report contains past
DEV theories, next discriminating tests, and failed mechanism families before
judging whether the third hypothesis is genuinely different.
