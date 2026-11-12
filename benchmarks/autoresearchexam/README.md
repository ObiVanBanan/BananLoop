# AutoResearchExam v1.1 integration

This directory pins a ten-task shortlist and contains a partial Harbor bridge.
It does not yet provide a complete BananLoop campaign or a BananLoop score on
AutoResearchExam.

## Verify the pinned shortlist

```bash
python benchmarks/autoresearchexam/verify_task_selection.py \
  --source /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam
```

The selection manifest pins task and instruction hashes for source revision
`23c0d92e11af1dea821f972b5f0a739060958002`; all ten tasks are version `1.1.0`.

## Harbor preflight

Harbor `0.25.0` is installed at
`/home/sonik/bananloop-benchmark-env/harbor-venv/bin/harbor`. A Docker dry run of
`budgeted-imputation-mcar50` succeeded with `--agent nop`; it resolved one trial
without building a container, invoking a model, or contacting a grader. This only
confirms Harbor recognizes the task configuration.

## BananLoop execution status

No AutoResearchExam task has been run or scored by BananLoop from this copy. A
Harbor `BaseAgent`, public-grader adapter, durable one-hour ledger, and isolated
public DEV runner now exist, but the durable outer five-child campaign driver is
not implemented.
A verifier-disabled, no-model probe discovered the
public MCP tools `submit`, `result`, and `status`; the observed schemas and
fingerprint are in
[public-mcp-contract-budgeted-imputation-mcar50.json](public-mcp-contract-budgeted-imputation-mcar50.json).
The probes called MCP initialization, `tools/list`, and the read-only `status`
tool. Then one separate verifier-disabled probe submitted the untouched task starter
once and polled `result` until terminal. Its public score was `0.0`; this is a
protocol baseline, not a BananLoop result. The sanitized record is in
[public-baseline-probe-budgeted-imputation-mcar50-2026-11-12.json](public-baseline-probe-budgeted-imputation-mcar50-2026-11-12.json).
The terminal response shape is now observed, while queued/grading response shapes
remain to be validated. Do not assume REST endpoints based on the MCP URL.

The public starter imputer was also run through the complete 42-cell DEV panel in
a pinned task image with `--network none` and a read-only root. It scored R²
`-0.0017`; this is only a public DEV baseline. The record is in
[public-devkit-smoke-2026-11-12.json](public-devkit-smoke-2026-11-12.json). The
runner passes only the aggregate to BananLoop and refuses an image ID that differs
from the approved protocol pin.

The current implementation gap and safety boundary are recorded in
[HARBOR_ADAPTER.md](HARBOR_ADAPTER.md). Do not replace the adapter with a direct-
writing Harbor agent and report it as a BananLoop result. The task shortlist is in
[task-selection.json](task-selection.json).

The `harbor_agent.py` adapter currently handles only the imputation task and one
pre-created Optimization child. The current DEV evaluator runs against the public
task image in a separate local networkless Docker container; it does not include
the grader service. `HARBOR_ADAPTER.md` tracks the remaining integration work.
The no-model Harbor lifecycle probe reached BananLoop and stopped on a
synthetic missing campaign, but Harbor's task cleanup still collected its
declared `/var/lib/are` grader artifact with verification disabled. That generated
copy was quarantined without inspection. Do not run the original task directory
again until an overlay removes its `artifacts` declaration and `verifier.collect`
hook. `prepare_safe_harbor_task.py` now builds and hashes safe overlays for all
ten selected tasks; the index and no-model preflight evidence are in
`HARBOR_ADAPTER.md`. The adapter still lacks the durable five-child campaign
driver and independently approved evaluator protocol, so it has not produced an
AutoResearchExam BananLoop score.

The imputation task now has a public-only `DEV` + `VALIDATION` fixture builder:
`uv run python -m benchmarks.autoresearchexam.build_public_fixture`. Its frozen
bundle includes the exact host DEV runner, public task instructions, `cellspec.py`,
`cellrunner.py`, MCP contract, Optimization backend, public grading ledger, and
task-container MCP client for independent review. It does not read the grader
sidecar or private tests; see
[`HARBOR_ADAPTER.md`](HARBOR_ADAPTER.md) for the reproducible command.

The consolidated status, benchmark traces, validation counts, and remaining gaps
are recorded in [`../pilot-status-2026-11-12.json`](../pilot-status-2026-11-12.json).
