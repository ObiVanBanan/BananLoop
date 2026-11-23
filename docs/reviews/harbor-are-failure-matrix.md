# BL-044 · Harbor, pivot and evaluator failure matrix

Offline review on PR #146 at `be24a260615179f9437859e87fe8a8b2b5b12377`.
The [historical fault report](../benchmarks/AUTONOMOUS-V01-FAILURE-REPAIR-2026-11-17.md)
describes the original series. The current
[preflight](../../benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json)
reports `PREP_BLOCKED`.

| Failure | Current evidence | Status |
|---|---|---|
| Missing candidate entrypoints for ACT Tensor, CausalPFN and HiCard | The three solution paths exist in the pinned Git tree. The fake 12×2 backend suite exercises source preparation and final archive creation. | Fixed in source. |
| ZIP handed to the JSON pivot decoder | `test_benchmark_pivot_accepts_opaque_zip_bundle_before_hypothesis_selection` passes a real ZIP through the pivot checks. | Fixed with regression coverage. |
| CARPS stopped before dispatch | The original failure recorded empty stdout/stderr and zero dispatches. A later archive records `PROTOCOL_REJECTED` with two observed model turns. These observations describe separate failures. | BLOCKED. The original setup cause is unresolved. |
| CARPS external final assessment | `python -c 'import numpy'` fails in the BananLoop project venv and succeeds with NumPy 2.1.3 in the Harbor venv. The historical continuation scorer imported in the project interpreter. The later archived launcher invokes the scorer through the Harbor interpreter and its CARPS receipt is GRADED with eight cells. | Interpreter boundary verified in the archived launcher. Current checkout-level replay remains blocked. |
| Label-risk fixture aggregation | Current runtime ZIP declares MEAN while the catalogue pins MEDIAN. The current builder produced a replacement matching the metric ID, field, MINIMIZE direction and MEDIAN aggregation. | Rebuilt offline. The new fixture needs a pinned descriptor and protocol review. |
| Shortest-CI fixture metric | Current runtime ZIP declares `shortest_ci_task_score/task_score/MAXIMIZE`; the catalogue pins `relative_interval_length/metric/MINIMIZE`. The current builder produced a replacement matching all four metric fields. | Rebuilt offline. The new fixture needs a pinned descriptor and protocol review. |
| Newton and SOPCC Full campaigns | Both current runtime overlays fail the DEV-only validator. Newly built overlays pass it. The latest archived Full attempts report `OPTIMIZATION_FAILED`. | Offline overlay preparation passed. The controlled DEV cycle is BLOCKED by Docker socket permission and the failed current preflight. |

At this SHA, 33 tests passed across the fake series, ZIP asset review, CARPS
metric contract, shortest-CI runner, public fixture builder, Newton paired
DEV contract and SOPCC fixture/runner tests. The explicit ZIP pivot test also
passed in the separately verified campaign CLI suite.

## Reproduction

From this checkout, repeat the overlay preparation for each affected task with
a new output directory. The source checkout is pinned at
`23c0d92e11af1dea821f972b5f0a739060958002`.

```sh
.venv/bin/python -m benchmarks.autoresearchexam.prepare_safe_harbor_task \
  --local-public-dev-only --source "$ARE_SOURCE" --task-id "$TASK_ID" --out "$OUTPUT"
```

Use the task's public MCP contract path from its local runtime descriptor with
`build_labelrisk_fixture --contract` or `build_shortest_ci_fixture --mcp-contract`.
For shortest-CI, use the catalogue image name and ID with
`--validation-metric-semantics public_are.validation_score=relative_interval_length.lower_is_better.v1`.
The rebuilt JSON hashes are:

| Fixture | SHA-256 |
|---|---|
| Label-risk | `9f09f819c34c0c719e9a90f4c449f0dc4e0f985f027eb254cf4c8d8db7ee4ee8` |
| Shortest-CI | `d0f921eb8d4d6324de688619409a230bfa8d3f273f465f316a0289d9415fdbf0` |

This verification wrote fixtures and overlays to a new temporary directory.
The controlled DEV cycle and fixture reapproval remain open. No models,
Harbor jobs, containers, or grader calls were started.

## Later archived runtime

The frozen runtime under
`/home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p0-dev-avio_2mq/runtime`
passes all 12 descriptor checks at `be24a260`. A second offline preflight
against that runtime still returned `PREP_BLOCKED` with 12 image probes
denied by the Docker socket. The archived Full launcher and receipts are
from `ada1f18`. Their checksums match the archive index. They include corrected
label-risk and shortest-CI fixture semantics and a completed CARPS final DEV
score. The CARPS campaign itself remains `PROTOCOL_REJECTED`; the earlier
zero-dispatch cause is unresolved.


## CARPS zero-dispatch audit

The preserved arm under `autonomous-v01-live-20261116-c0966ea` has zero-byte
Exploration stdout and stderr. Its audit is bound to campaign database
SHA-256 `5709bed76b1c8a54c0eb7e1c1fb68679dcf02285845b9a998088ae4cb8513a08`
and records zero reservations, worker jobs and worker events. The arm result
is `FAILED_BEFORE_MODEL_DISPATCH`, with zero model turns. A separate
`ModuleNotFoundError` is recorded during final scoring after all 24 arms.
That scorer failure occurred later. The archive lacks an initiating setup
exception, so the original stop remains unresolved.


## Exact-HEAD recheck with archived descriptors

The offline preflight was rerun from a fresh Git archive of
`be24a260615179f9437859e87fe8a8b2b5b12377` with
`--runtime-root /home/sonik/bananloop-benchmark-env/runs/autonomous-v01-p0-dev-avio_2mq/runtime`.
It validated all 12 descriptors and reported `PREP_BLOCKED` for the 24
arms. The 12 image-ID probes could not contact Docker. The CLI auth check
also rejected the login probe.

The descriptors bind label-risk to fixture
`9f09f819c34c0c719e9a90f4c449f0dc4e0f985f027eb254cf4c8d8db7ee4ee8`
and shortest-CI to fixture
`c999f8e4966584cf9ca4887ff326da51ec22192859bf116e3ae23017d887a17d`.
These are the archived descriptor pins. The rebuilt shortest-CI fixture
listed above is a separate candidate for review. The public MCP contracts
and overlay hashes are pinned in each descriptor. A new approval must
bind the fixture actually selected for a fresh campaign.

The Newton/SOPCC preparation can be checked offline. The controlled
public DEV cycle remains blocked at the Docker image probe. Earlier
reports and runtime descriptors were read in place.
