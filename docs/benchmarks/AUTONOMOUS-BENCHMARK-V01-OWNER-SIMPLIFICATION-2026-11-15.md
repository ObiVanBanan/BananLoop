# Autonomous Benchmark v0.1 — owner simplification, 2026-11-15

**Status: preparation only. No authorization to spend subscription calls or start the real 24-arm run.**
This document supersedes the more elaborate requirements for **mandatory Codex best-verified checkpoints**, while preserving the internal/non-RPDI-14 classification. Old offline evidence remains historical and must not be edited retroactively.

## Final benchmark contract

- **Primary:** 12 pinned AutoResearchExam v1.1 tasks, one `bananloop_full` arm and one iterative `codex_direct` arm each.
- Each arm has **3600 seconds wall-clock research time**, measured by a monotonic host clock from arm start. No synthetic limit on model turns, optimizer iterations or calls. No separately billed API/fallback. Record observed usage/tokens/retries/unknown statuses.
- Codex Direct receives the task, a clean task-specific workspace, allowed public DEV tooling and a working Codex CLI, then works autonomously. **No requirement to archive or choose its best intermediate verified candidate.** At deadline, stop the full process group, snapshot the final deliverable bytes at the required `/app/output` or `/app/solution` path, and SHA-256 hash the files. If the output is incomplete, invalid or missing, record that honestly; no repair after timeout.
- BananLoop Full receives the **same goal, public DEV evaluation access, model, environment and one 3600-second parent-to-child time budget**. Frozen in-scope Exploration -> Optimization pivot is preauthorized for this internal series; any out-of-scope or unverifiable proposal must block. Ordinary interactive HIL remains unchanged. At deadline snapshot its selected champion/final deliverable as BananLoop would normally produce it. Do not graft a new post-hoc optimizer onto either arm.
- During an arm, BananLoop's trusted fixture contains the public DEV tier only. VALIDATION and HOLDOUT are not exposed to either optimizer; the same pinned public DEV scorer grades both final artifacts after all arms are terminal.
- **Fairness rule:** compare *final submitted artifacts* from both arms on the same task. A BananLoop champion vs a Codex final working tree is a comparison of each system's native end-to-end workflow, not a comparison of identical checkpoint-selection algorithms. Explicitly report this workflow difference. Both arms have the same deadline and public feedback during research.
- Both arm outputs must be frozen before the **same post-run evaluator** is invoked. No agent or intermediate LLM Judge receives the final grade or the other arm's files. A genuine independent/private holdout is NOT claimed merely because graders are present. For three tasks with recorded `are-grader-ctx` operator exposure, disclose the limitation in every score report.
- One host with 12 logical CPUs / ~32 GiB RAM can conservatively run one arm at a time for 8-CPU/14-GiB tasks. **24 one-hour arms = up to ~24 hours sequential research wall time**, plus setup and final scoring. Do not promise 10 hours.
- All 24 terminal results, including failures, are archived with exact source SHA, input task/adapter SHA, final delivered source, scores, observed usage, timings and error logs. Owner will upload archive to Google Drive; one retrospective ChatGPT/Astra analysis only after the series.

## Engineering work, in order

### (1) Simple robust deadline runner
- Use an outer supervisor enforcing one monotonic 3600s clock per arm, and kill process tree and any task-owned containers at timeout. Do not let setup, HIL waiting or child pivot start a new hour. Record timeout phase and partial artifacts.
- Retain a narrow task-approved benchmark-bound auto-pivot policy so the BananLoop Full arm can actually start Optimization without waiting for owner input. This is a functional execution requirement, not a new RPDI-14 approval gate.
- Exercise both real backends with **fake CLI/model and fake evaluator** (no subscription usage), including timeout during pivot and after a partial output write.

