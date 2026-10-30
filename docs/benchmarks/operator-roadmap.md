# Operator roadmap benchmark record

Status date: 2026-10-30. This document records artifacts present in the target `main/` worktree and separates completed evidence from planned work. The source task catalog is `hf/README.md` (AutoResearchExam v1.1); the actual local run evidence is `benchmark_runs/autoresearchexam-v1.1/run_manifest.json` and `results/partial_summary.md`.

## Existing ten-task inventory

The local source bundle contains the ten AutoResearchExam v1.1 tasks listed below. The target-worktree evidence is `benchmark_runs/autoresearchexam-v1.1/run_manifest.json` and `results/partial_summary.md`. The manifest identifies branch `origin/main` at `76db695f20717563be64a4dff36fd639db64e115`, model `gpt-6-luna`, medium reasoning, six hypothesis rounds, the intermediate split with seed 40, one BananLoop arm, and `codex_arm_rerun: false`. The partial summary records current champion rewards and explicitly identifies unfinished tasks. There is no local Codex comparison arm in this run.

| Task | Project class | Official metric and direction | BananLoop status in target archive |
|---|---|---|---|
| Carps — Star Discrepancy | data selection | Mean exact star discrepancy; **lower is better** (`hf/tasks/carps-star-discrepancy-subset-select/README.md`). | STOPPED; 6 experiments, reward 0.522914, 2 accepted, 0 invalid evaluations. |
| Budgeted Imputation — MCAR50 | imputation | Unweighted mean feature-wise imputation R² over scorable masked features; **higher is better** (`hf/tasks/budgeted-imputation-mcar50/instruction.md`). | STOPPED; 6 experiments, reward 0.545766, 2 accepted, 0 invalid evaluations. |
| Label-Efficient Risk Estimator | risk estimation | Median per-cell ratio of model error to random-label reference; **lower is better** (`hf/tasks/label-efficient-risk-estimator/instruction.md`). | STOPPED; 6 experiments, reward 0.445984, 3 accepted, 0 invalid evaluations. |
| Shortest Valid CI | metric estimation | Mean per-setting interval score with coverage penalty; **lower is better** (`hf/tasks/shortest-valid-ci-l2-ece/instruction.md`). | STOPPED; 6 experiments, reward 0.498016, 2 accepted, 1 invalid evaluation. |
| Sketched Newton | streaming optimization | Mean absolute relative error of projected covariance estimate; **lower is better** (`hf/tasks/sketched-newton-cov-estimator/instruction.md`). | STOPPED; 6 experiments, reward 0.000000, 0 accepted, 6 invalid evaluations. |
| ACT Tensor — Sparse Panel Imputation | panel imputation | Mean imputation R² on hidden entries; **higher is better** (`hf/tasks/act-tensor-sparse-panel-imputation-r2/instruction.md`). | STOPPED; 6 experiments, reward 0.247287, 4 accepted, 2 invalid evaluations. |
| ActivePrune | unlabeled data selection | Mean final-iteration macro-F1 over seeded evaluation runs; **higher is better** (`hf/tasks/activeprune-al-unlabeled-pool-pruning/instruction.md`). | STOPPED; 6 experiments, reward 0.000000, 0 accepted, 5 invalid evaluations. |
| HiCard — Latent Encoder | representation learning | Mean percentage reduction in one-hot baseline MSE; **higher is better** (`hf/tasks/hicard-latent-encoder/instruction.md`). | RUNNING at EXECUTE in the archived snapshot; 1 experiment, current reward 0.000000; no completed campaign result. |
| Budgeted CovType | constrained classification | Balanced accuracy over seven classes; **higher is better** (`hf/tasks/budgeted-covtype-dual-market-open/README.md`). | READY; no experiment or reward. |
| DCTabEval | tabular predictive modeling | Mean ROC-AUC across fixed evaluation splits; **higher is better** (`hf/tasks/dctabeval-aeac-pooled-cat-statistics/instruction.md`). | READY; no experiment or reward. |

The numeric values are official grader rewards, not raw metric values; higher reward is better. Seven campaigns are STOPPED with completed evaluations, one is still RUNNING in the captured state, and two are READY with no run. The archived `results/partial_summary.md` reports no Codex rerun. This archive cannot support paired agent claims, selector claims, significance claims, or generalization beyond the stated DEV/intermediate split. Model-token totals and provider dollar charges are not aggregated in the summary and remain unavailable here.
## Preregistered selector comparison protocol (v1)

The comparison unit is one frozen portfolio on one task under one exact source/evaluator/data revision. The two arms differ only in selector: deterministic selector versus opt-in LLM semantic selector. Freeze the candidate portfolio, candidate IDs and contents, eligibility rules, tie-breaking, selector/model/prompt versions, random seeds, environment, evaluator, scoring direction, and DEV input manifest before either arm runs. Store hashes for the manifest and all inputs.

