# AutoResearchExam Harbor adapter status

Status: **one negative public DEV candidate result is recorded; end-to-end scoring remains incomplete**.

The isolated copy contains a Harbor `BaseAgent`, a public MCP client, a
`TrustedEvaluatorRunner` backend seam, and a durable public-submission ledger.
The bridge has task-pinned candidate/evaluator mappings for all ten selected
tasks. CovType uses an explicitly external static-price diagnostic proxy and
is not official benchmark-compatible scoring. Each task still requires its
own approved protocol and Optimization run. `series_runner.py` dispatches only
already-prepared task configs; it does not prepare or approve campaigns or
protocols, or automatically create the ten task configs. One frozen
Optimization child can now run up to five candidate iterations internally.
No completed
AutoResearchExam task score has been produced.

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
- The owner-approved `iterative_ridge` pivot ran as child campaign
  `c-dc0d93b495723084d965ee630fc402fd`, with the exact approved write path
  `benchmarks/autoresearchexam/solutions/impute.py`. It used one Luna response
  (1,371 reported model tokens) and two DEV evaluator calls. Candidate DEV R²
  was `-0.7818`, compared with starter `-0.0017`; it is a negative experiment,
  not a benchmark score. The campaign report is `STEP_LIMIT`, with the candidate
  still pending Integration Verifier.
- The first Harbor attempt failed before a model call because its configured
  visible paths exceeded the child autonomy scope. Restricting visibility to the
  approved `impute.py` path allowed the candidate to be generated and evaluated.
- A later resume failed on the baseline public VALIDATION submit. The durable
  ledger records `SUBMISSION_UNKNOWN` with no submission ID. Inspection of a
  verifier-disabled status-only trial reproduced the underlying cause: Harbor
  materialized the host `NamedTemporaryFile` with mode `0600` under a different
  UID, so the task agent could not read it. The old task environment was deleted;
  the unknown ledger entry is retained and has not been retried or rewritten.
- The bridge now creates MCP request files as the task agent user with mode
  `0600`. A separate status-only Harbor trial passed after this change and
  reported zero submissions and zero queued/grading jobs; it made no model or
  evaluator calls. This fresh task environment cannot reconcile the deleted
  environment's old unknown request.
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
- `are_public_mcp_client.py` allows public `submit`, `result`, and read-only
  `status`, classifies transport failures without recording server text, and
  filters the response before it leaves the task container.
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
  `devkit.py`, `cellspec.py`, `cellrunner.py`, public task instructions, sanitized
  MCP contract, host Optimization backend, public grading ledger, and task-container
  MCP client; runtime admission checks these sources against the frozen digests.
  The independent protocol review can inspect score-affecting DEV code, backend
  dispatch, lowercase MCP status normalization, public submit/result schemas, and
  fail-closed handling of grader error flags.
  The official validation server remains an externally trusted measurement service,
  not locally source-audited. The builder never
  reads the task's `are-grader-ctx`, private tests, or hidden evaluator.
- `public_grader.py` stores the one-hour deadline, candidate digest, submission
  identity, uncertainty state, and sanitized public score in SQLite. It refuses
  automatic re-submission after ambiguous delivery and caps candidate slots at
  five.
- Focused offline tests cover the ledger, runner, and MCP response filtering.

## Remaining work before an AutoResearchExam score

1. Resolve the indeterminate public baseline ledger without resubmitting; the
   current task container is gone, so the campaign remains incomplete.
2. Complete Integration Verifier and public Validation on a fresh owner-approved
   child run after the uncertainty policy and enrollment are explicit.
3. Prepare per-task frozen Optimization children and independently approve
   their evaluator protocols; Harbor validates that each child funds five
   candidate iterations within the one-hour job limit.
4. Add an automated preparation workflow for the ten task descriptors and
   configs, including durable resume without duplicating evaluation.
5. Run the ten-task series after the single-task acceptance and all ten
   prepared-job validations are complete.

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

## SOPCC public DEV runner — 2026-11-12

