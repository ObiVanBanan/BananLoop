# RPDI-14 — owner inputs and unresolved run gates (2026-11-05)

> Historical snapshot from PR #133. PR #135 subsequently delivered additional Linux integration; this record is **not** a current completeness audit or run authorization. Recheck all gates against the final integrated SHA before scoring.

**State: PRELIMINARY OWNER PREFERENCES — NOT A FROZEN MANIFEST, SPEND APPROVAL, OR RUN AUTHORIZATION.**
This record intentionally excludes the owner's private live subscription-quota balance.
Do not commit credentials, tokens, hidden task material, personal usage-dashboard screenshots,
or unsanitized final outputs to this public repository.

## Received owner preferences

| Subject | Owner choice | Protocol interpretation |
| --- | --- | --- |
| Shared model | GPT-6 Luna for BananLoop v0.2 Full and Codex Direct | Candidate Codex model ID: `gpt-6-luna`; exact provider/model version, CLI version and reasoning settings must be pinned and confirmed on the execution host before freeze |
| Authentication/billing | Codex CLI using an existing ChatGPT Plus subscription | Subscription access only; no separately billed API key, purchased credits, paid reset, or additional charges are authorized |
| Duration | Roughly one hour | Provisional **3,600 seconds total across both arms and both tracks**, not 3,600 seconds per arm/task. A feasible per-arm/track allocation and overhead must be fixed before freeze |
| Token and call budgets | Observe and report actual usage; owner does not want to specify arbitrary limits | **Not compatible with current RPDI-14 gate without a design change or independently established enforceable caps**; never fill missing measured usage with zero |
| Spend budget | No numeric additional paid-spend budget supplied | **NOT APPROVED**. No external provider invocation or paid experiment may be launched. Subscription plan availability is not a hard ceiling, and a provider receipt/usage truth has not been established |
| Launch authorization | Only after all RPDI-14 prerequisite gates are verified | Conditional intention, **not** signed run authorization or pre-approved task assignment |
| Candidate datasets | Examine repository material | Task-level rights and fresh sealed evaluation data must be established before candidate pool is eligible |
| Human review | Owner wants a final archive of artifacts to inspect | Owner's retrospective review is useful, but **does not satisfy the independent benchmark-operator/scorer/preregistration reviewer roles** required by the current benchmark contract |

No claim of a completed independent run, independent operator, provider metering verification,
task licensing clearance, or formal statistical power is made.

## Source inventory and eligibility

Repository-level `LICENSE` is MIT for BananLoop software; it does **not** automatically
establish rights to third-party benchmark tasks or hidden evaluation data.

| Candidate source found in the repository | Actual evidence | Eligibility for RPDI-14 |
| --- | --- | --- |
| `examples/calibration/mini_matcher` | Self-authored small CPU fixture; 16 DEV cases and an intentionally weak baseline. Existing selector trials use it | **Calibration only**. Reused DEV cases; no documented sealed VALIDATION/HOLDOUT |
| `examples/calibration/proposer_ab` | Public synthetic task, fixed DEV context, and human-rating sheet; previous model trials present | **Calibration only**. Hypothesis-scoring pilot, not a matched Full-vs-Direct hidden-outcome task suite |
| `examples/issue42` | Reproducible local deterministic CLI dogfood fixtures | **Smoke only**. Deterministic command agent, not a benchmark of both GPT-6 Luna arms |
| `examples/rag_tender` | Evaluator adapter/bridge README | **Not a dataset**. Would require task data, separate rights and sealed evaluator |
| `benchmarks/operator_roadmap_v1/suite_manifest.json` | Planned AutoResearchExam v1.1 ten-task roster for `gpt-6-luna`; older partial campaigns | **Blocked**. Third-party use/redistribution rights unverified, historical task exposure, and no fresh independent sealed evaluation shown |
| `benchmarks/selector_ab_v2` | Five-pair LLM-vs-deterministic selector DEV fixture; token accounting unknown | **Historical only**. Not BananLoop Full vs Codex Direct and no protected HOLDOUT |

