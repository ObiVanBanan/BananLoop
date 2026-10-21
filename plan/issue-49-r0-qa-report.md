# Issue #49 R0 QA report

**Status:** incomplete; this report records local evidence and outstanding R0 gates.  
**PR:** [#47](https://github.com/ObiVanBanan/BananLoop/pull/47)  
**Code revision tested:** `b3f773fee3404f51afbe7a7148df483540424797`  
**Environment:** Windows, Python 3.13.1.

## Local verification

| Check | Evidence | Result |
|---|---|---|
| Full pytest | `python -m pytest -q --basetemp .pytest-issue49-full-cc33021`; exit code 0. `pytest --collect-only` found 312 tests; run output showed five skips and no failures. | 307 passed, 5 skipped |
| DEV reflection and metric research regressions | `tests/test_research.py`; includes symmetric noise-band handling, DEV-only research state, hidden-tier isolation, and reframe family checks. | Passed in full suite |
| Process cancellation regression | `tests/test_completion.py::test_stop_cli_cancels_running_agent_and_preserves_source`; fixed in `0e484e133451e4781f2a2357cd4f01ae86c66cde`. | Passed in full suite |
| API preflight edge cases | `tests/test_candidate_preflight.py`; includes aliased `OneHotEncoder`, wrapper runtime safe-skip, Windows `python.exe`, and `py.exe -3.12` selector preservation. | Passed in full suite |
| Focused research/preflight suite | `python -m pytest tests/test_candidate_preflight.py tests/test_research.py -q --basetemp .pytest-issue49-edge-cc33021`. | 27 passed, 1 skipped |

The full run was performed after the runtime probe changes and before this report was added. The separate CI configuration commit `cc3302185bff88cbecf562d59ea1bf42d2d23ac9` sets matrix `fail-fast: false`, so one startup failure no longer hides the independent job outcomes.

## GitHub Actions

Run [39647659677](https://github.com/ObiVanBanan/BananLoop/actions/runs/39647659677), revision `cc3302185bff88cbecf562d59ea1bf42d2d23ac9`, reports all four matrix jobs failed. The GitHub Actions API shows each job completed in about two seconds with `runner_id=0`, an empty `runner_name`, and `steps=[]`. The failure occurred before runner assignment and before checkout/setup/pytest. Earlier attempts showed the same pattern. Log downloads return `BlobNotFound`.

**Conclusion:** the Linux/Windows CI gate is not green and cannot be counted as executed. Available evidence points to hosted-runner startup failure rather than a test failure; a runner-assigned rerun is required to establish CI results.

## R0 requirements still open

- No Bank Marketing campaign was run, so there are no genuine experiment receipts, baseline metrics, or a complete DEV → failure capsule → different hypothesis family → DEV chain with retry/resume evidence. The PR branch currently contains no Bank Marketing fixture. Per the operator's standing preference, campaign execution remains separate from preparation.
- No cross-platform GitHub Actions run has reached a runner; local Windows results do not substitute for the requested Linux and hosted Windows matrix.
- The final R0 QA decision and review of PR #47 must wait for those two evidence streams.

Unit/regression fixtures provide evidence for noise handling, hidden-tier exclusion, promotion gating, no-field `mechanism_family` fallback to `mechanism_key`, and family reframe selection. They do not substitute for the requested multi-experiment dogfood run.
