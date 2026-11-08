# BananLoop v0.2 — role-capability benchmark and offline runner

**Status: EXECUTABLE OFFLINE DEV DIAGNOSTIC + BENCHMARK DESIGN. No independent scored benchmark has been run by this suite.**
**Integration base:** [PR #135](https://github.com/ObiVanBanan/BananLoop/pull/135), `codex/v02-full-integration` head `9c5965d4072f3431ef4d487ebe3bb25e35df62cf`.
**Origins:** [PR #125](https://github.com/ObiVanBanan/BananLoop/pull/125) (role-specific specification) + [PR #133](https://github.com/ObiVanBanan/BananLoop/pull/133) (system-level RPDI-14 protocol, already substantially present in PR #135).

## What is being measured

1. **Role capability** — a pinned role, frozen task and independent oracle.
2. **Role contribution** — a controlled replacement/removal of a role *after* a validated v0.2 Full baseline.
3. **System performance** — **separate** Optimization and Exploratory Research comparisons: BananLoop v0.2 Full vs **iterative** Codex Direct, under [RPDI-14](../../docs/rpdi/rpdi-14-independent-benchmarks.md).

These are different claims, with different eligibility rules. Role scores cannot be pooled into a single system score. Neither an offline fixture nor the PR #135 Titanic diagnostic proves comparative superiority.

## Runtime alignment (PR #135)

`src/bananloop/roles.py` defines the authoritative **`RoleRegistryV1`** and **`TeamManifestV1`** contracts, not a hard-coded global role list. The integrated offline E2E currently exercises these pinned role IDs:

| Canonical role observed in integrated acceptance | Capability question | Example independent evidence |
| --- | --- | --- |
| `researcher` | Does the evidence-backed research proposal respect prior refutations, context limits and scope? | Source support, falsifiability, valid next-action selection |
| `optimizer` | Does a granted candidate implementation improve the frozen objective without scope/regression violations? | Independent test results, candidate/evaluator receipts |
| `independent_verifier` | Does an independently pinned verifier approve only a sound evaluator protocol? | False-approval rate, protocol pin and integrity checks |
| `integration_verifier` | Does independent verification reject invalid candidates and promote only eligible results? | Acceptance/rejection accuracy, promotion invariant checks |

These are observed **fixture/runtime bindings**, not proof that every production deployment has exactly four roles or that each has already been live-scored.

Old PR #125 entries `campaign_bootstrapper`, `proposer`, `selector`, `implementer`, `diagnostic`, `pivot_advisor` are **capability rubrics, not executable canonical v0.2 roles**. In the current integrated flow, campaign creation and evidence-linked selection are Core responsibilities, proposals come from `researcher`, and implementation is exercised through `optimizer`. A supervised pivot requires owner authority. Never auto-rename or award separate role scores to these legacy labels.

See [role-catalog.yaml](role-catalog.yaml) for the proposed benchmark crosswalk and [spec.md](spec.md) for detailed rubrics.

## Executable offline runner

The CLI `python -m bananloop.agent_benchmark` now supports two **non-billable** modes:

- `fixture`: runs a fixed, deterministic **non-LLM** subprocess from this checkout with sanitized environment and only public input passed on stdin. This proves wiring, contract validation and reporting, **not** intelligence or model capability. The subprocess is **not a security sandbox**.
- `replay`: evaluates **already recorded** agent outputs, without running a provider or trusting provider usage claims. Replayed model token/cost data are `UNKNOWN`.

The CLI **never** invokes Codex, Ollama, OpenAI APIs or v0.2 model-role workers. Neither mode accepts VALIDATION/HOLDOUT cases or produces a release-eligible RPDI-14 score. A future *independently authorized*, receipt-verifying provider/broker integration is required for live role evaluations.

Run in a source checkout with `pip install -e '.[test]'`:

```bash
# Generate four real v0.2 registry/team pins and eight synthetic public DEV cases
python -m bananloop.agent_benchmark prepare-demo --out /tmp/bananloop-role-demo

# Execute one subprocess fixture per case; evaluate independently against an oracle file
python -m bananloop.agent_benchmark run \
  --cases /tmp/bananloop-role-demo/cases.dev.json \
  --oracle /tmp/bananloop-role-demo/oracle.dev.json \
  --registry /tmp/bananloop-role-demo/registry.json \
  --team /tmp/bananloop-role-demo/team.json \
  --adapter fixture \
  --out /tmp/bananloop-role-demo/results
```

The `results/` folder contains `results.json` (per-case result-schema records), `summary.json` (per-role completed/errors/exact-match rate) and `report.md`. Paths are not overwritten. Keep local evaluator oracles out of Git and away from agent workspace. This publicly generated demo oracle is **not secret and not suitable for blind evaluation**.

To score previously recorded responses, create a DEV-only JSON file like:

```json
{
  "schema_version": 1,
  "responses": {
    "demo-00": {"selected": "alternative"},
    "demo-01": {"selected": "first"},
    "demo-02": {"result": 9},
    "demo-03": {"result": 2},
    "demo-04": {"decision": "PASS"},
    "demo-05": {"decision": "REJECT"},
    "demo-06": {"decision": "ACCEPT"},
    "demo-07": {"decision": "REJECT"}
  }
}
```

Then run the same command with `--adapter replay --responses /path/to/responses.json --out /path/to/new-results`. Every frozen case ID must have exactly one response.

This executor supports a basic deterministic **exact-match** oracle. It does **not** implement source-grounded research scoring, hidden-test execution, human rubric adjudication or a statistically powered role comparison. Those remain separate evaluator adapters and authorization work; see `evaluation-contract.md`.

## Files and execution boundary

- `schemas/case.schema.json`, `schemas/result.schema.json` — public **design schemas**, not the RPDI-14 scorer protocol;
- `manifest.template.yaml` — unpinned role-trial example, **not an authorized run manifest**;
- `evaluation-contract.md`, `dataset-sources.md`, `pilot-plan.md` — trust, licensing, resource and staged-run decisions;
- `tests/test_agent_capability_benchmark_spec.py` — offline consistency checks against the PR #135 role API;
- `tests/test_agent_benchmark_runner.py` — 12 offline E2E/negative tests for four roles, replay, pins, provenance, splits and oracle tampering;
- `src/bananloop/agent_benchmark.py` and `fixture_worker.py` — runnable DEV-only executor;
- `benchmarks/v02_full/` — the existing **separate** RPDI-14 preregistration, signed accounting, blinding, worker and scoring *infrastructure*.

To run the **offline checks only** from the repository root:

```bash
python -m pytest tests/test_agent_capability_benchmark_spec.py tests/test_agent_benchmark_runner.py -q
```

## Gates

Do not dispatch an actual provider call, allocate blinded tasks or score a trial from this suite without independent, exact-SHA preregistration, legal task rights, signed evaluator/run gates, an enforceable resource/metering boundary and explicit owner authorization. The old "up to eight Codex calls" pilot ceiling is a *planning suggestion*, not permission and not an RPDI-14 limit. No paid runs are authorized.

Keep PRIVATE HOLDOUT/VALIDATION material and oracle outcomes outside agent workspaces and the public repository. Record unknown usage as UNKNOWN, never as zero. Historical v0.1 / exploratory 7:3 results are not admissible for the v0.2 release gate.

**Merge reconciliation:** [docs/benchmarks/benchmark-unification-pr135.md](../../docs/benchmarks/benchmark-unification-pr135.md).
