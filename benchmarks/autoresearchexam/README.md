# AutoResearchExam v1.1 integration

This directory pins a ten-task shortlist and contains a partial Harbor bridge.
It does not yet provide a complete BananLoop campaign or a BananLoop score on
AutoResearchExam.

The public output/API and DEV-harness requirements for all ten selected tasks are inventoried in [TASK-ADAPTER-INVENTORY-2026-11-12.md](TASK-ADAPTER-INVENTORY-2026-11-12.md).

`series_runner.py` is a durable dispatcher for already-prepared per-task Harbor
job configs. It validates the selected task/version and frozen config and
overlay digests, limits each Harbor process to one hour, and records uncertain
outcomes without replaying them. It does not create or approve campaigns or
evaluator protocols, supply task-specific candidate/evaluator adapters, or make
the ten-task benchmark runnable by itself.

## Dispatching prepared jobs

Before dispatch, prepare exactly one descriptor JSON per selected task in
`<job-config-dir>/<task_id>.json`. Each descriptor must use schema
`bananloop-are-prepared-harbor-job-v1` and include `task_id`, `task_version`,
`task_instruction_sha256`, `overlay_path`, `overlay_task_sha256`,
`bananloop_config_path`, `bananloop_config_sha256`, `harbor_config_path`, and
`harbor_config_sha256`. The two referenced JSON configs and the overlay
`task.toml` must match those digests. The BananLoop config must pin the same task
and version, `max_candidate_iterations: 5`, and `time_limit_seconds: 3600`.
The Harbor config must run that safe overlay through the BananLoop ARE agent,
with one attempt and retries disabled. The dispatcher supplies
`--disable-verification` itself.

Once those independently reviewed configs exist, dispatch (or resume) them with:

```bash
python -m benchmarks.autoresearchexam.series_runner \
  --overlay-index /path/to/safe-overlays/index.json \
  --job-config-dir /path/to/prepared-job-configs \
  --series-dir /path/to/run-state/are-series-001 \
  --run-id are-series-001 \
  --harbor /path/to/harbor
```

The runner validates all ten task/version/config/overlay pins before starting.
It writes `series.json` atomically, continues to later tasks after a task
failure, and never replays a task left `RUNNING`, timed out, or failed. No
prepared ten-task job-config directory currently exists, so this command is a
usage contract and is not yet a runnable benchmark command.

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

One approved Optimization child was run through the safe Harbor overlay on
2026-11-12. It made one GPT-6 Luna call, created one candidate, and completed
two repeatability runs against the public DEV panel. The candidate scored
R² `-0.7818`, below the starter DEV baseline `-0.0017`; this is a negative
single-candidate result, not an AutoResearchExam task score. The run stopped
before public VALIDATION and Integration Verifier completed. Its durable record
and logs are archived at
`/home/sonik/bananloop-benchmark-env/runs/are-iterative-ridge-child/`.

A separate resume reached a public MCP submit attempt but the task-container
request file was unreadable because Harbor materialized the host temporary file
with mode `0600` under a different UID. The public ledger conservatively
records `SUBMISSION_UNKNOWN` with no ID; no automatic retry is allowed, and the
old task container has been removed. The bridge now creates request files from
inside the task as its agent user, still with mode `0600`. A read-only `status`
probe passed after this fix with zero submissions and zero active jobs. The
probe did not modify or reconcile the old submission ledger.

A Harbor `BaseAgent`, ten task-pinned public-grader/evaluator adapters, a
durable one-hour ledger, and isolated public DEV runners exist. The frozen
Optimization runtime can execute up to five candidate iterations inside one
child campaign. `series_runner.py` still dispatches only prepared Harbor jobs;
automated campaign/config preparation and per-task protocol approval remain
unfinished.
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

