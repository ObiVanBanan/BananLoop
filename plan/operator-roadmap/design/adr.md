# ADR: Operator interface and bounded research extensions

## Status
Accepted for implementation after the bounded framework comparison. The selected shell and transport still require production integration and live acceptance checks.

## Task
Provide one local researcher with an English terminal interface to observe the goal, cycle, live work, hypotheses, progress, budget and epochs, communicate with an agent and control a campaign. Extend the research roadmap with approved architecture pivots, composite hypotheses, stronger evaluation and mutation testing.

## Research Inputs
- `../research_report.md`: existing engine, SQLite state, lifecycle, evaluation protections, Codex and Pi adapters, CLI and tests; remote freshness and live integration are unverified.
- `../../../docs/ROADMAP.md`: existing research preview commitments, proposals and non-goals.
- User decisions in this chat: concept 1 combined with concept 3; Codex first; one local operator; model errors pause with current saved state and explicit resume; change of goal or evaluation rules requires a newly agreed plan; platform priority Windows Terminal.

## Context
The engine already supervises bounded experiments with durable stage transitions and receipts, but `STOPPED` is terminal; current stop is not a resumable pause. Attempts begin from the champion snapshot. Codex role invocations are ephemeral, not an established persistent interactive conversation. The interface's six stages are not a one-to-one representation of stored engine stages. Validation and holdout results must stay out of adaptive agent context. Existing action retry safeguards protect against unknown external outcomes.

## Decision
Separate the operator surface from the campaign engine. The engine and its persisted state remain authoritative for experiment decisions, budgets, evaluation and control; the UI displays a read-only projection and sends typed requests through a supervised local control boundary. Use Codex as the first conversational backend, with role models resolved from campaign configuration. Keep existing CLI commands and provider adapters available.

The bounded comparison selected a project-local Pi extension for the first full-screen operator shell. It renders a custom component and passes input through Pi; BananLoop remains the campaign engine. A narrow typed bridge will connect the extension to a separate Codex app-server process for the operator conversation. The existing per-role `codex exec` adapter remains available for research work. Pi's own model login is not assumed to authenticate Codex. The standalone line-oriented spike did not provide equivalent full-screen interaction. `plan/operator-roadmap/feasibility/README.md` records the comparison and limits. The local Codex app-server responded to read-only initialization, model listing and account state checks; no model turn was run. Production work must validate integration, actual model access and errors, Windows Terminal resize and packaging. Failure of those checks reopens the framework decision.

Use a durable, resumable **pause** operation distinct from the existing terminal `stop`. A UI request to stop an ongoing experiment means pause/cancel the active local work and preserve the campaign for explicit resume, while a clearly named terminal end retains existing stop semantics. If a process outcome is unknown, preserve the unknown state and require explicit informed retry; never assert that unfinished work is safely complete. The user sees both the pause request and resulting outcome. A model limit or failure pauses using locally available state, without another model call; do not claim unpersisted in-flight work was saved. Resume must not silently retry unknown or failed actions or change the approved budget.

The UI stage strip projects real stages: propose and select are distinct only when persisted evidence supports that distinction; execute maps to implement, evaluation/validation to evaluate, and analysis/record to learn. Snapshot, verify, decide, promotion and rejection must remain visible as precise detail rather than being mislabeled. Repeat describes transition to another iteration, not a continuously fabricated stage. Show unknown when unavailable. An epoch in the operator view denotes a completed research cycle, with its count derived from durable completed attempt data; label attempts and experiments separately if their counts differ.

Composite work runs as a bounded, isolated sequence based on a champion snapshot. Its step snapshots and DEV evidence are retained for the sequence; a neutral intermediate state does not become global champion. A final combined candidate passes existing scope, DEV, validation, champion-promotion and holdout rules. No change may leak validation or holdout details into adaptive research memory.

At a configurable plateau the engine can persist an architecture proposal showing the current and proposed architecture, rationale, expected effect, changed scope and additional resources if needed. Pause before execution. Approve or reject by proposal identity and immutable plan digest; approval is explicit, audited and cannot authorize a materially different plan. Any changed objective, evaluator or scope creates a new approved immutable campaign specification and plan lineage, without rewriting old evidence. On rejection the operator explicitly chooses continue under the current approved plan, prepare a modified plan, or end. Do not spend beyond an approved budget while generating a proposal.

Evaluate the existing ten-task benchmark as a separate workstream: inventory immutable task identities and data, freeze baseline and candidate configurations, define paired trials and common budgets before executing, record measured costs, failures and missing outcomes, and treat extension/repetition as separate versioned suites. Introduce Python mutation testing initially for budget, evaluation isolation, promotion and related control paths; baseline surviving mutants and classify equivalent mutants before choosing thresholds. Provider calls are not required for ordinary deterministic mutation checks.

## Alternatives
### Option A: Pi extension as operator shell
- Pros: reuses Pi TUI, extensibility, widgets and input.
- Cons: adds Pi and Node runtime coupling while Codex is first conversational backend; exact embedding and Windows behavior unverified.
- Decision: selected for the first implementation, conditional on the production acceptance checks above.

### Option B: Standalone terminal client with Codex transport
- Pros: surface can directly express campaign layout and be independent of the chosen experiment agent; existing CLI remains usable.
- Cons: additional UI and process/session integration to maintain; Codex access and rendering behavior must be verified.
- Decision: fallback if the Pi extension fails Codex integration, terminal usability or packaging checks.