**No qualifying Optimization and Exploration task sets are selected or frozen.**
Do not treat the existence of a task in GitHub as clearance of its license, dataset
rights, test confidentiality, or statistical suitability.

## Blocking compatibility findings

1. `benchmarks/v02_full/prereg.py:ResourceCaps` requires positive numeric
   `input_tokens`, `output_tokens`, `agent_calls`, `evaluator_calls`,
   `experiments`, `wall_seconds`, and `spend_microusd` plus nonnegative
   cached tokens/retries. A subscription-only `$0 extra API spend` preference
   cannot honestly be encoded by inventing a positive cash ceiling or falsely
   representing spend as zero provider usage.
2. `benchmarks/v02_full/dispatch.py` and `provider_broker.py` require an
   authoritative one-call provider receipt verifier, enforceable provider
   token/cost ceilings, hard timeout and cancellation. The project has no
   verified provider adapter that exposes those guarantees with ChatGPT
   subscription authentication. CLI `/status` is an owner-visible quota
   observation, not a signed per-action provider receipt.
3. Current `docs/rpdi/rpdi-14-independent-benchmarks.md` requires both
   RPDI-00–13 DoD, exact-head integrated CI, segregated evaluators, task rights,
   statistical review, distinct operator/scorer roles, and signed run gates.
   RPDI-13 remains draft/integration-incomplete at the time of this note.
4. One hour total may be insufficient for the preregistered number of
   independent usable task pairs in two separate tracks. Do not lower the
   sample-size target after looking at outcomes or pool Optimization and
   Exploration into one win rate.

**Decision needed before a paid or quota-consuming launch:** either implement
a genuinely enforceable subscription-aware provider/metering pathway accepted
by the signed RPDI-14 protocol, or explicitly create a separate versioned,
non-qualifying *diagnostic* subscription experiment with different claims
and non-authoritative receipts. Neither option is authorized to run yet.

## Final artifact delivery request

After a valid authorized run, provide a **local archive to the owner**, not a
public GitHub upload. At minimum include:

- A sanitized `README.md`, exact branch/SHA/config/model/reasoning and
  frozen preregistration manifests and hash indexes.
- Task provenance and rights register (without hidden case material), task
  selection/exclusions and randomization evidence with confidential mappings
  kept separately until scoring is final.
- Pair-level final outputs, anonymized score inputs, failed and stopped trials,
  timing, action/tool/evaluator/agent usage events, provider receipts or
  explicit UNKNOWN fields, and signed resource-ledger chain.
- **Separate** Optimization and Exploratory Research scores, pairwise deltas,
  confidence intervals, wins/ties/losses, eligibility and deviations.
- Signed isolated scorer evidence, audit trail, anonymization checks, and a
  separately controlled unblinding record.
- Owner review template for independent qualitative analysis.

Do **not** include API credentials, OAuth tokens, private signing keys, raw
VALIDATION/HOLDOUT contents, unredacted PII, or developer secrets in the
owner-facing archive. Preserve any restricted evidence through a separate,
access-controlled handoff if needed. Archive creation follows an actual
authorized run, not this preference registration.

## Next resolution sequence

1. Finish and reverify RPDI-13 integration (including Research Ledger
   provenance API compatibility); obtain exact-head RPDI-14 CI.
2. Choose and clear *fresh* Optimization and Exploration tasks/rights and
   sample-size/inference design; check whether a one-hour total can support it.
3. Prototype/offline-review a subscription-aware provider meter against
   strict fail-closed requirements; never test by consuming owner quota
   without a separate permitted diagnostic invocation.
4. Establish enforceable comparable limits and distinct independent
   statistical, security/evaluator and benchmark-operator attestations.
5. Obtain a new explicit authorization to start using the frozen manifests,
   measured resource envelopes and supported runtime; only then assign tasks,
   run paired arms and independently score.
