# CARPS evaluator backend integration

The task-specific fixture builder and runner are in `build_carps_fixture.py`
and `carps_runner.py`. They do not alter or automatically register an adapter
in the Harbor bridge.

## Protocol meaning

The trusted protocol must pin:

- `execution_backend_id`: `autoresearchexam-carps-public-v1`
- `metric_id`: `autoresearchexam.carps.public_task_score`
- `metric_field`: `public_task_score`
- `direction`: `MAXIMIZE`
- output path: `/app/output/select_subset.py`
- candidate path: `benchmarks/autoresearchexam/solutions/carps/select_subset.py`

The public DEV evaluator computes mean exact L-infinity star discrepancy over
the eight pinned example clouds and reports its negative as a higher-is-better
proxy. VALIDATION uses the public ARE `validation_score` as the official
higher-is-better task score. The task's public docs establish that lower
discrepancy earns a higher score, but do not specify a numeric transformation
between raw discrepancy and the official score; therefore the two sources are
tagged separately in result diagnostics and are not presented as numerically
comparable.

## Required Harbor wiring

The existing Harbor bridge still needs an explicit adapter route for this
backend. That route must pin the task ID/version, MCP contract digest, image
reference and immutable image ID, candidate/output paths above, and instantiate
`AREPublicCARPSEvaluatorRunner` with the existing `PublicAREGrader` ledger and
candidate publication callback. The campaign's approved protocol must match
the metric fields above before the runner is invoked.

Build the fixture only after a public-only `initialize` + `tools/list` contract
for this task has been observed and stored locally. The builder also requires
a clean runtime image and its immutable Docker image ID. The image must contain
only the runtime packages needed for this task; do not use a benchmark image
that contains another task's data or any grader-side materials.

The fixture contains only public task metadata, instructions, README, the eight
public example clouds, the observed MCP contract, and public BananLoop runner
modules. It contains no task tests, hidden arrays, or private grader artifacts.
