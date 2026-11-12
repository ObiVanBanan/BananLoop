# AutoResearchExam Harbor adapter status

Status: **Public DEV execution is now runnable in isolation; full BananLoop scoring is not yet wired end to end**.

The isolated copy contains a Harbor `BaseAgent`, a public MCP client, a
`TrustedEvaluatorRunner` backend seam, and a durable public-submission ledger.
The bridge is currently specialized to `budgeted-imputation-mcar50` and a
pre-created Optimization child. It is not a complete benchmark runner: no outer
loop creates up to five Optimization children, and the other nine selected tasks
have no candidate/output mapping. No BananLoop AutoResearchExam score has been
produced.

## Verified environment

- Harbor CLI: `0.25.0` at `/home/sonik/bananloop-benchmark-env/harbor-venv/bin/harbor`.
- The pinned ten task directories under
  `/home/sonik/bananloop-benchmark-env/sources/AutoResearchExam` match the manifest.
- `harbor run --path .../budgeted-imputation-mcar50 --agent nop --env docker
  --dry-run --n-tasks 1` returned `Dry run OK — 1 trial(s); nothing was run.`
- The dry run did not build a container, contact a grader, or make a model call.
- The task configuration exposes an `are` MCP endpoint at
  `http://grader:8000/mcp`. A no-model, verifier-disabled Harbor probe discovered
  `submit`, `result`, and `status`; details are pinned in
  `public-mcp-contract-budgeted-imputation-mcar50.json`.
- A separate verifier-disabled, no-model probe submitted the untouched task starter
  once, then polled `result` three times to `graded`. Public validation score was
  `0.0` over 28 cells. The sanitized record is
  `public-baseline-probe-budgeted-imputation-mcar50-2026-11-12.json`; the raw
  `grader_output_tail` was not saved or forwarded. This baseline is not a BananLoop
  run.
- The terminal result fields and types are captured in the MCP contract file. The
  advertised output schema remains open, and queued/grading payload variants have
  not been validated. Do not forward raw `result` grader output to a model role.
- The public task image was built only from its pinned `environment/` directory;
  its Dockerfile copies only `./app` to `/app`, with no grader context or sidecar.
  Image `bananloop-are-public-devkit-probe:2026-11-12` is pinned to
  `sha256:5d2bac54769dc124be218efc1a1495210197a25652f6005ad327d9fa5bc8f9f4`.
- The complete 42-cell public DEV panel ran against the task's starter imputer in
  a `--network none --read-only` container and returned aggregate R² `-0.0017`.
  This is a public DEV baseline, not a BananLoop campaign score or public
  VALIDATION score. The sanitized run record is
  [`public-devkit-smoke-2026-11-12.json`](public-devkit-smoke-2026-11-12.json).
- The maintainers' public-validation/private-test boundary is pinned in
  [`public-evaluator-attestation-v1.json`](public-evaluator-attestation-v1.json).
  It records immutable repository revisions and README byte digests, the v1.1
  task/grader relationship, and the published public/private grader boundary.
  The attestation explicitly treats this as a maintainer trust claim, not a
  cryptographic audit of server implementation. Core's protocol review builder
  can now include a small digest-verified attestation artifact's contents while
  continuing to omit protocol case inputs.
- `bananloop v2 explore --protocol-review-attestation benchmarks/autoresearchexam/public-evaluator-attestation-v1.json`
  stages that file under the campaign's content-addressed published-artifact
  store. Its digest is frozen against the campaign spec, rechecked on resume,
  and supplied directly to the independent protocol verifier. The option does
  not approve the protocol; the verifier still decides, and the exact runtime
  source review remains outstanding.

## Implemented adapter components

- `harbor_agent.py` implements a Harbor `BaseAgent` and declares
  `AgentCapabilities(mcp_servers=True)`, required because Harbor merges the task's
  public ARE MCP server into custom agents and otherwise rejects the trial.
- `are_public_mcp_client.py` allows only `submit` and `result`, and filters the
  response before it leaves the task container.
- `optimization_runner.py` pins the public backend manifest to the task, version,
  MCP contract, candidate file, public output path, DEV image ID, and 42-cell
  panel; it rejects HOLDOUT and uses BananLoop's evaluator runner seam.
- `public_devkit.py` checks the exact image ID, mounts only the candidate
  `impute.py` read-only, runs the task's public devkit with network disabled and
  a read-only root filesystem, and returns only the panel aggregate. It caps
  Docker resources and output, explicitly stops/removes the named container on
  timeout or error, and disables image pulls.
- Each frozen tier is one canonical aggregate case: the DEV input binds the task,
  image ID, and 42-cell panel; VALIDATION binds the public MCP endpoint and
  contract digest. Multi-case manifests are rejected before evaluation/submission,
  so the receipt cannot imply per-cell scores the backend does not return.
- Evaluator fixtures now support a public-only `DEV` + `VALIDATION` pair as well
  as the existing three-tier layout. Their owner-authored fixture can pin an
  execution backend ID; the Researcher still supplies only metric intent, while
  Core binds the backend identity and exact tier manifests into the frozen
  protocol. This matches ARE's absence of a benchmark HOLDOUT endpoint.
