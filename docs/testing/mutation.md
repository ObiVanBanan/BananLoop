# Mutation testing (initial opt-in configuration)

`pytest-gremlins` is an optional pytest plugin. Ordinary pytest runs do not
enable mutations. The initial targets are `src/bananloop/policy.py` and
`src/bananloop/budget.py`, covering evaluation validity, improvement thresholds,
guardrails, promotion decisions and pre-action budget limits. This is a small
scope, not full coverage of resource reservations, evaluation isolation, or
campaign control; those require separate follow-up runs and evidence.

The plugin is pinned to `1.13.1` in the `mutation` extra. Use a fresh virtual
environment and install the project with `python -m pip install -e ".[mutation]"`.
This installation requires package access and is not part of normal runtime
dependencies. A lockfile is needed to pin transitive dependencies for stronger
environment reproducibility.

Run from the repository root after installation:

```sh
python -m pytest -p no:cacheprovider tests/test_policy.py
python -m pytest -p no:cacheprovider tests/test_policy.py --gremlins --gremlin-targets=src/bananloop/policy.py --gremlin-operators=comparison,boundary
```

The command bounds scope to one source file, selected tests, and two mutation
operators; it does not impose a wall-clock limit. Mutation execution can still
be slow or hang, and a run interrupted by the operator is incomplete. No
production model, network call or live experiment is needed for these selected
tests. Do not infer a score from an incomplete run. Inspect survivors individually
and distinguish genuine missing assertions, equivalent mutations, uncovered
code, tool errors, and timeouts before deciding how to respond. No mutation
score threshold is configured until an actual baseline has been measured.

Boundary tests in `tests/test_budget_policy.py` check exact attempt, experiment,
agent-call, evaluator-call and deadline limits. The optional CI workflow
`.github/workflows/mutation-policy.yml` runs policy and budget subsets on relevant pull
requests and by manual dispatch. Its job has a twelve-minute timeout and uploads
both console reports. Surviving mutants are reported without a score gate until
they have been classified.

## Platform status and verification

The tool's [comparison documentation](https://pytest-gremlins.readthedocs.io/en/latest/guide/comparison/)
describes pytest-gremlins as cross-platform and mutmut as requiring Unix or
WSL; the latter's [own documentation](https://mutmut.readthedocs.io/en/latest/)
confirms that it requires fork. A limited Windows run is recorded below. It is
not evidence for every Windows setup or for Linux CI.

## Initial Windows trial (2026-10-29)

On Windows, Python 3.13.1 and pytest-gremlins 1.13.1, the selected
`tests/test_policy.py` tests passed (9 tests). The completed mutation command
reported 53 mutations: 19 zapped and 34 survived, in 277.99 seconds, exit 0.
The reported leading survivor locations were `policy.py` lines 59, 69, 73,
75, 105 and 106. This is evidence only for the specified target, operators and
tests; it is not a project-wide mutation score. Survivors have not been triaged
as equivalent, missing assertions or other categories. No mutation threshold is
set, and coverage of budget and evaluation isolation remains future work.

After adding boundary tests for exact improvement, guardrail operators and
valid metric endpoints, the same 53 mutations produced 30 zapped and 23
survived with 17 passing policy tests in 290.50 seconds (exit 0). The earlier
test subset zapped 19 and left 34. Eleven additional mutations were detected;
the remaining 23 have not been classified. The newer run still surfaced
survivors at protocol comparison, fixed input alignment, and paired decision
lines. These locations guide further tests, but the summary alone does not
prove that each survivor is a missing assertion.
Three focused checks for a frozen protocol, matching candidate inputs, and
required paired case metrics were then added. They pass locally, but the
53-mutation policy run has not yet been repeated with those checks; the 30/23
result above remains the latest measured mutation result.

The budget boundary subset (`tests/test_budget_policy.py` against
`src/bananloop/budget.py`, comparison and boundary operators) first zapped 11
of 12 mutations and left one survivor: `deadline_reached` at the exact instant.
The added frozen-clock assertion detected that case. The repeat run zapped all
12, with 8 passing tests in 81.23 seconds, exit 0. This result covers those
operators and selected tests only; it is not evidence about resource reservation
or accounting elsewhere in the engine.

The observed trial used temporary local dependencies rather than an installed
project environment. From the repository root, its exact PowerShell setup and
command were:

```powershell
$env:TEMP=(Resolve-Path 'plan/operator-roadmap/feasibility/temp').Path
$env:TMP=$env:TEMP
$env:PYTHONPATH='plan/operator-roadmap/feasibility/mutation-deps;src'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTEST_ADDOPTS='-p pytest_cov.plugin'
python -m pytest -p pytest_gremlins.plugin -p no:cacheprovider tests/test_policy.py --gremlins --gremlin-targets=src/bananloop/policy.py --gremlin-operators=comparison,boundary
```

