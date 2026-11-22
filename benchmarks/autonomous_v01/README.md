# Autonomous benchmark v0.1 offline preparation

This package is the offline preparation path for the 12-task AutoResearchExam
paired comparison. It does not start research agents, score through Harbor, or
invoke an AI Judge. A separate owner approval over a frozen launch manifest is
required before any live series.

The time policy is **3600 wall-clock seconds per arm**. Exploration, verification,
the Optimization child, and grader waits share that deadline. Model-turn and
token usage are recorded where the CLI reports them; they are not call caps and
do not prove provider-request counts. No paid API or fallback is permitted.

## Offline tools

Build the owner-curated catalogue from the exact pinned upstream checkout:

```sh
python -m benchmarks.autonomous_v01.task_catalogue \
  --source /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam
```

Regenerate a read-only local preflight report (it also inspects Docker image IDs,
but does not start a container):

```sh
python -m benchmarks.autonomous_v01.runner preflight \
  --out benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-YYYY-MM-DD.json
```

The fake-series harness accepts only an injected backend. Its tests create 24
separate arm roots and produce a hash-indexed archive without models, Harbor,
evaluators, or AI Judge calls. The archiver refuses incomplete/duplicate arms,
symlinks, credential-like paths, and common API-key/Bearer-token patterns.
`CodexDirectBackend` can run a persistent Codex CLI task and snapshot its final
task output directory as-is, including missing or invalid output status. A
complete series launcher and BananLoop Full backend with the frozen auto-pivot
policy are not yet implemented.

## Current status: `PREP_BLOCKED`

The catalogue pins 12 distinct official tasks at AutoResearchExam source revision
`23c0d92e11af1dea821f972b5f0a739060958002`. Nine task images have local image IDs
that match their existing pins. Three new CPU tasks now have public DEV scorer
code, but still need trusted DEV/VALIDATION fixtures, task-specific Harbor agent
adapters, and content-pinned DEV images. Do not describe those tasks as runnable
until the preflight reports all of those pins ready.

The host exposes 12 logical CPUs and about 32 GiB RAM. With 8 CPUs and 14 GiB
requested per task, measured capacity is one arm at a time; the series therefore
needs at least 24 arm-hours, plus preparation and final evaluation. No 10-hour
completion estimate is supported.

The owner accepted continued use of the three affected tasks for an internal,
non-RPDI-14 diagnostic, provided source exposure is disclosed. The record is
[`source-access-declaration.json`](source-access-declaration.json); do not claim
blind or independent holdout evaluation for those tasks. The latest evidence is in
[`OFFLINE-PREFLIGHT-2026-11-15.json`](OFFLINE-PREFLIGHT-2026-11-15.json).
During offline contract research, local `are-grader-ctx` source snippets for the
three new tasks were inspected accidentally. No candidate, evaluator, Harbor
grader, or model was run. This disposition does not restore RPDI-14 independence.
RPDI-14 is unchanged. Board Games remains a separately approved secondary
experiment. See the [plan](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-PLAN.md)
and [updated decisions](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-DECISIONS-2026-11-15.md).