As of 2026-11-13, `harbor_agent.py` has task-specific dispatch for all ten of
the selected tasks: imputation, SOPCC, CARPS, DCTAB, shortest-CI, sketched
Newton, label-risk, CPU decoder, CPU LLM, and a CovType external proxy. The
CovType adapter is diagnostic and does not implement the official dynamic
market protocol. Each adapter has task-specific public
DEV/VALIDATION fixtures, a public-only MCP contract, and a passing starter
baseline smoke. These are evaluator preflights, not BananLoop optimization
results. `HARBOR_ADAPTER.md` and
[`TASK-ADAPTER-INVENTORY-2026-11-12.md`](TASK-ADAPTER-INVENTORY-2026-11-12.md)
track the integration details and remaining gaps.

The no-model Harbor lifecycle probe against the original task directory once
collected its declared `/var/lib/are` grader artifact with verification
disabled. That generated copy was quarantined without inspection. Safe overlays
now remove both the task-level artifact declaration and `verifier.collect`
hook; run probes only against those overlays.

The imputation task now has a public-only `DEV` + `VALIDATION` fixture builder:
`uv run python -m benchmarks.autoresearchexam.build_public_fixture`. Its frozen
bundle includes the exact host DEV runner, public task instructions, `cellspec.py`,
`cellrunner.py`, MCP contract, Optimization backend, public grading ledger, and
task-container MCP client for independent review. It does not read the grader
sidecar or private tests; see
[`HARBOR_ADAPTER.md`](HARBOR_ADAPTER.md) for the reproducible command.

The first digest-pinned public imputation DEV/VALIDATION protocol was approved
in campaign `c-5ed94ff2d7dc592556dede3c68d4c0da` (fixture SHA-256
`582b2d30516fbefdcafa4d3fc037464b7fd41b62a333a26d6706c4b104d77eb4`). Its
owner-approved `iterative_ridge` child made one GPT-6 Luna call and two DEV
evaluations. The candidate R² was `-0.7818` versus the starter DEV baseline
`-0.0017`, so it was a negative experiment, not a task score. A subsequent
public VALIDATION submit attempt has durable state `SUBMISSION_UNKNOWN` with no
ID. No retry was made; the at-most-once ledger prevents safely resubmitting it.
The child report remains `STEP_LIMIT` with Integration Verifier pending. Its
logs and SQLite records are archived at
`/home/sonik/bananloop-benchmark-env/runs/are-iterative-ridge-child/`.

No ten-task AutoResearchExam job configs or series score exist yet. The current
`series_runner.py` dispatches only prepared jobs; it does not create campaigns
or configs. Each prepared task must point to a frozen Optimization child whose
campaign budget, five-candidate cap, and independent evaluator approval match
the Harbor limits; the bridge now checks this binding before any live work.

The consolidated status, benchmark traces, validation counts, and remaining gaps
are recorded in [`../pilot-status-2026-11-12.json`](../pilot-status-2026-11-12.json).

## CovType external public diagnostic

CovType still has no task-provided labelled public DEV panel. A separate
diagnostic panel is now reproducibly generated from the public UCI CoverType
archive, excluding the exact 250,000 source rows selected by the task's public
data-preparation script. It uses 30,000 external labelled training rows and
10,000 external evaluation rows, with the task's public feature/class
anonymization transform. Its simplified market uses static prices because the
official dynamic pricing formula is not documented publicly. The result is a
proxy diagnostic only; it must not be reported as an AutoResearchExam score.

Build the local panel with:

```bash
python -m benchmarks.autoresearchexam.build_covtype_proxy \
  --archive /home/sonik/bananloop-benchmark-env/sources/covertype-public-uci.zip \
  --output /home/sonik/bananloop-benchmark-env/runs/covtype-external-public-proxy-2026-11-13
```

The archive is SHA-256 pinned in the builder. The generated manifest and
arrays are in the output directory above. The CovType Harbor adapter and
trusted evaluator runner are implemented, and the public-only fixture was
built and its text-only backend bundle verified. Public task and official
Terminus documentation are pinned in
`public-evaluator-attestation-covtype-v1.json` for the public-validation trust
boundary. The starter policy scored balanced
accuracy `0.46194230556880844` on the 10,000-row panel in 32.73 seconds, with
2,000 acquired labels. This is an evaluator smoke, not a BananLoop campaign or
official AutoResearchExam score. The static-price proxy still needs independent
protocol review before a campaign can use it.
