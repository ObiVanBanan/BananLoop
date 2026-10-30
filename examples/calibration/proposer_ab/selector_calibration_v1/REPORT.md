# Selector Calibration v1 — historical portfolio replay

No LLM calls were made. Frozen DEV fixture spec, research state, and experiment history came from `runner.py`; the six raw proposer responses and original selector decisions are preserved under `results/`. The old ID-sort replay matches each saved original decision before rename probes.

## Rename reproduction

| Run | Original winner | Winner after ID renaming | Changed? | Reason |
|---|---|---|---:|---|
| exploit-1 | `negation-aware-overlap` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |
| exploit-2 | `technical-alias-expansion` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |
| exploit-3 | `synonym-lexicon-matching` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |
| reframe-1 | `synonym-canonicalization` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |
| reframe-2 | `synonym-alias-lexicon` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |
| reframe-3 | `synonym-alias-expansion` | `charlie` | yes | all substantive ordering criteria tied; the final lexical candidate ID decided the winner |

## Old vs new selector

Criteria order is strategy-aware novelty, DEV evidence, falsifiability, budget feasibility, estimated cost, then the old ID or the new neutral content hash.

| Run | Old winner (mechanism) | Old criteria (novelty/evidence/falsif/budget/cost/tie) | New winner (mechanism) | New criteria (novelty/evidence/falsif/budget/cost/hash) | Mechanism changed? | Gates preserved? | New ineligible winner? |
|---|---|---|---|---|---:|---:|---:|
| exploit-1 | `negation-aware-overlap` (negation-aware-overlap) | `-1/0/1/1/-4/negation-aware-overlap` | `compositional-order-scoring` (compositional-order-scoring) | `-1/0/1/1/-4/9e9e5227f6f3e3633d4703cab1036caa18baa385fb6824333692ddb297f34838` | yes | yes | no |
| exploit-2 | `technical-alias-expansion` (technical-alias-expansion) | `-1/3/1/1/-4/technical-alias-expansion` | `technical-alias-expansion` (technical-alias-expansion) | `-1/3/1/1/-4/e6e7970d5cf0028c17d269cfb0d11618181c551df7a7a24dc10c92a0b688a62f` | no | yes | no |
| exploit-3 | `synonym-lexicon-matching` (synonym-lexicon-matching) | `-1/3/1/1/-3/synonym-lexicon-matching` | `acronym-expansion-matching` (acronym-expansion-matching) | `-1/3/1/1/-3/fd6149ff3dd0bba6c9f80115f20106bd7a219ebcc45d02a71b0429a2184279d3` | yes | yes | no |
| reframe-1 | `synonym-canonicalization` (synonym-canonicalization) | `1/3/1/1/-3/synonym-canonicalization` | `ordered-composition` (ordered-composition) | `1/3/1/1/-3/f0628a83e4aaa69269848572fa42c2304397f0598416037d0239a6479897711d` | yes | yes | no |
| reframe-2 | `synonym-alias-lexicon` (synonym-alias-mapping) | `1/3/1/1/-3/synonym-alias-lexicon` | `synonym-alias-lexicon` (synonym-alias-mapping) | `1/3/1/1/-3/dcb83507472b8e81e8166537371d6085fc3cdc5c24228433739723f117c90660` | no | yes | no |
| reframe-3 | `synonym-alias-expansion` (synonym-alias-expansion) | `1/0/1/1/-3/synonym-alias-expansion` | `order-sensitive-composition` (order-sensitive-composition) | `1/0/1/1/-3/bbb365de2e2be4f6dd73205a04613052cd93dea76d306eb824db41a621e59a96` | yes | yes | no |

Candidate-order permutation was checked for each run; see `replay.json`. The SHA-256 digest only resolves exact ties and has no scientific interpretation.
