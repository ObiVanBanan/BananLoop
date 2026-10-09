# Issue #33 dogfood evidence

| Experiment | Required evidence | Status | Notes |
|---|---|---|---|
| E0 adversarial evaluator sanity | constant, inverse, wrong-objective, and missing-evidence controls | Not run | Existing readiness tests reject missing required outputs and fail constant/inverse separation checks. Independent provenance and the full four-control gate are still pending. |
| E1 bugfix | real baseline failure, candidate regression fix, validation, DEV replay | Not run | The repository test suite passing is not a dogfood campaign. |
| E2 retrieval | fixed corpus/index/model identities and bounded replayable campaign | Blocked pending prerequisites | No retrieval service, corpus, or independent benchmark was provisioned for this implementation. |
| E3 performance | repeated fixed-load latency/throughput with correctness guardrail | Not run | Requires a frozen benchmark workload and campaign after M1–M3. |
| E4 AnimeDiff | frozen seeds/models/runtime, artifacts, paired review | Blocked pending prerequisites | No GPU/A1111 backend or frozen image benchmark was provisioned. |

No baseline, champion, delta, or business-improvement claim is asserted for these experiments.
