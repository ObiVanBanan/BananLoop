# Issue #60 — token-budget QA plan (operator)

Branch: `codex/issue-60-token-budget`, draft PR #61; base `main`.
No edits to benchmark/evaluator code or DEV/HOLDOUT data.

## Semantics

- Token metering is **opt-in** via `bananloop init --max-model-tokens`.
  Currently supported only for the `codex` adapter (including all role overrides).
- Billed usage = **input_tokens + output_tokens** from Codex `--json` final
  `turn.completed` events. `cached_input_tokens` is a subset of input, not an
  additional charge.
- Before every agent action, the SQLite store durably reserves the configured
  number of tokens and refuses admission when no reserve fits. `status --json`
  and `report --json` show `resources.model_tokens`.
- A completed call replaces its reservation with actual usage. Unknown/failed
  calls without trustworthy usage retain their **worst-case reservation**;
  explicit retries reserve again. This prevents silently treating unknown
  calls as free, but is still **not** a provable upper bound on actual cost.
- A call can exceed reservation or campaign cap because Codex does not expose
  an externally enforced token cutoff. If settlement exceeds cap, the agent's
  implementation is not applied; stop reason `BUDGET_EXHAUSTED`, diagnostic
  `MODEL_TOKEN_OVERSHOOT`, pair ineligible under an official matched budget.
- Existing campaigns with no token cap preserve their approved spec hash.
- `bananloop goal/start` automatic bootstrap remains outside this feature:
  run an already-approved `init` campaign for matched-budget comparison.
  Bootstrap calls are not included in the token cap.

## Local verification (Windows PowerShell)

```powershell
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
New-Item -ItemType Directory -Force .tmp | Out-Null
python -m pytest tests/test_token_budget.py tests/test_engine.py -q
python -m compileall -q src/bananloop tests/test_token_budget.py
git diff --check main...HEAD
```

Prepare a **fresh disposable** repo/fixture containing an approved DEV evaluator.
Use a clean state path and a Codex CLI supporting `exec --json`:

```powershell
python -m bananloop.cli init --repo .\fixture --state-dir .\state-token-test --goal "Improve frozen DEV quality" --eval-cmd "python evaluator.py" --metric quality --direction maximize --target 0.99 --min-improvement 0.01 --agent codex --model gpt-6-luna --max-model-tokens 750000 --model-token-reserve 300000 --token-reserve PROPOSE=100000 --token-reserve EXECUTE=300000 --token-reserve ANALYZE=100000 --max-agent-calls 12 --max-evaluator-calls 8
python -m bananloop.cli run --repo .\fixture --state-dir .\state-token-test
python -m bananloop.cli status --repo .\fixture --state-dir .\state-token-test --json
python -m bananloop.cli report --repo .\fixture --state-dir .\state-token-test --json
```

*Expected evidence:* stage-level final usage receipts, baseline/champion preserved,
no `EXECUTE` when the remaining token reserve is less than 300k, explicit
`insufficient_token_reserve` stop. With a test metering stub that charges more
than the limit, candidate files must not be copied into the supervisor's
workspace. No HOLDOUT in smoke. Do **not** weaken Codex sandbox to force a pass.

## Limits / validation status

- Unit/regression tests added to the PR for JSONL usage, min reserve,
  overshoot blocking, historic compatibility and retry accounting.
- GitHub Actions job allocation for the first PR #61 head failed **before steps
  ran** (Linux cancelled and Windows failed without pytest steps). Therefore
  **no hosted-test success is claimed**.
- A live Codex write campaign and Windows runtime validation still need to be
  performed by the operator; until then leave PR draft.
- The full 750K head-to-head A/B is **not rerun**; it must be a fresh pair with
  identical frozen inputs/model/cap, and must discard over-cap candidates.
