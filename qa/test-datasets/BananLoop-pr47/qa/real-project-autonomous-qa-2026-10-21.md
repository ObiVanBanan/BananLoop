# BananLoop autonomous QA on real projects

**Run date:** 2026-10-21 UTC
**BananLoop version:** 0.1.0, source base `2fc59f297da0d10201b23270fdbf998078d1ddf2` plus the working-tree fixes listed below.
**Projects:** Bank Marketing and the fixed product-matching playground.

## Scope and reproduction

The campaign path was exercised from each project checkout with the repository-aware flow, not with a hand-built evaluator loop:

```sh
bananloop doctor --repo . --json
bananloop goal "<the project goal below>"
bananloop status --repo . --campaign-id <campaign-id> --json
bananloop report --repo . --campaign-id <campaign-id>
```

The Bank Marketing benchmark was run from a clean temporary checkout at
`/tmp/bananloop-bank-marketing-clean`, based on `802a39c65eb70bda706600c057c1c6ab49555229`.
The playground baseline was `f00140b1aadbd111285663fbac06b98ed56a8d63`. Doctor found
the project Python runtime, CSV inputs, and repository test/evaluation commands.
The Bank Marketing evaluator enforces chronological DEV/validation/holdout splits,
excludes `duration`, and runs on CPU. The matcher evaluator measures exact top-1
product ID accuracy on fixed inputs.

Goals used:

- Bank Marketing: improve chronological PR-AUC, preserve validation, never tune on
  final holdout, exclude `duration` and post-call information, CPU-only with at
  most two threads and under 60 seconds per evaluation, and leave evaluator,
  frozen splits, labels, and benchmark contract unchanged.
- Matcher: improve top-1 accuracy from DEV evidence, preserve validation, keep
  catalog/labels/evaluator/tests unchanged, hide `data/queries.csv` from
  implementation agents, expose `data/products.csv` read-only, and use at most
  four experiments.

All scores below are aggregate evaluator outputs. Holdout scores were recorded
only after candidate selection; no case-level holdout data is included here.

## Campaign results

| Project / campaign | Baseline → champion | Validation | Final holdout | Outcome |
|---|---:|---:|---:|---|
| Bank Marketing `c-3b6aa0d5c6ae135328cc91fff5cab8b2` | PR-AUC `0.229681 → 0.229681` | `0.340177` baseline; no candidate promoted | PR-AUC `0.492159`, final-only | Four experiments; budget exhausted; none passed validation. Detailed incident and trial log: `bananloop-bank-marketing/BANANLOOP_EXPERIMENT_LOG.md`. |
| Bank Marketing `c-226fc50bd99ad640be98ead83495700a` | PR-AUC `0.229681 → 0.229681` | `0.340177 → 0.340177` | PR-AUC `0.492159`, final-only | Five scientific experiments plus one implementation-blocked attempt; `BUDGET_EXHAUSTED`; no champion promotion. |
| Matcher `c-afbf21b4f610db2db0a97087313f9112` | Accuracy `0.875 → 1.000` (`+0.125`) | `0.375 → 0.750` (`+0.375`) | `0.750`, final-only | Experiment 2 promoted; three later/no-gain trials rejected; `BUDGET_EXHAUSTED`. |
| Matcher visibility retry `c-a8f6e99ce1e11b6af2cce032cc89a511` | Accuracy `0.875 → 0.875` (`0`) | `0.375 → 0.375` | `0.750`, final-only | Four experiments. Three candidates improved DEV by `+0.125` but were rejected on validation; champion stayed baseline. |

Additional historical Bank Marketing campaigns show strategy and stop behavior:

- `c-cde1ec2b26dc1b83f47da23b01d2cb80`: three DEV regressions, then `PLATEAU`.
- `c-775985eb7ab0201e6fc54ea936eb2099` and
  `c-fc92c19229136976deafdcc737a8692c`: `NO_HYPOTHESIS` stop. Their reports
  initially failed to render because of legacy validation metric naming; they
  render after the compatibility fix below.

The first matcher campaign used unit normalization (no gain), then distinctive
token weighting (`+0.125` DEV and `+0.375` validation), which was promoted. Its
remaining trials did not improve DEV. In the visibility retry, related weighting
refinements repeatedly reproduced the same DEV gain but continued to fail
validation. This is evidence that exploration should move away from a mechanism
after repeated validation rejection, even when DEV remains strong.

## Preflight, isolation, recovery, and budgets

- **Preflight/readiness:** Bank Marketing initially failed before model trials.
  Its model API consumed a list of feature dictionaries while the pipeline
  expected a two-dimensional matrix. A later smoke run exposed evaluator result
  schema differences (string split diagnostics and identity-only case records).
  The data adapter and result contract were corrected; the bounded bootstrap
  retry limit was raised from three to five total attempts. Readiness then passed
  and the campaign ran. The original incident and repair are recorded in the
  Bank Marketing log.
