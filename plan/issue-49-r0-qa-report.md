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
| Logic Gold v0.3 | `run_gold.py --json`, with `PYTHONPATH` set to this worktree's `src`. Imported modules and Git revision were verified by `--env`. | 51 passed, 0 failed on `f5f7101071cbcb68d281fa54adea72b96d0ab111` |

The full run was performed after the runtime probe changes; the report-only edits do not affect tested code. The separate CI configuration commit `cc3302185bff88cbecf562d59ea1bf42d2d23ac9` sets matrix `fail-fast: false`, so one startup failure no longer hides the independent job outcomes.

Gold v0.3 confirmed all 51 curated policy/research vectors and its four source-contract checks (`validation_tier_scoping`, `numeric_diagnostics_for_guardrails`, `symmetric_dev_noise_band`, `failed_dev_families_in_research_state`) against the current PR head. It is a synthetic deterministic oracle: its 18 campaign E2E journeys are specifications, not tests run here, and it does not prove agent behavior or Bank Marketing campaign effectiveness.

## GitHub Actions

Runs [39649047084](https://github.com/ObiVanBanan/BananLoop/actions/runs/39649047084) and [39649477862](https://github.com/ObiVanBanan/BananLoop/actions/runs/39649477862), revisions `ef3d519` and `1fb34e4`, each report all four matrix jobs failed. The GitHub Actions API shows every job completed in about two seconds with `runner_id=0`, an empty `runner_name`, and `steps=[]`. The failure occurred before runner assignment and before checkout/setup/pytest. The prior fail-fast-disabled run [39647659677](https://github.com/ObiVanBanan/BananLoop/actions/runs/39647659677) showed the same pattern. Log downloads return `BlobNotFound`.

**Conclusion:** the Linux/Windows CI gate is not green and cannot be counted as executed. Available evidence points to hosted-runner startup failure rather than a test failure; a runner-assigned rerun is required to establish CI results.

## R0 requirements still open

- Existing tracked QA reports document real Bank Marketing campaigns and baseline metrics; campaign execution was **not** performed in this turn. `qa/real-project-autonomous-qa-2026-10-21.md` reports one campaign with five scientific experiments and another with four total experiments (their scientific/operational breakdown is not given). `qa/failure-to-hypothesis-current-project-2026-10-21.md` reports a two-experiment diagnostic campaign with a DEV failure-capsule → distinct hypothesis family → DEV test sequence. The first related run had two scientific experiments before an implementation-blocked third attempt.
- The underlying campaign databases/receipts and isolated Bank Marketing snapshots cited by those reports are not present in this worktree, so their experiment counts and lineage cannot be independently replayed here. No tracked report documents a Bank Marketing retry/resume of that chain. Therefore the full R0 end-to-end acceptance remains unverified despite the historical report evidence.
- Campaign execution was not performed in this turn; the operator's standing preference keeps campaign execution separate from preparation.
- No cross-platform GitHub Actions run has reached a runner; local Windows results do not substitute for the requested Linux and hosted Windows matrix.
- The final R0 QA decision and review of PR #47 must wait for those two evidence streams.

Unit/regression fixtures provide evidence for noise handling, hidden-tier exclusion, promotion gating, no-field `mechanism_family` fallback to `mechanism_key`, and family reframe selection. They do not substitute for the requested multi-experiment dogfood run.