- `build_public_fixture.py` builds a content-addressed two-tier fixture for the
  imputation task. The bundle contains the exact host DEV runner, public task
  `devkit.py`, sanitized public MCP contract, and host Optimization backend;
  runtime admission checks these sources against the frozen digests. The
  independent protocol review can inspect both score-affecting public DEV source
  files, backend dispatch, and public submit/result schemas. The builder never
  reads the task's `are-grader-ctx`, private tests, or hidden evaluator.
- `public_grader.py` stores the one-hour deadline, candidate digest, submission
  identity, uncertainty state, and sanitized public score in SQLite. It refuses
  automatic re-submission after ambiguous delivery and caps candidate slots at
  five.
- Focused offline tests cover the ledger, runner, and MCP response filtering.

## Remaining work before an AutoResearchExam score

1. Obtain independent approval for the task-pinned DEV/VALIDATION protocol. A
   first review rejected the generic argv interpretation; the bounded review
   context now explains the Core-registered backend contract and includes its
   exact digest-bound source, then receives one fresh review.
2. Implement an outer durable campaign driver that creates the approved parent
   and up to five child Optimization campaigns, carries evidence between them,
   and resumes without duplicating evaluation.
3. Generalize candidate/output mapping and independent evaluator protocol setup
   for all ten selected tasks.
4. The no-model Harbor lifecycle probe is complete through the safe overlay. Run
   one complete single-task BananLoop pilot before starting the ten-task series.

## Harbor lifecycle preflight and artifact boundary

The earlier probe at
`/tmp/harbor-are-bridge-preflight-2026-11-12/2026-11-12__09-41-49` is
inconclusive: the job was left pending with empty logs.

A second no-model preflight used `model_call_limit=0`, a synthetic missing
campaign ID, and `--disable-verification`. It confirmed Harbor instantiated the
custom agent, accepted its MCP capability/options, started the task container,
and reached `run_v2_optimize`; the expected terminal error was
`campaign registry does not contain the requested campaign`. The public
submission ledger has zero submissions and the Harbor result reports zero model
usage. This is an adapter lifecycle check, not a BananLoop benchmark run.

The task's Harbor config still ran its `verifier.collect` hook despite
`--disable-verification` and copied the task-declared grader artifact
`/var/lib/are` into that preflight's job directory. I did not inspect that
artifact; the generated copy was moved to a mode-700 quarantine at
`/tmp/bananloop-are-private-artifact-quarantine/are-sidecar-1794459150`.
The main and sidecar containers were created, but no candidate was submitted.
Do not run further Harbor trials against the source task directory as-is. A
diagnostic task overlay must first omit both the task-level `artifacts` entry
and `verifier.collect` while retaining the public MCP service; record its
modified task hash and keep private verifier collection disabled.

`prepare_safe_harbor_task.py` now creates an overlay only when the source
revision, task/instruction hashes, and task-specific artifact/collector entries
match the reviewed shortlist. It created safe overlays for all ten selected
tasks. The index with original and overlay task hashes is
`/home/sonik/bananloop-benchmark-env/safe-overlays/index.json`. The imputation
overlay is
`/home/sonik/bananloop-benchmark-env/safe-overlays/are-v1.1-budgeted-imputation-mcar50`
with task hash
`3f2294f4ab6605481bf363025ef5bb63283bcb3e48dd04c1c3da9d7eeea9fdc4`.
Each preserves its original task instruction and public environment but is a
local adapter task, not the official Harbor task digest. A no-model
preflight through this overlay reached BananLoop and failed only on the
synthetic missing campaign ID; its artifact manifest contains only
`/logs/artifacts`, with no grader sidecar artifact or collect hook.

Rebuild the standalone public DEV image from the pinned task's `environment/`
directory with:

```bash
docker buildx build --load --platform linux/amd64 \
  --tag bananloop-are-public-devkit-probe:2026-11-12 \
  --file Dockerfile .
```

The runtime refuses to evaluate if Docker reports an image ID different from
the evaluator protocol pin.

Recreate it with:

```bash
python benchmarks/autoresearchexam/prepare_safe_harbor_task.py \
  --source /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam \
  --task-id budgeted-imputation-mcar50 \
  --out /home/sonik/bananloop-benchmark-env/safe-overlays/are-v1.1-budgeted-imputation-mcar50
```

Build the owner-pinned public-only evaluator fixture from the public source task:

```bash
uv run python -m benchmarks.autoresearchexam.build_public_fixture \
  --repo "$PWD" \
  --source-task /home/sonik/bananloop-benchmark-env/sources/AutoResearchExam/budgeted-imputation-mcar50 \
  --out "$HOME/bananloop-benchmark-env/are-public-imputation-fixture-v2.json"
```

The current source revision is `022df229d9133f8e84933de12fe51737cf593e69` on
`codex/autoresearchexam-verifier-evidence`. The full offline suite passed on that
revision: 1,104 collected, 1,102 passed, 2 skipped, exit code 0. The focused
ARE fixture, runner, and protocol-review tests passed: 31 tests.
The combined benchmark status and public DEV smoke record are in
[`../pilot-status-2026-11-12.json`](../pilot-status-2026-11-12.json) and
[`public-devkit-smoke-2026-11-12.json`](public-devkit-smoke-2026-11-12.json).
A Harbor `nop`, public baseline, or direct-writing agent is not a BananLoop
benchmark result.