For each paired replicate, give both selectors the same candidate set, order-independent IDs, number of selection opportunities, evaluator-call ceiling, wall-clock ceiling, and token/cost ceiling. Evaluate selected candidates using the same evaluator and DEV data. Randomize arm order per replicate and use at least five paired seeds per task before making inferential claims. Record every selection, fallback, invalid choice, timeout, error, evaluator result, elapsed time, model/token usage, and actual provider charge; missing or failed outcomes remain visible and are not silently dropped. Report paired per-task deltas and uncertainty; do not pool heterogeneous reward scales into one winner. A reproducible report must name the preregistered stopping rule and show deviations.

The target archive is a BananLoop-only partial run; it does not contain a matched Codex arm and cannot satisfy selector-comparison evidence. The separate `operator_roadmap_v1` suite is planned with no trial rows and compares agent/harness configurations, not selector treatment.

## Selector experiment status

| Item | Status |
|---|---|
| Frozen paired selector manifest and schema | `benchmarks/operator_roadmap_v1/suite_manifest.json` freezes a ten-task **agent/harness** suite, not a selector suite. A selector-specific preregistration and measured five-pair result exist at `benchmarks/selector_ab_v2/manifest.json`; the manual public-task portfolio is frozen and is not the historical smoke portfolio. v1 remains preserved as aborted. Subscription-only Codex use was authorized for up to one hour; tokens remain unavailable from Codex CLI. |
| Deterministic synthetic/dry runner and report | Implemented as `benchmarks/operator_roadmap_v1/report.py`; it validates and summarizes saved trials without launching agents or evaluators. Focused tests: `tests/test_benchmark_report.py`. |
| Real deterministic-vs-LLM selector measurements | One preregistered LLM slot (seed 53) failed after selection and before candidate evaluation because nested Codex could not write the disposable `matcher.py`. The slot is recorded; no other paired slots were run. |
| Existing ten-task agent comparison | The target archive has one BananLoop arm only. The separate agent/harness suite manifest is `planned_no_trials` and its JSONL trial file is empty. |

No model call was made for the inventory or report construction. The one-arm mini-matcher selector smoke in `docs/DEMO.md` used 3 agent calls and 2 evaluator calls; token usage and provider charge are unavailable. A paired selector comparison requires the frozen task/portfolio/replicate manifest and an approved resource plan. The preregistration fixes the mini-matcher task, a new manual public-task portfolio, 5 paired seeds, arm order, and call ceilings. It cannot run until the operator sets provider cost, token, and wall-clock caps; the new portfolio is not the historical smoke payload. Record costs even when provider charge is zero or unavailable.

### Offline report runner

Use `benchmarks/operator_roadmap_v1/suite_manifest.json` and `trials.jsonl` with `benchmarks/operator_roadmap_v1/report.py` for the existing paired agent/harness record. Each row records task, replicate, arm, reward, status, cost, tokens, duration and failure reason. The runner validates the frozen suite and reports complete/missing/failed trials and known versus unknown costs. This suite compares BananLoop with the Codex harness; it is not a selector ablation. The selector preregistration, offline ledger reporter, and scored v2 record are in `benchmarks/selector_ab_v2/`; v1 is retained separately with two failed seed-53 slots and is not reused. The reporter validates frozen hashes, five-pair arm order, resource authorization and measured rows without launching work. Tests: `tests/test_selector_report.py` and `tests/test_selector_preregistration.py` (10 passed). In v2, the user authorized Codex subscription quota only, with no separately billed API spend and a one-hour ceiling. The Docker run scored all 10 trial slots: 15 agent/Codex calls, 20 evaluator calls, 312.67 seconds total, $0 separately billed provider cost and unknown token usage. Four pairs tied at 1.0; one pair scored 0.4375 LLM versus 1.0 deterministic. Mean LLM-minus-deterministic DEV delta is -0.1125; there is no winner or generalization claim because this is one 16-case DEV fixture with no HOLDOUT.

````powershell
python -m benchmarks.operator_roadmap_v1.report `
  benchmarks/operator_roadmap_v1/suite_manifest.json `
  benchmarks/operator_roadmap_v1/trials.jsonl --output report.json