### (2) Codex final-as-is output
- Replace obsolete `codex_best_verified_checkpoint_integration` preflight requirement with `codex_final_artifact_export`.
- At completion or timeout, collect only the exact required deliverable path(s), check path safety and file sizes, SHA-256 hash and archive them, plus JSONL/stdout/stderr and status. Do not require Codex to use `CandidateArchive`. If no valid final output exists, preserve failure and do not retry.
- Apply same final-evaluation rubric to the BananLoop final deliverable. DEV scoring during the hour remains available but final benchmark score is produced only after both outputs are frozen.

### (3) Prepare the three missing CPU tasks completely

Pinned upstream `bespokelabsai/AutoResearchExam@23c0d92e11af1dea821f972b5f0a739060958002`; only public task/instruction/dev/environment assets may be used:

| Task | Output entrypoint | DEV metric |
| --- | --- | --- |
| `act-tensor-sparse-panel-imputation-r2` | `/app/solution/impute.py:impute(panel, seed)` | imputation R2, maximize |
| `causalpfn-cate-pehe-ihdp-surfaceb` | `/app/output/estimator.py:estimate_cate(X_train,t_train,y_train,X_test)` | PEHE, minimize |
| `hicard-latent-encoder` | `/app/output/encoder.py:Encoder.fit/transform` | relative one-hot MSE improvement %, maximize |

Current code exists in `benchmarks/autonomous_v01/public_task_dev.py` (DEV scoring), `public_task_runner.py` (networkless Docker execution), `task_catalogue.py` (pins), and `benchmarks/autoresearchexam/prepare_safe_harbor_task.py` (safe overlay). Complete rather than duplicate them.

For EACH task:
1. Build public DEV image **on the owner's host** from pinned upstream Dockerfile/environment. Confirm `.dockerignore` excludes `are-grader-ctx`; do not read, copy or mount private grader assets into agent workspace. Pin the actual `docker image inspect` SHA-256 image ID (cannot be determined from GitHub alone). Keep raw image ID evidence outside repo before catalogue regeneration.
2. Generate safe Harbor overlay and task-specific immutable DEV fixture/protocol using only publicly specified I/O and scoring. Freeze source/instruction/overlay/image SHA, public MCP contract, candidate mapping and evaluator backend ID.
3. Add corresponding trusted evaluator runner(s) and three adapter IDs to `benchmarks/autoresearchexam/harbor_agent.py` with allowlisted task/output mappings and a valid frozen configuration preparer. Follow proven existing task patterns; **do not silently use a proxy metric as an official score**.
4. Exercise baseline, invalid output, numeric direction, input integrity, networkless image isolation, no private collector hooks, timeout and replay behaviour via **offline scoring only**, with zero model calls and no private grade submission.
5. Update `task-catalogue.json`, preflight integration states, image IDs, adapter fixture hashes and tests only after these checks pass. Preserve the existing source-access disclosure; it is accepted for an internal diagnostic, not an independent holdout.

## Exit criteria for PREP_BLOCKED -> READY_NON_RPDI_DIAGNOSTIC

- Twelve tasks all have actual safe overlays, real public DEV scorer + trusted fixture/protocol + task-specific Harbor adapters and pinned local images.
- Codex real iterative backend and BananLoop Full backend can both run under one 3600s supervisor, with a functional in-scope auto-pivot and verified final-as-is artifact export. No mandatory Codex checkpoint scoring.
- Offline fake 24-arm series covers timeouts, invalid/missing final artifacts, clean task isolation, recorded usage UNKNOWN and complete SHA-256 archive.
- Run manifest, Exact HEAD SHA and current task image IDs frozen. PR remains draft until all offline checks pass.
- **No real model launches, no Harbor public submissions and no AI Judge** as part of this preparation. The owner will separately authorize one live command after a READY preflight.

## Agent instruction
Finish (1)–(3) above in PR #146 instead of adding more administrative review gates. Keep focus on code that can execute the benchmark. Report exact commit, tests, new preflight and any remaining *technical* blockers.
