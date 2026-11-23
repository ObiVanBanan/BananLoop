# BananLoop benchmark smoke matrix

**Purpose:** cheap, sequential integration checks before any scored or broad run. This is a run plan, not authorization to make model calls. Run local deterministic checks first; a smoke pass establishes wiring and evaluator behavior only.

## Matrix

| Suite / role | What it checks | Smoke scope | Evaluator and expected artifacts | Limits and stop rule | Pass criterion |
|---|---|---|---|---|---|
| Synthetic DEV / workflow + role contracts | Role dispatch, schema/output contracts, permissions, artifact hashes, evaluator binding, budget/failure accounting, report generation | 8 public synthetic DEV cases (two per observed canonical role: `researcher`, `optimizer`, `independent_verifier`, `integration_verifier`); deterministic fixture then optional replay | Independent deterministic DEV oracle; `results.json`, `summary.json`, `report.md`; keep oracle evaluator-only. Fixture is explicitly non-LLM and not a security sandbox. | Default 0 model calls. Legacy role-pilot plan proposed no more than 2 short single-case live trials total for wiring, then at most 8 invocations across roles only after explicit separate authorization. These are planning ceilings, not standing permission or RPDI-14 limits. | All cases complete, schemas/pins validate, per-role counts and errors reconcile, report reproduces. Makes no capability or superiority claim. |
| BoardGames / hypothesis and registered-analysis workflow | Researcher selects a registered analysis; Core runs it, persists a hashed result and evidence ID; next iteration receives it; repeats are excluded | Offline smoke: one frozen task (`bg-01-strategies-tournament-win`) and registered `primary_association`; broader offline check: runner exercised against all 10 task definitions. Do not start live series automatically. | Deterministic independent statistic runner: pairwise-complete rows, Spearman correlation with average ties, paired row bootstrap 95% interval, 10,000 draws, seed `20261111`. JSON result plus campaign/evidence receipt. | Historical live pilot envelope: 1h, 5 iterations, 8 agent calls, 10,000-token reservation per call, approval up to 6 calls. Pilot report also describes these configured caps. Treat as legacy operational settings, not current global policy or authorization. Stop on missing registered ID, schema/provenance/evidence failure, evaluator mismatch, deadline/budget exhaustion, or ambiguous worker outcome; never auto-replay ambiguous attempts. | Analysis result is content-addressed and durable, appears in Research Memory for the next request, and cannot be selected twice. A completed iteration/cap is not scientific success. Correlation is associational, not causal; public dataset/questions are not sealed or official DiscoveryBench scoring. |
| AutoResearchExam / system-level adapter and protocol | Task pinning, public DEV adapter/evaluator, Harbor bridge and time-bounded system workflow. System-level comparison belongs to separately gated RPDI-14 Optimization and Exploratory Research tracks. | No-model offline preflight first: verify selected task/version/instruction hashes, adapter/fixture and runner contracts, and prepared-job descriptor digests. For public DEV smoke, one task only, no hidden split, only after separate authorization. The current internal diagnostic uses a frozen catalogue of 12 tasks and 24 arms. The earlier ten-task selection and its dispatcher are a separate preparation path. The current preflight is PREP_BLOCKED. | Task-specific public DEV evaluator/adapter; record task/config/overlay digests, evaluator receipt and campaign artifacts. Public validation endpoint is external trust boundary where applicable; record any public DEV numeric score as a diagnostic metric. | **Authoritative ARE benchmark execution policy is time-only:** one-hour absolute deadline; no candidate-iteration cap (`max_candidate_iterations` absent); technical scheduler/model-call/step ceilings are 1000. These are defensive implementation ceilings; the one-hour deadline is the stopping policy. Legacy ordinary-task driver (`1–5` candidate iterations, up to 20 model calls, 1h) is a different mode and must not be substituted for time-only policy. Request and public-grader timeouts are task- and stage-specific. Record them with the run inputs. BL-042 tracks enforcement of the shared deadline. Full RPDI-14 paired-run resource ceilings still require separately frozen, signed authorization; this matrix is not that authorization. Stop at deadline, invariant/preflight failure, split/evaluator uncertainty, or ambiguous submission; do not retry or replay automatically. | Pinned public DEV evaluator completes and artifacts/pins reconcile; report only diagnostic result. RPDI-14 eligibility requires all independent preregistration, rights, isolation, accounting, signed gates and paired-track conditions. No offline or DEV result is release-eligible. |

## Run order and offline checks

1. Run unit/contract checks and synthetic fixture/replay locally. Do not invoke a provider.
2. Run the BoardGames statistical runner on one registered task; verify its result digest and offline evidence handoff. Broader task-definition checks can remain offline.
3. Run ARE selection/config/preflight validation only. A valid Harbor dry run with `--agent nop` checks task recognition, not the BananLoop agent or grader. Do not dispatch prepared jobs unless their exact configs, evaluator, authorization and resource gates have been reviewed.
4. Any live smoke is opt-in, one at a time, no automatic retries, and must be explicitly authorized for the exact task/config. Never start a full suite automatically after smoke success.

## Interpretation and limits

- Smoke success means integration contracts held for the tested scope; it does not imply model capability, benchmark superiority, official benchmark score, or release readiness.
- Missing usage/cost is `UNKNOWN`, not zero. A local token reservation is not a provider-enforced spend or subscription-quota ceiling.
- Keep public DEV separate from VALIDATION/HOLDOUT. Never expose private task material or oracle outcomes to an agent.
- Legacy numeric limits above document earlier pilot plans/configurations. The ARE time-only policy is a distinct current execution mode; its one-hour deadline is authoritative for that mode. RPDI-14 A/B resource caps are frozen per authorized manifest and cannot be inferred from either legacy limits or this smoke plan.

## Evidence inspected

Prepared against PR #146 source snapshot `be24a260` (read-only), especially:

- `benchmarks/agent_capability_v0.2/README.md` and `pilot-plan.md` (8-case synthetic DEV suite, four observed roles, fixture/replay boundary, proposed live ceilings).
- `benchmarks/discoverybench_boardgames/README.md`, `pilot_manifest.json`, `runner.py`, and `series_runner.py` (registered analyses, statistical method, legacy pilot envelope, failure/replay policy).
- `benchmarks/BOARDGAMES-LIVE-PILOT-2026-11-12.md` (reported pilot configuration/outcomes and non-official caveats).
- `benchmarks/autoresearchexam/README.md`, `harbor_agent.py`, `campaign_binding.py`, and `task-selection.json` (pinned shortlist, ordinary vs time-only modes, task/config requirements).
- `docs/rpdi/rpdi-14-independent-benchmarks.md` (separate signed preregistration/resource/evaluator gate; lack of infrastructure evidence does not authorize a scored run).

## Offline verification

At `be24a260`, 40 tests passed across the Synthetic DEV fixture runner and metrics,
the BoardGames registered-analysis and offline series runners, and ARE task
selection. The exact-SHA ARE preflight also ran offline and recorded
[PREP_BLOCKED](../../benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-22-be24a260.json).
No provider or scored benchmark ran during this verification.
