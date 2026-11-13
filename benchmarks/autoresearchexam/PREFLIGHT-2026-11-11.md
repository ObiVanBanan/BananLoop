# AutoResearchExam pilot preflight (historical, 2026-11-11)

> **Correction (2026-11-12):** The verification section below listed
> `test_are_public_grader_adapter.py` and `test_autoresearchexam_selection.py`, but
> neither exists in the isolated checkout. Do not use the historical 15-pass count
> as evidence for a Harbor adapter. The current implementation status is in
> `HARBOR_ADAPTER.md`.

## Pinned selection

- Source revision: `23c0d92e11af1dea821f972b5f0a739060958002`
- Task-selection manifest SHA-256: `071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316`
- Ten selected task directories are pinned to version 1.1.0.
- `verify_task_selection.py --source /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam`
  verified all ten `task.toml` and `instruction.md` hashes against that checkout.

## Local execution environment

- Harbor: 0.25.0
- Docker Engine: 29.8.2, overlayfs
- Docker Compose: 5.6.0
- Host: x86_64 Linux, 12 CPUs, 31.26 GiB RAM
- Free disk: about 881 GiB
- Cached images: only one 4 GiB SWE-bench image; no AutoResearchExam image.

## Static task check

`harbor lint --json` over all ten selected task directories returned one finding
class: `compose-additional-capabilities`. Every task grants `SYS_ADMIN` to both its
`grader` and `test-grader` services. With only that rule ignored, Harbor lint returns
no findings. No task containers were built or started during this preflight.

## Current BananLoop verification

On 2026-11-12, the full offline test suite in the isolated copy
`/home/sonik/Проекты/BananLoop-ARE-work-copy` reached 100%: 1,053 collected, two
skipped, no failures (`.pytest_cache/v/cache/lastfailed` is empty). This verifies the
current repository tests, not an AutoResearchExam adapter; no AutoResearchExam task
has been scored by BananLoop.

Harbor `0.25.0` also completed a `--dry-run` of
`budgeted-imputation-mcar50`, with one trial resolved and no task execution. A
separate no-model probe with verification disabled discovered the public MCP tools
and queried read-only status (zero submissions, zero active grading);
the result is pinned in
`public-mcp-contract-budgeted-imputation-mcar50.json`. A separate one-submit probe
then scored the untouched public starter: validation score `0.0`, 28 cells, three
result polls, verification disabled, no model calls. Its sanitized record is
`public-baseline-probe-budgeted-imputation-mcar50-2026-11-12.json`; this is not a
BananLoop score and did not count toward the ten-task series.
