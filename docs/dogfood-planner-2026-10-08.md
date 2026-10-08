# DEV planner dogfood: competitor analogue campaign

Campaign: `c-e199085523d775bf9a7696db6def8cbc`. This note uses only aggregate DEV results and durable proposal metadata. It does not include benchmark cases, expected answers, validation, or holdout evidence.

| Trial | Planner mode | Proposed mechanism | Top-1 | Initial recall | Rerank recall | DEV result |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Baseline | — | — | 30/100 | 63/100 | 64/100 | — |
| 1 | exploit | Wider hybrid candidate pool (20 to 40) | 30/100 | 63/100 | 64/100 | No measured effect; rejected |
| 2 | explore | Technical attribute query normalization | 30/100 | 63/100 | 64/100 | No measured effect; rejected |
| 3 | reframe | Character n-gram candidate generation | 33/100 | 63/100 | 65/100 | Partial gain; rejected |

The campaign's baseline receipt reports rerank recall **64/100** and **45** MARSHAL cases, while the earlier experiment note quoted **63/100** and **42**. The analysis above uses the frozen campaign receipt; the discrepancy deserves a separate measurement review.

The proposer did refer to earlier attempts in free-text rationale: trial 2 named trial 1's lack of effect, and trial 3 named both earlier attempts. Its structured research state contained outcome counts and mechanism keys, but no ranked summary of the allowed DEV failure counters or measured effect by mechanism. The baseline exposed 37 retrieval misses and 32 rerank selection errors. Trial 3 reduced rerank selection errors to 29 while retrieval misses stayed at 37.

The frozen spec required a **0.10** primary metric improvement per promotion. Trial 3 gained **0.03**, so the policy correctly rejected it; other target guardrails also remained unmet, including a TEMPER initial-recall regression. The research classifier nonetheless recorded `DEV_NO_EFFECT`, and the store counted a third consecutive non-improvement. `plateau_experiments=3` then stopped the campaign with one experiment still available.

The planner change derives a bounded summary from agent-visible aggregate failure metrics, allowlisted numeric diagnostics, and sanitized DEV failure capsules. It records each prior mechanism's visible metric changes, requires a new hypothesis to name a failure signal and causal explanation, and requires a reference to prior DEV evidence when present. A rejected candidate with positive DEV primary delta is classified as partial progress and resets the plateau streak. Promotion rules and the frozen campaign spec stay authoritative. The existing stopped campaign is not reopened by this code change.
