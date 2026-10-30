# Selector A/B preregistration v1

This folder preregisters a deterministic-versus-LLM selector comparison on the local mini-matcher fixture. One live LLM arm was attempted and failed before candidate evaluation; see `manifest.json`, `trials.jsonl`, and the artifact directory recorded there. The new three-candidate portfolio is manually authored from public `TASK.md`; it is frozen for this preregistration and is not represented as the historical smoke portfolio.

## Frozen portions

- Task: `examples/calibration/mini_matcher`, 16 DEV cases, quality maximization, with task and selector implementation hashes in `manifest.json`.
- Portfolio: `frozen_portfolio.json`, three admissible mechanism hypotheses, byte SHA-256 recorded in the manifest.
- Arms: deterministic core selection versus opt-in LLM semantic selection after the same admissibility gates.
- Replicates: paired seeds 53, 101, 211, 307, 401; arm order is frozen from randomization seed 20261030.
- Authorized workload for the execution runs: ten campaigns, one experiment per campaign, at most 20 trial evaluator calls plus one baseline-only setup smoke (21 total), at most 15 agent calls (five selector plus ten implementation), and five LLM selector calls. Each campaign uses the same frozen three-candidate portfolio and identical budgets in both arms.
- Replicate labels `53`, `101`, `211`, `307`, and `401` identify paired runs; they do not seed the Codex model or a CLI random generator. Only arm order was randomized from seed `20261030`.
- Use Codex subscription quota only, with no separate API-billed spend authorized. The prior live-use allowance caps the comparison at 20 minutes. Codex enforces its dynamic subscription quota; the CLI does not expose a numeric remaining-token cap. Record token use if reported, otherwise leave it unknown. The execution command disables user config/MCPs while retaining Codex-managed authentication.
- Outcomes: per-seed DEV reward delta, selected ID, status/fallback, errors, elapsed time, token usage, provider charge and action/event trace. No winner claim.

## Live execution status

The first slot (seed 53, LLM selector) selected `constraint_intersection`, then stopped with `IMPLEMENTATION_BLOCKED`: the nested Codex process could not write `matcher.py` under its filesystem policy. The baseline DEV score was 0.4375; no candidate score was produced. The slot is recorded as failed and will not be retried. The remaining nine slots were not started on Windows. The user then directed continuation in Docker/Ubuntu; subsequent arms use a fixed Docker image and read-only auth mount, while the failed seed 53 LLM slot is not repeated. A separate one-call write probe confirmed the nested CLI reports effective `read-only` despite a requested `workspace-write` sandbox and rejects file creation; no further model calls were made. The campaign used two Codex calls and one local baseline evaluator call, with zero candidate evaluator calls. A separately preregistered direct sandbox diagnostic used one Codex call and reported 7,957 tokens. The campaign adapter did not expose its token usage. There was zero separately billed API spend. The report therefore makes no comparison or winner claim.

## Offline report

The report utility validates the current task/code hashes, frozen portfolio hash, trial identities and authorization record. It reads JSONL and never invokes campaign code, a model provider, a selector or evaluator.

```powershell
python -m benchmarks.selector_ab_v1.report `
  --root . `
  --manifest benchmarks/selector_ab_v1/manifest.json `
  --portfolio benchmarks/selector_ab_v1/frozen_portfolio.json `
  --trials benchmarks/selector_ab_v1/trials.jsonl `
  --output selector-report.json
python -m pytest tests/test_selector_report.py tests/test_selector_preregistration.py -q
```

Each JSONL row represents one `(seed, arm)` slot and must include `execution_ordinal` (1 or 2). The ordinal must agree with that seed's frozen `arm_order_by_replicate`. Rows also require `experiment_id`, `seed`, `arm`, `manifest_sha256`, `portfolio_sha256`, the exact `task_input_sha256` and `selector_implementation_sha256` maps, `status`, and `dev_reward` plus `selected_candidate_id` when completed. `cost_usd`, `tokens`, `duration_seconds`, and `failure_reason` are recorded when known. `failed` rows require a reason; failed/incomplete rows cannot claim a reward. Duplicate slots, unrecognized seeds/arms, hash mismatches and incomplete data fabricated as scores are rejected.

The report shows every expected seed pair, missing/failed/incomplete rows, direction-aware per-seed DEV deltas, known totals, unknown measurements, resource authorization status and `winner_claim: null`. A missing recorded cost/token/duration is unknown, never treated as zero. Missing slots are counted separately from observed rows with unknown measurements. If rows exist while the manifest says `NOT_AUTHORIZED_NOT_RUN`, the report rejects them. An `AUTHORIZED` manifest must set top-level `status` to `authorized_frozen_portfolio_not_historical`, include an authorization reference and positive cost/token/time caps; top-level status must agree with `resource_authorization.status`. Known totals above a cap are rejected. Rows are rejected while the manifest is `NOT_AUTHORIZED_NOT_RUN`. The report cannot itself grant authorization.

## Current status and gates

The report accepts either a numeric token cap or `token_cap_basis=provider_enforced_codex_subscription_quota` with no numeric token count. A subscription-quota run is still reported with unknown local token totals when Codex does not expose usage. The fixture has no holdout, so even completed pairs support descriptive DEV evidence only. Every campaign must use `--frozen-portfolio` and `--replicate-label`; omit no failed/missing trial and do not retry a seed.

Example init settings (repeat for every arm/replicate, changing only the arm and label):

```powershell
python -m bananloop.cli init `
  --repo <trial-repo> --state-dir <trial-state> `
  --goal "Read TASK.md and improve analog matching on DEV" `
  --eval-cmd "python evaluator.py" --metric quality --direction maximize `
  --target 0.99 --min-improvement 0.02 --check cases_valid `
  --editable matcher.py --protect evaluator.py --eval-asset evaluator.py --agent-hide evaluator.py `
  --agent codex --model gpt-6-luna --codex-arg=--ignore-user-config `
  --codex-arg=--disable --codex-arg=multi_agent `
  --selector-mode <deterministic|llm> --frozen-portfolio benchmarks/selector_ab_v1/frozen_portfolio.json `
  --replicate-label <53|101|211|307|401> `
  --max-experiments 1 --max-attempts 2 --max-agent-calls 2 --max-evaluator-calls 2 `
  --evaluator-timeout-seconds 10 --deadline-seconds 1200 --plateau 1
python -m bananloop.cli run --repo <trial-repo> --state-dir <trial-state> --agent-timeout-seconds 60
```

No validation or HOLDOUT command is configured. The evaluator is CPU-local and only reports aggregate DEV quality. Preserve the campaign SQLite DB, JSON report, selected candidate/selector event, CLI version, arm execution ordinal and elapsed time for every trial.