The temporary dependency directory is a record of this trial, not an install
method or a portable prerequisite; use the virtual environment installation
above for future runs. `TEMP` and `TMP` were redirected because the sandbox
denied access to the default temporary location; that is an environment-specific
workaround. An earlier attempt with plugin autoload disabled failed because
subprocess pytest did not recognize `--no-cov`; explicitly loading pytest-cov
for subprocesses via `PYTEST_ADDOPTS` resolved that issue for this trial. The
earlier interrupted attempt is excluded from the results. After the completed
run's summary, a `pyreadline3` destructor emitted a Windows handle error; the
process still returned 0. Recheck these constraints in a clean environment.

Configuration syntax and operator names follow the
[pytest-gremlins configuration reference](https://pytest-gremlins.readthedocs.io/en/latest/guide/configuration/).
Record Python, pytest, plugin versions, commit, exact command, platform, exit
status and complete report with each future run. Never count infrastructure
failures or unfinished mutants as killed or survived.

## Current implementation checkout status (2026-10-30)

The active project checkout adds explicit min-improvement and guardrail
operator boundary assertions in `tests/test_policy.py`, plus deterministic
budget boundary and frozen-clock tests in `tests/test_budget_policy.py`.
The ordinary focused baseline passed: 20 tests. The checkout also defines the
optional `mutation` extra and a 12-minute CI workflow that uploads policy and
budget mutation logs; it intentionally has no score threshold.

The first sandboxed installation attempt could not resolve PyPI. The pinned
plugin was subsequently installed into a local temporary target and the focused
mutation jobs were run with the required local process permissions. Environment:
Python 3.13.1, pytest 9.2.0, pytest-gremlins 1.13.1, Windows. Full JSON reports
are saved under `mutation-results/`.

| Scope | Mutants | Killed | Survived | Error/timeout | Focused tests |
|---|---:|---:|---:|---:|---:|
| Policy, frozen inputs, tier manifests, paired gates | 47 | 47 | 0 | 0 | 33 passed |
| Budget comparisons and boundaries | 12 | 12 | 0 | 0 | 8 passed |
| Typed pause/resume control boundary | 1 | 1 | 0 | 0 | 7 passed |

The policy run included direct DEV/validation/HOLDOUT manifest checks, paired
comparison boundaries, the completion paired-guard test, and artifact-evaluation
tests for frozen protocol and fixed-input identity. The latter changed every
case input hash so the inverse of the per-case equality check could not survive
because unchanged cases masked it. The control run covered idempotent receipts,
campaign identity, accepted-vs-completed pause, explicit resume, and terminal
end. No mutants survived, timed out, errored, or were pardoned; there are no
equivalent survivors to classify in these measured scopes. This is still a
bounded set of operators and files, not a project-wide score.

One earlier restricted artifact-only run completed with 7/47 killed and 40/47
survivors because its test selection exercised only protocol-specific cases;
those unrelated policy comparisons were out of scope for that invocation. It
was superseded by the combined policy and artifact run above. An earlier
uncompleted run is excluded. The CI workflow has been added but has not yet run
on GitHub. It has a 12-minute job timeout, fixed source/test scope, and uploads
all three logs; there is no score gate.

Reproduce the measured scopes after installing `.[mutation]` and `pytest-cov`:

```powershell
$env:PYTHONPATH='src;tests'
python -m pytest -p no:cacheprovider tests/test_policy.py tests/test_completion.py::test_multiple_paired_bounds_cannot_overwrite_failed_predicate tests/test_artifact_evaluation.py::test_fixed_input_identity_is_enforced_without_paired_acceptance tests/test_artifact_evaluation.py::test_core_protocol_manifests_are_tier_specific_frozen_and_private --gremlins --gremlin-targets=src/bananloop/policy.py --gremlin-operators=comparison,boundary --gremlin-batch --gremlin-batch-size=10 --gremlin-report=console,json
python -m pytest -p no:cacheprovider tests/test_budget_policy.py --gremlins --gremlin-targets=src/bananloop/budget.py --gremlin-operators=comparison,boundary --gremlin-report=console,json
python -m pytest -p no:cacheprovider tests/test_operator_control.py --gremlins --gremlin-targets=src/bananloop/operator_control.py --gremlin-operators=comparison,boundary --gremlin-report=console,json
```

On the restricted Windows runner, the measured shell also set `TEMP`/`TMP`,
disabled pytest plugin autoload, and set `PYTEST_ADDOPTS=-p pytest_cov.plugin`
for subprocess runs. These are environment workarounds; CI installs the
dependencies normally and uses the checked-in workflow.

## Mutation coverage boundary after operator roadmap work

The measured mutation reports above cover only the listed policy, budget, and
single typed pause-control target/operator combinations. They do not establish
mutation sensitivity for pivot proposal integrity/approval, child-spec lineage,
Codex chat secrecy, Pi command parsing, or terminal rendering. Those areas have
unit, integration, and security-review evidence, but no mutation report. Do not
interpret the 100% results in the bounded JSON reports as project-wide or
operator-roadmap mutation coverage. Extend mutation targets and add a separately
bounded run before making claims about those invariants.
