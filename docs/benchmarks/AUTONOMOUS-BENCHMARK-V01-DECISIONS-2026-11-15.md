# Autonomous Benchmark v0.1 — decisions 2026-11-15

Status: OFFLINE PREPARATION; no model, Harbor grader or AI Judge calls authorized by this document. RPDI-14 unchanged. This internal comparative diagnostic is not an independent sealed benchmark.

## Decision 1: three source-verified CPU ARE tasks

Official upstream: https://github.com/bespokelabsai/AutoResearchExam at exact source commit `23c0d92e11af1dea821f972b5f0a739060958002`.

| Task ID | Deliverable | Development evidence | Metric |
| --- | --- | --- | --- |
| `act-tensor-sparse-panel-imputation-r2` | `/app/solution/impute.py` :: `impute(panel,seed)` | 8 NPZ development panels with withheld values | Out-of-sample imputation R², maximize |
| `causalpfn-cate-pehe-ihdp-surfaceb` | `/app/output/estimator.py` :: `estimate_cate(...)` | 100 NPZ realization DEV cases with `tau_test` | PEHE, minimize |
| `hicard-latent-encoder` | `/app/output/encoder.py` :: `Encoder.fit/transform` | 24 NPZ DEV instances with one-hot reference MSE | Percent relative MSE gain, maximize |

All three have task.toml version 1.1.0, 8 CPUs, 14,336 MB RAM, 0 GPUs, 153,600 MB declared storage, and public grading MCP contract. Their existence is VERIFIED from the pinned upstream, but the current BananLoop branch has NO adapters for them. They are CANDIDATES, not READY tasks.

Do next: pin task.toml + instruction.md SHA-256 and source rights; build exact DEV scoring, output adapter, public evaluator contracts, trusted receipts, fixture, safe Harbor overlay (no private artifact collection), and tests. Verify seeded DEV, correct metric direction, bad-output handling, and an unchanged baseline. If a task fails feasibility, alternate CPU-only official task `activeprune-al-unlabeled-pool-pruning` is source-verified, but a replacement must happen BEFORE final freeze. Existing 9 real-adapter tasks + 3 new tasks = 12 curated tasks, not a random sample. Randomize only the two arms' run order unless a larger eligible catalogue is built.

## Decision 2: TIME-ONLY, no 250-call gate

Owner changed policy: **3600 seconds of research wall clock per arm**, 12 tasks × BananLoop Full and Codex Direct = 24 independent research sessions. The same clock covers Exploration, verifier, Optimization child and public grading waits, and does NOT restart for a child. No synthetic per-arm iterations/model turns or 250-call hard ceiling. The historical 250 figure is a planning estimate only and should not appear as a claim of enforced subscription use. Keep durable OBSERVATIONAL usage: CLI-observed model turns, token totals, retries, errors, unknowns, per-arm and overall tallies. No paid API, paid fallback, account quota bypass or automatic change of model. Throttled/unavailable ChatGPT subscriptions must be marked as failures or interrupted time, with wall-clock time still counted.

Codex Direct must be a persistent *iterative* coding/research process with repository edits, tests and DEV feedback, not the old no-tools one-shot role diagnostic. On the deadline, freeze the best VERIFIED candidate with source SHA and receipt (or NO_VERIFIED_CANDIDATE). Final private scoring may run AFTER the arm freezes but results must not reach either agent for adaptation. The old `runner.py` code still enforces `MAX_CALLS=250` and `PER_ARM_CALLS=10`: it must be refactored, and preflight and tests regenerated rather than quietly edited.

## Decision 3: one-time series-bound pivot authorization

In ordinary BananLoop the Exploration→Optimization handoff pauses in `PAUSED_NEEDS_INPUT` awaiting a user choice of a concrete Optimization pivot. This is not a need to approve 12 tasks individually. Implement explicit BENCHMARK-ONLY preregistered authorization bound to the immutable experiment manifest, exact task/output scope, evaluator protocol, allowed resource permissions and inherited deadline. A proposal inside this boundary may be authorized automatically and logged with its source/receipt; outside scope, changed protocol, unknown verification or broadened permissions MUST block. Never silently disable independent verification or remove the normal HIL policy from ordinary campaigns. The experiment must disclose this difference from normal interactive Full.

Final owner permission to spend subscription usage is a SEPARATE confirmation over the exact frozen manifest/launch. This document approves preparing code, not starting the runs.

## Remaining engineering gates

- Real task fixtures/overlays/adapters for 3 newly found tasks; regenerate selection/preflight with 12 valid tasks.
- Iterative Codex Direct arm, full BananLoop arm, a truly inherited 3600s deadline, best-verified-checkpoint selector and offline fake-24-run tests.
- Update old 250-call ledger from ENFORCEMENT to OBSERVABILITY; never claim hard per-turn Codex provider quotas.
- Preauthorized pivots scoped to frozen series without bypassing verifier decisions.
- Practical concurrency: current host has 12 CPU, 32 GiB RAM; each task requests 8 CPU/14 GiB, possibly plus grader CPU. Do not promise 10h for 24 arm-hours until resource probes demonstrate parallel execution.
- No intermediate AI Judge. Package both arms' artifacts after all tasks are terminal; owner will upload archives to Google Drive for a single retrospective review. Board Games is separately approved, 10 × 2 arms at 600s per arm, not included here.