`public_sopcc_dev.py` now runs the selected SOPCC task's public `dev_eval.py`
inside a networkless, read-only container. The runner verifies the local image
ID before execution, mounts only candidate `policy.py` read-only at
`/app/solution`, bounds output, and returns aggregate reward/failure metrics.
Its default panel is a smoke sample (draw 0, one instance, four episodes), not
the official 6,400-episode DEV panel. It does not call the model or grader and
does not make a task campaign runnable by itself. `sopcc_runner.py` now binds
that aggregate into the shared BananLoop `TrustedEvaluatorAdapter` contract,
and `build_sopcc_fixture.py` can assemble the public-only DEV/VALIDATION
fixture after a task-specific public MCP contract is observed. The Harbor
agent now pins the SOPCC output path. A live BananLoop Researcher call was made
in a separate one-task campaign; protocol review then hit a source/context
bound and no Optimization or grader submission followed. See the SOPCC pilot
section below for the trace and current limits.

Build the image from the pinned public task environment:

```bash
docker buildx build --load --platform linux/amd64 \
  --tag bananloop-are-sopcc-public-dev:2026-11-12 \
  --file Dockerfile .
```

The observed image ID was
`sha256:5fc63eccd0a3448db1dd9240c3fe7ecb4cef02511bafc77e2c91f294c3d82b8e`.
The baseline smoke report, including the runner source digest, is
`results/sopcc-public-dev-baseline-smoke-2026-11-12.json`.


## Safe overlay Harbor dry-run for all selected tasks — 2026-11-12

All ten prepared safe overlays passed Harbor 0.25.0 `--dry-run` using individual JSON job configs. This validates only Harbor configuration and task metadata selection; no trial, task agent setup, container, model request, or grader call was executed. Each result has `Dry run OK — 1 trial(s); nothing was run.` The machine-readable record and exact temporary configs are at `/home/sonik/bananloop-benchmark-env/runs/autoresearchexam-dry-run-20261112/`. This does not validate task-specific candidate mappings or evaluator protocols.

## SOPCC adapter pilot, 2026-11-12

The bridge now has a second task-pinned adapter for
`sopcc-online-chance-constrained-policy`:

- The baseline policy is `benchmarks/autoresearchexam/solutions/sopcc/policy.py`.
- Candidate access is limited to that file and Harbor output
  `/app/solution/policy.py`.
- The baseline passed the isolated public DEV panel: reward `3.3136`, failure
  rate `0.0`, four episodes. The image was pinned to
  `sha256:5fc63eccd0a3448db1dd9240c3fe7ecb4cef02511bafc77e2c91f294c3d82b8e`.
- A no-model Harbor probe captured the public MCP `tools/list` contract and
  read-only `status`; it did not call `submit` or `result` and disabled the
  private verifier.
- The first BananLoop campaign was created at
  `/home/sonik/bananloop-benchmark-env/runs/sopcc-bananloop-campaign-2026-11-12/`
  with five candidate iterations, a one-hour deadline, and a 12-call evaluator
  budget. One GPT-6 Luna Researcher call completed (4,997 reported tokens).
  Cost remains `UNKNOWN` for the ChatGPT plan.
- Protocol review initially stopped before calling the independent verifier:
  the complete public SOPCC bundle has 142,666 source characters and produced
  a 153,947-character review context, exceeding the old 88,000/104,000 limits.
  No public grader submission occurred. No automatic replay occurred.
- The verifier source/context and role-context bounds were raised to 160,000 / 180,000
  characters. A regression test builds a larger public fixture, checks that the
  complete source is retained, and validates the bounded verifier context. A
  no-model recovery check now prepares the independent-verifier job successfully.

This is not a completed SOPCC BananLoop optimization: the protocol has not yet
been approved, no Optimization candidate was generated, and there is no public
VALIDATION score. The original campaign remains durable for evidence; its token
allowance is 8,192 per call, while SIWC does not enforce that as a provider-side
hard limit and the full verifier context is 153,947 characters. Actual verifier
usage is therefore unknown. The verifier job is prepared but has not been
dispatched. Do not edit the frozen spec or execute that job under an assumed
token cap. Start a new campaign only after selecting a budget that can fund the
complete verifier context and child roles.