python -m pytest tests/test_benchmark_report.py -q
```

The report module is offline; it does not launch selectors, model providers, or evaluators. Its fixtures cover complete and missing pairs, failures and recorded/unknown costs. Selector-specific paired measurements remain open.

## Expansion status

Target `main/` contains a separately versioned public starter bundle at `hf/expansion/operator-roadmap-v1/` for three new project classes: revisioned ledger, temporal access policy, and industrial catalog matching. `expansion_manifest.json` records source bundle/version, task identities, metric definitions, public source hashes and hashes of every bundled file. `tests/test_expansion_bundle.py` checks inventory, hashes, identities, and exclusion of private/generated evaluation material. The source contained no license file; provenance is retained and the manifest marks the bundle as not cleared for external redistribution.

This is starter-data progress, not completion of Task 7.5: no private seeded DEV/HOLDOUT cases or oracle implementations were copied, and no Codex or BananLoop measurement was run. The source contains oracles/generators, but the benchmark warns that same-host paths do not protect private data. BananLoop command agents and evaluators currently run without OS-level filesystem isolation, so a hidden workspace path alone cannot guarantee oracle/HOLDOUT confidentiality. Safe use requires a separate OS identity, container, or VM with aggregate-only output. The source bundle also has no license file and is not cleared for external redistribution. All three remain starter-only; Task 7.5 stays open until usage rights and a secure evaluator boundary are established. The original ten AutoResearchExam v1.1 tasks remain unchanged.
## Evidence and limits

- Inventory source: `hf/README.md` and each task directory under `hf/tasks/`.
- Local run configuration/results: `benchmark_runs/autoresearchexam-v1.1/run_manifest.json` and `results/partial_summary.md`/`partial_summary.json`.
- Local run traces/state: task directories under `benchmark_runs/autoresearchexam-v1.1/`; `ARCHIVE_README.txt` describes the archive contents.
- The archived summary is a BananLoop-only intermediate-split run, not a paired or closed-holdout comparison. Missing task-level evidence and unsupported provenance remain missing evidence.


## Consolidated result status (2026-10-30)

| Evidence set | Configuration and repeatability | Observed results | Cost, errors, and remaining limits |
|---|---|---|---|
| Existing AutoResearchExam v1.1 target run | BananLoop only; `gpt-6-luna`; medium reasoning; six hypothesis rounds; intermediate split, seed 40. The run manifest set max agent/evaluator calls and deadline to 2,147,483,647 and token budget to null. | Seven tasks STOPPED with the current rewards listed above; HiCard is RUNNING at EXECUTE; CovType and DCTabEval are READY. No Codex arm. | Token use/provider charges are not aggregated in the archived summary. The large ceilings are not a bounded paid comparison. This is intermediate DEV evidence only, with no paired/inferential or sealed HOLDOUT conclusion. |
| Planned agent/harness paired suite | `operator_roadmap_v1/suite_manifest.json`; 10 tasks, 3 repeats, baseline Codex and deterministic BananLoop, common budget and hashes. | `protocol_state=planned_no_trials`; `trials.jsonl` has 0 bytes. The offline report currently shows 0/30 completed pairs and 60 missing arm-trial slots. | No trial cost or errors were observed. Unit tests exercise report logic only; there is no benchmark result. This is not a selector comparison. |
| Project-class expansion | Three public starter classes and hash manifest exist locally at `hf/expansion/operator-roadmap-v1/`; this bundle is excluded from public redistribution because its source has no license file. No private oracle or seeded DEV/HOLDOUT bundle is present. | No additional-task measurements. | Task 7.5 remains open: source gold is not safely usable without separate OS/container isolation; no license file was found, so external redistribution is not cleared. |
- [x] **Task 7.4 — Сравнить deterministic и LLM selector.** Файлы: `benchmarks/selector_ab_v2/`, `docs/benchmarks/operator-roadmap.md`. DoD закрыт для этой preregistered mini-fixture: пять paired DEV-реплик, общий byte-frozen портфель и одинаковые campaign budgets; валидатор проверил все хэши/порядок arm/статусы. v1 сохранён как aborted: два seed 53 failure до измерения кандидата, ни один не повторялся. После исправления binary patch transport и symlink/reparse destination checks выполнен v2 в Docker Desktop Linux (Debian 12, Python 3.11.2, Codex CLI 0.165.0, pinned image `sha256:7fa488be06863911b277b9370cb21547381ab65c9f4a47a01197cdb8e6ed74de`); auth был readonly, контейнер non-privileged, seccomp unconfined только для эфемерного контейнера из-за bubblewrap. 10/10 измеренных слотов, 5/5 пар, 15 agent/Codex calls, 20 evaluator calls, 312.67 с суммарного trial wall time, $0 отдельно тарифицируемого API, token use недоступен; одна модельная пара дала 0.4375 vs 1.0, остальные четыре — 1.0 vs 1.0; descriptive mean LLM−deterministic = −0.1125. `winner_claim=null`: это только 16-case DEV mini-fixture без HOLDOUT, вывод не обобщается. Доказательства: `benchmarks/selector_ab_v2/manifest.json`, `trials.jsonl`, `report.json`, per-trial `state/campaign.sqlite3`, `campaign-report.md`, `execution.json`; Docker build прогнал 13 process/workspace tests (2 Windows symlink tests skipped ранее с WinError 1314), и v2 offline report в статусе `complete`.

Task 7.4 has five completed paired measurements in `benchmarks/selector_ab_v2/report.json`. The report is descriptive DEV evidence only: four pairs tied at 1.0, and one favored deterministic by 0.5625. No HOLDOUT exists in this fixture, so there is no selector winner or generalization claim. v1 failures and v2 measurements remain separate. The offline report runner validates saved observations and does not launch work.






Offline selector report (does not launch model or evaluator work):

```powershell
python -m benchmarks.selector_ab_v2.report --root . --manifest benchmarks/selector_ab_v2/manifest.json --portfolio benchmarks/selector_ab_v2/frozen_portfolio.json --trials benchmarks/selector_ab_v2/trials.jsonl --output benchmarks/selector_ab_v2/report.json
python -m pytest tests/test_selector_report.py tests/test_selector_preregistration.py -q
```
