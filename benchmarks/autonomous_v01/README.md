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
task output directory as-is, including missing or invalid output status.
`BananLoopFullBackend` links Exploration, frozen auto-pivot authorization,
inherited grader deadline checks, and Harbor Optimization. The complete
production series launcher is tracked in BL-043.

## Prepare an offline launch manifest

`prepare` freezes the 12 tasks, deterministic arm order, the model and CLI
pins, and the Full feature flags. It creates a new file of kind
`ARE_PREPARATION_ONLY` with `launch_authorized=false`.

```sh
python -m benchmarks.autonomous_v01.runner prepare --out /tmp/are-plan.json \
  --seed 7 --model gpt-6-luna --reasoning medium --codex-version 'codex-cli 0.171.0'
```

A proposed full candidate Git SHA can be recorded with
`--candidate-source-sha`. Its availability and the actual runtime pins are
launch requirements. Prepare uses the supplied CLI version without probing
Codex, Docker, or Harbor. An existing manifest path is refused.

## Offline 24-arm CLI

On POSIX, run 24 synthetic arms in separate workspaces with the same parent
watchdog used by the command-arm integration tests.

```sh
python -m benchmarks.autonomous_v01.runner dry-run --out /tmp/are-dry-run
```

Use a new output directory. The catalogue digest and arm order are frozen
before dispatch. The command archives final worker output, start and terminal
records, and the usage summary. The source event ledger is
`observed-usage.sqlite3` under the series directory. No model, Harbor, or
scorer runs in this mode.

After interrupting an unarchived dry run, pass `--resume` to continue with
the same catalogue and output directory. Saved terminal results are kept.
An arm directory without a terminal record is archived as
`INTERRUPTED_UNKNOWN`. Only arms without a start directory get a new worker.
A completed archive is published atomically and the CLI refuses to reopen it.

[Verified 24-arm index](fake-24-arm-e2e-index.json) lists the files and
SHA-256 values from the offline CLI verification.

## Preflight and CI · 2026-11-22

The offline preflight returned `PREP_BLOCKED` for PR #146 HEAD `be24a260`
and for the earlier `e0d139c8`. Both runs validated the 12-task catalogue,
24 arms at 3600 seconds per arm and all six implementation gates.

- Nine runtime descriptors fail the safe-overlay validation. Their overlay
  `environment` is a symlink and exposes an `are-grader-ctx` directory. Rebuild
  and validate DEV-only overlays and descriptors under BL-044 and BL-041.
- Docker denied access to its socket for all 12 image probes. The local IDs
  are unknown on this host. BL-041 can verify the catalogue pins with read access
  to the Docker daemon using `docker image inspect --format '{{.Id}}' IMAGE`.
- `codex login status` returned `Logged in using ChatGPT` together with a
  read-only PATH alias warning. The preflight's exact-string comparison rejected
  that output. BL-043 tracks dependable launcher preflight gating.
- The pytest matrix failed on both SHAs. Ubuntu 3.11–3.13 each reported two
  catalogue tests requiring a machine-local ARE checkout. Windows 3.12 reported
  those two failures and six public scorer or fake CLI failures. The test
  failures are reproducible from the workflow jobs and are tracked under BL-046.
  The dashboard-browser workflow passed on both SHAs.
  [Current pytest run](https://github.com/ObiVanBanan/BananLoop/actions/runs/45000766669)
  · [Earlier pytest run](https://github.com/ObiVanBanan/BananLoop/actions/runs/44931736277)

A later check on the local working tree used the archived validated runtime
and the corrected probes. It recognized the logged-in Codex status and
blocked on all 12 image IDs because Docker access was denied. An unobserved
ID now reports that verification was unavailable. A mismatch requires an
observed digest that differs from the expected pin.

The local CI repair uses a temporary Git source fixture for catalogue
tests, a subprocess-boundary mock for final Docker scoring, and the active
Python interpreter to launch the runner's fake workers. This preserves the
runner deadline and isolation assertions. The linked remote jobs still
refer to the earlier commit. The current session cannot connect to the
configured Docker socket. The socket is writable under the current UNIX
credentials, but `connect()` returns `EPERM`.

The JSON reports include the exact SHA, probe outputs and CI job links.
[Current HEAD preflight](OFFLINE-PREFLIGHT-2026-11-22-be24a260.json)
· [Earlier SHA preflight](OFFLINE-PREFLIGHT-2026-11-21-HEAD.json)

The owner accepted continued use of the three affected tasks for an internal,
non-RPDI-14 diagnostic, provided source exposure is disclosed. The record is
[`source-access-declaration.json`](source-access-declaration.json); do not claim
blind or independent holdout evaluation for those tasks. The [2026-11-15 preflight](OFFLINE-PREFLIGHT-2026-11-15.json) records the
implementation and environment at that time.
During offline contract research, local `are-grader-ctx` source snippets for the
three new tasks were inspected accidentally. No candidate, evaluator, Harbor
grader, or model was run. This disposition does not restore RPDI-14 independence.
RPDI-14 is unchanged. Board Games remains a separately approved secondary
experiment. See the [plan](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-PLAN.md)
and [updated decisions](../../docs/benchmarks/AUTONOMOUS-BENCHMARK-V01-DECISIONS-2026-11-15.md).
