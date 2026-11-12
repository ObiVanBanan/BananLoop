# AutoResearchExam task adapter inventory — 2026-11-12

Scope: public task instructions, `README.md`, `task.toml`, and public task environment/devkit only. No private tests, `are-grader-ctx`, or grader artifacts were inspected. SOPCC public DEV baseline smoke was run without a model or grader call; scope and limits are recorded in `results/sopcc-public-dev-baseline-smoke-2026-11-12.json`.

All ten selected tasks declare 8 CPUs, 14,336 MB RAM, 153,600 MB storage, no GPU, and a 24-hour task timeout. The pilot's one-hour/five-iteration limit is a shortened local experiment, not the official benchmark budget.

| Selected task | Required output and API | Public DEV material | Adapter gap from current imputation bridge |
|---|---|---|---|
| `budgeted-covtype-dual-market-open` | `/app/output/policy.py`; `Policy(data_dir)` with `select_queries(...)`, `select_next(...)`, `predict(...)` | Public unlabeled pool and metadata; no public DEV script identified | Active label acquisition and classification policy; different protocol and metric |
| `budgeted-imputation-mcar50` | `/app/output/impute.py`; `impute(X_train, X_eval, seed) -> ndarray` | Six-dataset NPZ panel, manifest, `cellspec.py`, `cellrunner.py`, `devkit.py` | Current supported task |
| `carps-star-discrepancy-subset-select` | `/app/output/select_subset.py`; `select(points, k, seed, time_budget_s) -> indices` | Public example point arrays and manifest; no public evaluation harness identified | Subset selection and star discrepancy metric |
| `cpu-decoder-graph-executor` | `/app/output/executor.py`; `build_executor(graph_spec, weights, n_workers) -> callable` | Public reference executor, graph spec, harness, benchmark, worker | Task-specific equivalence and performance evaluator |
| `cpu-llm-decode-throughput` | `/app/output/engine.py`; `Engine(model_dir)` and `generate(requests)` | Public workloads, reference engine, harness, benchmark runner | Model-directory and generation-throughput protocol |
| `dctabeval-aeac-pooled-cat-statistics` | `/app/output/solution.py`; `fit_predict(train_X, train_y, pool_X, eval_X, seed) -> ndarray` | Public `local_panel.py` and `dev.csv`; no explicit public DEV runner identified | Pooled-learning protocol and ROC-AUC metric |
| `label-efficient-risk-estimator` | `/app/output/estimator.py`; `Estimator(pool, budget, rng)` with `next_index`, `observe`, `estimate` | Twelve public labelled pools and metadata; no DEV runner identified | Stateful label-query protocol and risk metric |
| `shortest-valid-ci-l2-ece` | `/app/output/interval.py`; `ece2_interval(Z, Y, k, alpha, rng) -> (lo, hi)` | Public 24-setting `dev_panel.json`; no DEV runner identified | Coverage and interval-length metric |
| `sketched-newton-cov-estimator` | `/app/output/estimator.py`; `estimate(stream, config) -> ndarray` | Public runner and two DEV instances with JSON and exact arrays | One-pass covariance estimator and resource protocol |
| `sopcc-online-chance-constrained-policy` | `/app/solution/policy.py`; `Policy(seed)`, `prepare(instance)`, `act(obs)` | Public `dev_eval.py`, runner, problem module, baseline; pinned no-network Docker runner returns aggregate only; smoke: metric 3.3136, 4 episodes, 0 failures | SOPCC evaluator backend and public-only fixture builder now exist; task MCP contract still must be observed, and Harbor agent/config remains imputation-specific |

## Required next implementation

Each task needs an explicit owner-pinned adapter descriptor: repository candidate path, Harbor output path(s), allowed visible/write scope, public DEV entrypoint and data, metric schema/direction, repeatability policy, and task-specific resource/call limits. Core must bind the descriptor and its source digests into an independently reviewed evaluator protocol. The current imputation-only runner cannot safely infer these fields from an output filename.

The current bridge is useful only for `budgeted-imputation-mcar50`. The ten-task dispatcher is deliberately limited to already-prepared and approved per-task Harbor configs; no such ten-config directory exists yet. It does not generate or approve protocols, choose Supervised pivots, or make the AutoResearchExam ten-task live series runnable by itself.