### Option C: Fork Pi or replace BananLoop engine
- Pros: maximum control of a single runtime.
- Cons: larger maintenance and migration burden; risks duplicating existing engine protections and ties UI decisions to agent choice.
- Decision: rejected for initial work; revisit only with concrete evidence that supported integration points cannot meet requirements.

### Option D: Treat STOPPED as resumable
- Pros: reuses a familiar command.
- Cons: changes a documented terminal state and risks reopening completed or finalized campaigns.
- Decision: rejected; introduce resumable pause and retain terminal stop compatibility.

## Contracts
### API
Local operator commands carry an explicit campaign identifier and request ID. Read projection returns `campaign_id`, `revision`/event sequence, `goal`, `status`, exact `engine_stage`, nullable display stage, active experiment and epoch counts, DEV-only hypothesis outcomes, baseline/champion metrics, resource counters, and last-update time. Missing values are null with a reason; UI never synthesizes a live value. A consistent snapshot or clearly identified incremental revision prevents misleading partial updates. Control returns accepted/rejected plus request ID, followed by durable observed state; acknowledgement does not mean completion.

`pause` targets active campaign work and is idempotent; `resume` is explicit and only applies to a resumable status with existing retry safeguards. `end` delegates to existing terminal-stop semantics. Conversational intent is translated to a typed command and validated by the same control service; the agent cannot bypass authorization, budget or evaluation checks. Destructive/plan-changing actions require an explicit operator decision. Failed actions, unknown outcomes and unrecognized intent are visible and never auto-replayed. Exact wire protocol and UI library are chosen in feasibility work; local subprocess/IPC contracts must be versioned and validated before external use.

### Data
SQLite remains the source of truth; UI retains only disposable view and chat state. Add versioned, forward migrations only when new durable pause status, control receipts, composite sequence metadata, proposal lineage or chat audit records require them. Existing databases must open safely; migration needs explicit rollback/backup policy and tests. Distinguish intent, in-flight, completed, failed and unknown outcomes. Retain only information needed for audit; never store secrets in chat or receipts. Approved spec hashes remain immutable: changes create a linked new spec/campaign, preserving prior results and comparison conditions.

### Events / Jobs
UI observes committed sequenced events and persisted snapshots asynchronously; lag and disconnect are explicit, and the last committed sequence supports reconnect without assuming every live event exists. No separate distributed queue is required for one local operator. The engine owns ordering, write locking and cancellation of managed work. Idempotent control requests use durable IDs. Side effects are never retried automatically where the original outcome is unknown; explicit retry must preserve reservation accounting. Terminal end does not trigger unapproved holdout on a user pause.

### Errors
Distinguish user pause, terminal stop, model rate limit, authentication/configuration errors, transport failure, evaluator failure and uncertain action outcome where evidence supports classification; fall back to an honest generic error. For model failures, stop dependent work, persist available status/reason and surface the last committed state without generating an explanatory model call. Keep incomplete candidates marked incomplete. Remote/detached child termination and in-flight unsaved output cannot be guaranteed. Log actionable error categories while avoiding secrets and private evaluation content.

## Security
This is a local trusted-user application, not an OS-grade adversarial sandbox. Treat repository content, logs and model output as untrusted for commands and UI rendering; sanitize terminal control sequences and validate structured commands. Never execute raw agent-generated shell for operator controls. Preserve existing filesystem scope, evaluator binding, frozen spec, DEV/validation/holdout separation and budgets in the engine regardless of UI or chat. Restrict local control endpoint to the initiating user or an equivalently protected local IPC channel; do not open a network listener by default. Never log credentials, tokens or private evaluation payloads. Approval must reference displayed immutable proposal digest, allowed scope and budget; no model response can approve on the operator's behalf.

## Testing Strategy
Test projection truthfulness and mapping for all persisted stages, resizing and absent data; verify pause acknowledgement versus completion, idempotency, interruption and crash recovery, unknown and failed retries, and a terminal stop remaining terminal. Integration tests exercise local CLI/engine/control with fake or deterministic agents and evaluator, including reconnects and mocked model failures without any subsequent model calls. Verify composite branch isolation, neutral step retention, scope enforcement and DEV/validation/holdout boundaries; verify approval tampering, rejection and new spec lineage. Exercise real Windows Terminal and, where available, Linux/macOS terminals. Use a bounded optional Codex smoke test only after environment and cost constraints are known. Run mutation analysis on the critical invariants and maintain a documented survivor baseline. Benchmark tests verify manifests, pairing, resource accounting and reporting without asserting success from incomplete trials.

## Consequences
The first operator experience may require new dependencies and a local control boundary, and a genuine resumable pause entails state and migration work. Showing only committed facts may provide less immediate detail than a model-generated log, but avoids inventing state. A framework bake-off and Codex validation add early work before implementation; they limit the risk of committing to an unsuitable host. Existing terminal stop and CLI compatibility remain intact. Roll out behind an opt-in entry point; preserve old storage until migration and recovery are verified. A failed migration or UI rollout must leave existing CLI usable and state recoverable from backup.

## Open Questions
- Production chat integration: verify thread and turn lifecycle, role versus operator model settings, real authentication and limit errors in the selected Pi plus Codex bridge.
- Production UI acceptance: verify actual Windows Terminal resize, narrow-window input and packaging; reopen shell choice if these fail.
- Blocking exact progress implementation: which useful events are currently durable, how stale can concurrent reads be, and what additional engine events are necessary?
- Blocking composite/pivot implementation: migration details and explicit proposal/sequence contracts must be refined with actual engine state transitions and compatibility tests.
- Benchmark task provenance, task equivalence, resource limits and mutation tool Windows compatibility require inventory before setting targets or running expensive comparisons.
