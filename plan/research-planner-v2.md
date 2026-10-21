# Research Planner v2 — integration audit and design

Issue: [#42](https://github.com/ObiVanBanan/BananLoop/issues/42)
Related: #28, #29, #30, #33, #40
Status: pre-implementation audit and design baseline; implementation is integrated into PR #43. This document preserves the original architecture findings and baseline.

## Integration point checked

This work starts from `feat/issue-33-evidence-aware-research` at `6328c9987a8ebff189295f45570b620cd514bf7a` (PR #35), the current open research-v1 line descended from PR #34 and `feat/autopilot-campaign-bootstrap-v1` (PR #32). This is the line named in #42 and it contains `research.py`, the research contracts, DEV-only capsules, budgets and explain/report surfaces.

PR #41 (`codex/issue-40-evidence-driven-diagnosis`, `4ea6c0249ed861136d32ae20304b23e79e29fd33`) is open/draft and stacked on PR #36, whose base is `fix/friendly-validation-recovery-20261013`; it is a sibling line, not an ancestor of PR #35. #40's generic evidence/budget changes are therefore not in this base yet. Reuse its public DEV trace contract when that line is integrated; do not copy its RAG diagnosis implementation into this planner slice. The branch used here is isolated as `codex/issue-42-research-planner-v2`.

## Existing behavior versus #42

| Area | Already present on the PR #35 line | Gap for v2 |
|---|---|---|
| Research memory | DEV-only outcomes, mechanism evidence, recent lessons/capsules, `exploit` / `explore` / `reframe`, remaining-budget estimate | Context is still a short recent-history window; it does not contain a set of live alternatives |
| Proposal contract | One `Hypothesis` per `propose` action; `new/refine/retry/supersede`, prior experiment refs, novelty rationale | No bounded candidate portfolio or stable IDs for unselected candidates |
| Core gate | Grounding, deterministic scope/materialization checks, redundancy check, budget and evaluation policy remain core-owned | No core selector that compares multiple admissible proposals or records why alternatives lost |
| Persistence/lineage | Selected hypothesis is in the experiment row; `HYPOTHESIS_SELECTION` records mode, rationale and duplicate result; history can cite prior ordinals | No durable record of all considered candidates, rejection reasons, or candidate parent links beyond selected hypothesis refs |
| Plateau | Research mode changes using completed scientific DEV outcomes; the final proposal opportunity can be in `reframe` mode before the stop threshold | One proposal can still repeat a failed mechanism; no portfolio-level strategy choice or criterion-level explanation |
| Safety/reporting | Holdout/validation are excluded from research state; deterministic ACCEPT/REJECT and champion snapshot remain core-owned; reports show research state and selection record | Report cannot explain why a candidate beat other candidates, which were blocked, or their bounded costs |

No proposal or evaluator code currently grants the LLM authority over acceptance, campaign limits, frozen evaluation assets, or champion promotion. The v2 design keeps this boundary intact.

## Deterministic baseline

I ran a CPU-only toy repository through the existing engine using a deterministic in-process `AgentAdapter` and the existing `tests/test_engine.py::make_engine` fixture. Three proposals deliberately repeated the `same-cache-family` mechanism, each produced a measurable change too small to pass the frozen minimum-improvement threshold, and the candidate always started from the current champion. No external model or API was used.

| Observation | Baseline result |
|---|---:|
| Propose calls / proposals considered | 3 / 3 |
| Same mechanism family | 3 of 3 (two repeats after the first) |
| Planning modes delivered to proposer | `exploit` → `explore` → `reframe` |
| Experiments / outcomes | 3 / all `REJECTED: PRIMARY_NOT_IMPROVED` |
| Agent calls | 6 (one propose + one implement each) |
| Evaluator calls | 4 (baseline + 3 DEV) |
| Stop | `STOPPED / PLATEAU` after three genuine no-effect DEV trials |
| Terminal research explanation | Reframe selected after 3 consecutive DEV non-improvements; evaluator trust remains `UNVERIFIED` |
| Source repository changed | No |
| Alternatives considered/persisted | None |

This establishes a specific baseline: the strategy label changes, but the core sees only the single proposal supplied by the agent. There is no recorded alternative to select instead of another same-family experiment. It does not establish that a different mechanism would improve the metric.

Run evidence: local runner `C:\Users\theso\Documents\Codex\2026-10-15\new-chat\outputs\issue42-baseline.py`; log `C:\Users\theso\Documents\Codex\2026-10-15\new-chat\outputs\issue42-baseline-20261018.log` (2026-10-18 11:45 UTC). The runner uses only a temporary toy repo and stub agent/evaluator.

## Proposed compatible extension

1. **Portfolio response with legacy fallback.** Keep the existing `propose` action ID and permit either a portfolio of 2–3 candidates (hard cap 4) or the current single `Hypothesis` response. Normalize the latter into a one-item portfolio. Avoid a second model call for ranking.
2. **Typed candidate record.** Validate candidate ID, hypothesis genealogy, DEV evidence/capsule references (or explicit `INSUFFICIENT_EVIDENCE`), suspected failure mode, predicted observable metric/diagnostic, falsification or discriminator, area/scope eligibility, estimated call/work cost, budget feasibility, rationale and uncertainty. Mark interpretation as agent inference; store observed evidence separately.
3. **Core-owned admissibility and deterministic selection.** Reject malformed, duplicate-without-new-discriminator, out-of-scope, untestable, or unaffordable candidates before implementation. Rank admissible candidates with an explainable lexicographic policy: relevant permitted DEV evidence; novelty against failed mechanisms; discriminating/falsifiable test; strategy alignment; then lower estimated cost; stable candidate ID as the final tie-break. Do not score invented gains or confidence.
4. **Durable plan/replay.** Persist the full normalized portfolio, admissibility results, selected candidate, and criterion-level reason under the existing propose action plus a core event keyed to the experiment. The selected candidate continues through existing `Hypothesis` and stage contracts. Resume after proposal must reuse the saved response/selection without another planning call or duplicate charge.
5. **Lineage and learning.** Keep implementation based on the validated champion. Add logical parent/mechanism-family links and concise `observation → supported/refuted/unknown → next discriminator` records only from genuine DEV outcomes. Reuse capsules and existing ancestry; do not make a Git branch tree or second research store.
6. **Reports and A/B proof.** Extend JSON/Markdown reports to show considered, blocked and selected candidates, why, evidence links, bounded call/work estimates, and unavailable costs as `unknown`. Compare legacy single-proposal and portfolio modes on frozen toy bugfix and CPU-performance (or retrieval) fixtures with identical seed and budgets; report duplicate proposals, scientific trials, distinct mechanisms, strategy switches, calls, best DEV value, and stop reason.

The selector is a decision aid over already validated core constraints. It cannot alter evaluator/protocol, metrics, inputs, guardrails, holdout, budget, candidate base snapshot, or ACCEPT/REJECT policy.

## First implementation slices and acceptance gates

- Slice A: typed bounded portfolio and compatibility normalization; core validation/selection; durable selection record; tests for duplicate, scope, feasibility, deterministic tie-break and old single-output agents.
- Slice B: logical lineage and bounded learning; plateau/reframe behavior; status/report/explain; resume safety and isolation tests.
- Before claiming completion, run all nine #42 scenarios, both CPU-only fixture domains, the fixed-budget A/B, full pytest, and available CI. Keep #40/RAG campaign work out of these tests; do not spend real model credits.

Unresolved at this audit: whether candidate portfolios should live in the `propose` response or a separate internal `plan` stage. The preferred first slice is one bounded `propose` response to keep call accounting and compatibility simple; tests must prove replay is durable before accepting that choice.

## Audit validation

- Deterministic repeated-no-effect baseline runner completed: 3 same-family proposals, 6 agent calls, 4 evaluator calls, `STOPPED/PLATEAU`; source tree remained clean. See the runner and log listed above.
- Existing research contract tests on the selected PR #35 base: **10 passed** (`tests/test_research.py`). Log: `C:\Users\theso\Documents\Codex\2026-10-15\new-chat\outputs\bananloop-issue42-research-20261018.log`.
- `git diff --check` passes. No production code changed in this audit slice.


## Integration note — 2026-10-18

The pre-change audit from draft PR #44 is preserved in this file and integrated into the single Issue #42 implementation line, PR #43. Its deterministic baseline remains historical evidence (3 same-family proposals, 3 no-effect trials, 6 agent calls, 4 evaluator calls, STOPPED/PLATEAU, no alternatives persisted); it is not presented as a post-change A/B result. PR #43 now includes the implementation and follow-up fixes for the Codex portfolio schema, minimum actual resource costs, stable capsule references, and immediate durable portfolio resume. The two-project empirical comparison remains an open validation item because the Windows sandbox currently blocks fixture Git clones and Codex CLI state access.