- **Isolation:** the saved matcher `CampaignSpec` for `c-a8f6...` confirms
  `agent_visible_context_paths=["data/products.csv"]`,
  `agent_hidden_paths` includes `data/queries.csv` and `.bananloop_eval/**`, and
  `editable_scope=["src/matcher.py"]`. The core now rejects undiscovered and
  label-bearing context paths and rejects any path the goal explicitly says to
  hide. The prior uninitialized campaign `c-bdddd233633f1bebe676bbff1c870a39`
  could not be resumed or stopped because it had no initialized campaign DB; it
  is now inactive, and the fixed retry ran under a new campaign ID.
- **Budget:** the campaigns stopped at their configured limits with no open
  reservations or unknown outcomes. Bank `c-226...` used 18 agent calls, 12
  evaluator calls, and six attempts (five experiments plus one blocked
  implementation). Matcher `c-afbf...` used nine agent calls, eight evaluator
  calls, and four attempts. Matcher `c-a8f6...` used eight agent calls, ten
  evaluator calls, and four attempts.
- **Recovery and reporting:** old campaigns with `validation_pr_auc` and the
  legacy `validation.pr_auc` format now report successfully. `validation_*`
  guardrails are resolved to their base metric on validation and skipped on DEV
  and final holdout. `bananloop status --json` now emits aggregate metrics and
  checks only; it no longer serializes every case record and per-case diagnostic
  into routine status output.

## Fixes made during QA

1. Bootstrap supports a separate allowlist for explicitly requested read-only
   project context, distinct from training data. Unsafe names and hidden paths
   remain denied. The list is included in both Codex and Pi bootstrap contracts.
2. Validation-only guardrails support both current underscore and legacy dotted
   names (for guardrails and non-regression checks), apply only to validation,
   and resolve to the evaluator's base metric.
   This fixes old `status`/report failures such as `missing required metric:
   validation_pr_auc` and `validation.pr_auc`.
3. Status output is aggregate-only to prevent oversized output and accidental
   case-level benchmark disclosure.
4. Existing fixes exercised by the Bank run: five bounded bootstrap attempts,
   record-to-matrix model input compatibility, string diagnostics and
   identity-only cases, numeric diagnostic guardrails, validation metric tier
   routing, and holdout manifest validation.

## Verification

- `python -m pytest -q tests/test_policy.py tests/test_cli.py tests/test_bootstrap.py tests/test_engine.py` — passed after the validation-tier and status changes.
- `python -m pytest -q -o addopts=''` — **294 passed in 98.12s** on the final working tree, including the legacy dotted non-regression test.
- Bank Marketing project tests: `python -m pytest -q` — **1 passed**.
- Matcher playground tests: `python -m pytest -q` — **2 passed**.
- `git diff --check` — passed.
- `bananloop status --repo . --campaign-id c-226fc50bd99ad640be98ead83495700a --json` — completed successfully after the policy and status changes; output contained aggregate metrics/checks and no case rows.
- `build_report` completed for Bank campaigns using both validation metric spellings and the matcher campaigns; the report summaries above were read from durable campaign state.

## Findings and recommendations

- **Fixed in this QA pass:** read-only catalog context was previously forced through a training-only path contract, causing bootstrap to exhaust attempts. Keep the two concepts separate and require explicit discovery plus deny-list checks for context.
- **Fixed in this QA pass:** validation-prefixed guardrails were applied to DEV/holdout even though those tiers do not emit the metric, breaking old status/report paths. Keep tier routing centralized and cover legacy serialized specs.
- **Fixed in this QA pass:** `status` exposed all case records. Keep routine status aggregate-only; use an explicit report/export path for fuller diagnostics and continue excluding protected holdout details.
- **Further work:** two Bank campaigns stopped with `NO_HYPOTHESIS`; capture the agent's empty/invalid proposal receipt in the report and consider a bounded recovery proposal before stopping, while preserving the attempt budget.
- **Further work:** the matcher visibility retry repeatedly pursued DEV-positive variants after validation rejection. Feed validation failure mechanisms into strategy selection and force a materially different hypothesis after repeated rejection.
- **UX clarification:** `campaigns list` can show `active: true` for a terminal `STOPPED` campaign because this field marks the project's selected/default campaign, not a live loop. Add explicit `selected` and `running` fields or rename the marker so users can distinguish selection from execution.

This report is a QA artifact, not evidence that either project's model improved
under every campaign. The only promoted matcher improvement is the separately
identified `c-afbf...` result; both listed Bank Marketing runs retained baseline.
