# RPDI-12 Pivot Execution Contract

**Status:** acceptance contract; implementation remains incomplete until every required path below is implemented and verified.

## Decision

An approved pivot is an immutable authorization for one exact child execution plan. Human approval authorizes only the plan bound into the proposal digest. The execution path loads that plan and its durable decision from the parent campaign database; a caller cannot replace or extend it at execution time.

For Optimization handoff, verification completes against the frozen protocol first. A new Optimization campaign is then created with a child-bound copy of the approved protocol evidence. The existing campaign's mode, spec, and frozen evaluator protocol are not mutated.

## Required invariants

### Proposal and execution-plan binding

- A versioned, immutable execution plan binds every execution-affecting child-spec field, exact normalized repository-relative changed paths, capabilities/autonomy policy, and the complete canonical resource vector, including optional resource dimensions.
- Proposal digest and ID include the plan when present. Legacy proposals without a plan keep their existing serialized digest/ID bytes and remain readable for audit, but cannot be executed.
- Generated lineage fields are excluded from the plan to avoid a digest cycle. Core adds lineage deterministically from the persisted parent, proposal, decision, and allocation, then verifies the resulting child spec against the approved plan.
- Execution accepts no child-spec or approval override. A retry with the same idempotency key and same persisted plan resumes the same saga; reuse with different content conflicts.
- Requested changed paths are canonical, repository-relative, contained in the child allowed scope, and equal to the approved path set. Protected evaluator, validation, holdout, secret, credential, and other hard-banned paths remain forbidden.

### Authorization modes and budget

- Full Auto remains allow-only: path scope, capabilities, child limits, and all resource dimensions stay within the frozen parent scope and available ancestor envelope.
- Supervised may widen only the paths, capabilities, and resource dimensions explicitly stated in the immutable approved plan. Approval alone cannot create budget: the saga must reserve the expanded amount from currently available parent/ancestor funds before child allocation.
- Absolute project limits and protected-path rules cannot be overridden. Parent spec and approval hashes remain immutable.
- Missing, stale, mismatched, rejected, or unpersisted approval fails closed before escrow or child allocation.

### Application, API, CLI, and recovery

- The application service loads the persisted proposal, execution plan, and durable decision by stable proposal ID/digest, validates parent campaign/spec identity, and starts or resumes the saga without caller-supplied plan data.
- API execute/resume requests carry only proposal identity and an idempotency key; they do not accept a child spec or approval body.
- CLI execute/resume uses the same application service and cannot bypass its checks.
- Concurrent calls create one durable intent and one allocation. Recovery tests cover process restart at each saga boundary, stable replay, cancellation, stale approval, and conflicting idempotency-key reuse.
- The child remains non-dispatchable until the durable parent activation transition is proven. Cancellation and activation are mutually exclusive; escrow is released exactly once for cancelled no-work children.
- Terminal settlement is derived from the registered child database and remains consistent with the child allocation and campaign status.

## Compatibility requirements

- Preserve existing CampaignSpec approved hashes and v1 hashes.
- Preserve the historical scope-less v2 hash and optional approval-pin compatibility.
- Preserve proposal and decision identities for legacy records. Legacy proposals lacking a bound plan may be inspected but must not start a child.
- Add fixtures that verify old stored records load with unchanged identifiers and hashes.

## Acceptance evidence

| Requirement | Required evidence |
| --- | --- |
| Plan binding and legacy compatibility | Contract tests for digest inclusion with a plan, unchanged legacy digest/ID without one, tamper rejection, exact path/capability/spec matching, and all budget dimensions |
| Full Auto containment | Tests reject every widening attempt before escrow, including optional resource dimensions |
| Explicit Supervised expansion | Application/API/CLI tests approve a plan that widens named paths, capabilities, and funded budget; reject any widening omitted from the approved plan; prove insufficient parent/ancestor funds fail before allocation |
| Durable execute/resume | App, API, and CLI tests load persisted approval and plan; no request override fields; same-key replay, conflicting reuse, concurrent calls, restart recovery, and cancellation races |
| Saga safety | Fault injection at each durable boundary; child dispatch gate; exact-once escrow settlement; authoritative terminal-state settlement |
| Optimization handoff | Verification precedes child creation; a distinct Optimization campaign receives child-bound frozen protocol evidence; source campaign mode/spec/protocol stay unchanged |
| Compatibility | Existing schema/hash fixtures reopen and retain byte-for-byte legacy identities |
| End-to-end DoD | Exact-head CI on supported Linux/Windows Python versions, technical review, and RPDI-12 DoD checklist updated with evidence |

RPDI-12 remains **Draft / incomplete** while any row lacks implementation and passing evidence. PR #130 must not be marked ready until all rows are satisfied. This document records acceptance criteria; it is not evidence that the criteria have passed.
