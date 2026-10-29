# BananLoop — opt-in LLM Semantic Selector QA

## Scope

This is **an experimental selector**, not a new evaluator or a promise of scientific improvement.
The default `selection_mode="deterministic"` is unchanged. In `llm` mode
BananLoop first runs the existing Python admissibility gates. If **two or more**
candidates are admissible and the budget can fund selection **and** implementation,
a read-only `select` agent compares the eligible portfolio and DEV-only evidence.
The core validates the returned candidate ID and cited refs. On an invalid,
malformed or failed answer, the deterministic choice wins. The final portfolio
decision and the judge action are persisted for resume.

**Never put validation/holdout data in the selector prompt.** The decision is a
prediction only; promotion still requires independent evaluation.

## Deterministic checks (no models)

Work in an isolated clean worktree of `codex/llm-semantic-selector-v1`:

```bash
export PYTHONPATH=src
python -m compileall -q src
python -m pytest tests/test_research.py tests/test_engine.py -q -o addopts=''
python -m pytest -q -o addopts=''
git diff --check
```

Verify these properties from tests, code and any SQLite traces:

1. `deterministic` remains default. Its approved spec hash matches historical specs.
2. `llm` with **one** admissible hypothesis makes **no** selector agent call.
3. The `select` action's `agent_visible_request` contains **only eligible_candidates**,
   TASK.md, public goal/context, public DEV history and budget/planning information.
   Its sibling `base_record` is internal and **must not be passed to the agent**.
4. The candidate ID must be one of the gates-approved IDs. An unknown ID, invalid
   schema, or fake evidence reference causes deterministic fallback and an event.
5. There must be enough calls for the extra selector action **plus implementer**.
   Otherwise log `SKIPPED`, not a user-visible budget failure.
6. The action `exp:<id>:propose:semantic-select` is durable. Simulated
   `PORTFOLIO_SAVED` interruption and resume must reuse the frozen choice,
   without invoking the selector again.
7. Inference does not change DEV evaluation, guardrails, validation or holdout.
8. Role configuration via `[roles.select]` is accepted; unsupported roles are not.

## Tiny real-model smoke (optional; only after green tests)

Use a fresh **CPU-only** copy of `examples/calibration/mini_matcher`. Never
reuse an earlier campaign's state; do not touch the existing checkout.

```bash
mkdir -p /tmp/bl-semantic-smoke
cp -r examples/calibration/mini_matcher /tmp/bl-semantic-smoke/matcher
(cd /tmp/bl-semantic-smoke/matcher && git init &&
 git config user.name QA && git config user.email qa@example.invalid &&
 git add . && git commit -m baseline)

PYTHONPATH=src python -m bananloop.cli init \
 --repo /tmp/bl-semantic-smoke/matcher \
 --state-dir /tmp/bl-semantic-smoke/state \
 --goal "Read TASK.md and improve analog matching on DEV" \
 --eval-cmd "python evaluator.py" \
 --metric quality --direction maximize --target 0.99 \
 --min-improvement 0.02 --check cases_valid \
 --editable matcher.py --protect evaluator.py \
 --agent-hide evaluator.py --eval-asset evaluator.py \
 --agent codex --model gpt-6-luna --selector-mode llm \
 --max-experiments 2 --max-attempts 3 \
 --max-agent-calls 9 --max-evaluator-calls 5 --plateau 3

PYTHONPATH=src python -m bananloop.cli run \
 --repo /tmp/bl-semantic-smoke/matcher \
 --state-dir /tmp/bl-semantic-smoke/state

PYTHONPATH=src python -m bananloop.cli report \
 --repo /tmp/bl-semantic-smoke/matcher \
 --state-dir /tmp/bl-semantic-smoke/state --json
```

The model may produce only one admissible hypothesis, in which case no judge
is expected; report `SKIPPED` or absence of judge calls honestly. To choose
a separate model for the role, create a TOML file:

```toml
[roles.select]
model = "gpt-6-luna"
```

and pass `--agent-profiles PATH` at init. The role override is frozen in
CampaignSpec; it cannot be changed on an existing campaign without approval.

## Verify durable records

Inspect `campaign.sqlite3` inside the fresh state directory:

```sql
SELECT id,status,kind,stage FROM actions
 WHERE id LIKE '%semantic-select%';
SELECT event_type,payload_json FROM events
 WHERE event_type IN ('PORTFOLIO_SELECTION','PORTFOLIO_DECISION',
                      'PORTFOLIO_REPAIR_DECISION')
 ORDER BY id;
```

Check the select action's `request_json` (which contains both
`agent_visible_request` and private `base_record`) and saved
`response_json`. Never assume that `PORTFOLIO_DECISION` was made by LLM
if there was no `PORTFOLIO_SELECTION` with status `SELECTED`.

## Deliverables

Provide exact SHA, environment, tests, selected IDs, evidence citations,
model errors, action/event trace, fallback counts, time and observed usage
(or `unavailable`). Save SQLite and JSON artifacts. Do not alter scoring,
protected files, or benchmark data. Do **not** claim that the semantic
selector is scientifically better based on one miniature run; a later
paired control/treatment benchmark is needed.


## Follow-up QA after evidence-reference replay finding (2026-10-29)

The first independent QA observed `324 passed` on SHA
`2ad9f211530e0524b5decc72d28603d5018801c3`, one successful Luna
smoke, and **2/6** replay choices rejected because the judge produced
annotated references like `"exp:1: ..."` or `"TASK.md: ..."`.
The validator correctly rejected these strings; **do not loosen it**.

The follow-up patch adds:
- A public per-candidate exact-reference allowlist in `selection_contract`.
- Explicit instructions distinguishing `reason` prose from `evidence_refs` IDs.
- Codex structured JSON schema enums for all eligible candidate IDs and
  source-defined DEV evidence references (the Python validator still enforces
  per-candidate membership).
- Fault-injection tests for protected candidates, one eligible candidate,
  malformed/mismatched responses, crash before final decision and orphan RUNNING
  judge actions. Identical audit events are deduplicated on pre-decision resume.

**Required verification:** run `compileall`, `tests/test_codex_adapter.py`,
`tests/test_engine.py`, `tests/test_research.py`, `tests/test_cli.py`,
`tests/test_agent_profiles.py`, and the entire pytest suite on the **new HEAD**,
not on the earlier `2ad9f211` snapshot. No source modifications during QA.

Then replay exactly the same six archived portfolios with Luna as judge.
A changed prompt/JSON schema requires fresh judge calls; do not re-label the
historical JSON as though it came from the new version. For each run report
whether the model returned `selected_candidate_id` from the eligible list
and `evidence_refs` only from the selected candidate's source references.
The two previous failure cases `exploit-2` and `reframe-2` must be included.
Keep source portfolios, task text and the DEV history unchanged.

**Failure thresholds:**
- A prohibited file reaches the judge, an ineligible ID gets implemented,
  or hidden evaluator/validation/holdout content is exposed: P0.
- Duplicate judge call on resume with completed action, incorrect budget
  reservation, or automatic re-execution of an orphan RUNNING action: P1.
- An invalid evidence ref causes safe deterministic fallback: P2 functional
  selection loss, not a gate bypass.
- A model transport failure/sandbox failure: infrastructure, with full logs.

A replay of an old (pre-patch) model response should still fail the validator
when references are annotated; this is EXPECTED and guards against accidental
weakening. Preserve the old JSON for regression proof.

Save SQLite, action requests/responses, `PORTFOLIO_SELECTION` events, test
logs, exact SHA, and per-run model usage when available. Leave PR #70 Draft
until both deterministic fault injection and live evidence-reference replay pass.
