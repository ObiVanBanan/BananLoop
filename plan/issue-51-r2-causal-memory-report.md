# Issue #51 R2 causal memory QA

## Scope

R2 extends the portfolio and adaptive budget planner delivered in #42. This
change adds durable, DEV-derived causal lesson states and typed structural
intervention signatures; it does not reimplement portfolio selection or #40's
budget drift audit.

## Causal lesson state

- Each lesson records an observed DEV outcome separately from causal status:
  `inferred`, `refuted`, or `unknown`.
- Lessons carry confidence, safe `exp:<ordinal>` evidence references, and a
  discriminating next test.
- Analyst capsules marked `insufficient_evidence`, and mechanisms explicitly
  beginning with `Unknown`, cannot become inferred causes. The aggregate-only
  case remains `unknown` with confidence 0.
- Only experiments classified as scientific DEV trials contribute. Operational
  failures and validation/holdout decisions do not establish refutations.

## Structural intervention memory

- Hypotheses can declare a controlled `intervention_type` and
  `algorithmic_layer` plus existing `files_or_area` paths.
- The core strips function/line locators, normalizes and sorts paths, then hashes
  the typed fields and paths into a stable signature. Agent-provided mechanism
  keys/families are not part of that signature.
- `ResearchState.structural_interventions` stores signature, type, layer, paths,
  experiment ordinals, DEV outcome counts, and the most recent DEV outcome.
- A candidate that repeats a signature whose latest scientific DEV outcome was
  no effect, regression, or guard rejection is rejected even if it renames its
  key/family or path locator. A different intervention type remains eligible.
- Legacy hypotheses without typed fields remain readable and produce no
  structural signature; existing key/family protections continue to apply.

## Budget and durability

The existing core checks remaining experiments, attempts, agent calls,
evaluator calls, and work units before selecting a candidate. This PR reuses
those gates and does not duplicate #40's effective-budget accounting. Research
state is rebuilt from persisted experiments on resume, keeping refutations and
safe references durable without replaying completed actions.

## Verification

- Structural-signature tests cover renamed keys/families, normalized paths,
  distinct interventions, failed DEV outcome gating, and exclusion of a
  validation-only secret from planner state.
- Causal-state tests cover inferred capsule evidence, aggregate-only unknowns,
  safe evidence references, and a discriminating next test.
- Legacy hypothesis parsing and full campaign/spec compatibility are covered by
  regression tests.
- Full repository test suite and `git diff --check` are run before PR creation.

## Limits

Signatures establish equivalence only for the declared controlled intervention
type, layer, and normalized paths. They cannot prove semantic equivalence for
unrecognized operations or dishonest declarations; this is a deterministic
core guard over supported signatures, not a code-semantics theorem prover.
