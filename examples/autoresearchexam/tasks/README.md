# Carps broker

This adapter implements one public Carps case per sandbox invocation. The
candidate container sees `select_subset.py` and one JSON request on stdin: the
public cloud, `k`, the fixed seed, and the 10 second task budget. It receives no
grader files, reference scores, other case inputs, or evaluator mount. The
trusted evaluator validates indices and computes exact star discrepancy only
after the candidate container exits. Invalid output, exceptions, and timeouts
receive the pilot's maximum discrepancy penalty of `1.0`.

Upstream is pinned to
[`bespokelabsai/AutoResearchExam@23c0d92`](https://github.com/bespokelabsai/AutoResearchExam/tree/23c0d92e11af1dea821f972b5f0a739060958002/carps-star-discrepancy-subset-select).
The public example panel is frozen as `cloud_00`–`cloud_04` DEV,
`cloud_05` VALIDATION, and `cloud_06`–`cloud_07` public-pool HOLDOUT, with seed
0 per cloud. HOLDOUT is operationally withheld from the candidate/evaluator
caller until freeze; because its source is public, it is not an official hidden
evaluation set. `prepare_carps.py` downloads only those eight explicitly pinned
public files and verifies each Git blob SHA-1, dimensions, dtype, values, and
records a SHA-256 for each file. It never fetches upstream hidden/sealed data.

## Runtime image

The trusted runtime needs Python 3.13 and NumPy 2.2.6. Build it from the repo
root, then use the resulting image's immutable digest in `CandidateSandbox`:

```powershell
docker build --pull=false -f examples/autoresearchexam/tasks/Dockerfile.carps -t bananloop-carps-runtime:local .
docker image inspect bananloop-carps-runtime:local --format '{{index .RepoDigests 0}}'
```

`CandidateSandbox.command` must be `("python", "-I",
`/opt/are/carps_runner.py")`. The task adapter enforces one CPU and a 15 second
outer deadline (10 second function budget plus the upstream 5 second kill
grace). The image digest must be captured after the build and independently
reviewed; the moving local tag is never accepted by the sandbox.

## Preparation and current limits

```powershell
python -m pip install "numpy==2.2.6"
python -m examples.autoresearchexam.tasks.prepare_carps --out <private-evaluator-path>\carps
python -m pytest tests/test_are_carps.py -q
```

The prepared directory contains all splits and is evaluator-only. Keep it
outside agent workspaces and mounts. `evaluate_case` also requires the agent to
be stopped and the candidate snapshot frozen before staging. This module does
not itself prove host ACLs, same-user process isolation, BananLoop readiness or
`CommandEvaluator` integration, model proxy isolation, USD accounting, total
Docker disk use, or a full target-host negative preflight. No model call,
benchmark run, DEV score, or HOLDOUT score has been made. The current desktop
session denied access to the Docker Desktop pipe during preflight, so the
runtime image was not built or exercised here. These gaps keep Issue #64
ineligible until separately cleared.

The scorer logic is adapted from upstream `grader_core.py` under Apache-2.0;
see `NOTICE.autoresearchexam` and `LICENSE.autoresearchexam`.
