# Roadmap

BananLoop is at **v0.1.0 Research Preview**. This roadmap distinguishes working capabilities from proposals. It is not a delivery schedule.

## Available in the preview

- Bounded goal-driven propose → implement → evaluate → learn loop.
- DEV-only research state and previous experiment evidence.
- Exploit / explore / reframe planning signals.
- Frozen evaluator governance, budgets, durable SQLite actions, resume.
- Role-specific models and supported coding-agent adapters.
- Opt-in LLM semantic selector with deterministic gates and fallback.
- Experimental opt-in Pi operator terminal for committed snapshots and event
  history, typed pause/resume, and a separate Codex app-server chat. Chat sends
  only an explicit user message plus bounded goal/status/DEV/budget context;
  model-suggested pause requires a separate short-lived operator confirmation.
  The bridge is fake-server tested, but live account and Windows acceptance
  remain open.
- Ordered composite hypotheses retain intermediate snapshots and DEV outcomes;
  only the final combined candidate reaches the existing validation/promotion gates.

## Next

### Visual research interface

The current opt-in Pi interface provides campaign snapshot, event history,
metrics, budgets, composite sequence details, typed pause/resume, and a
restricted Codex chat. Natural-language pause is proposed in a constrained
schema and requires a separate expiring confirmation token. Synthetic tests cover layout, process arguments, structured output, confirmation expiry and control observation. A pending pivot proposal is shown with its ID, digest, architecture, rationale/effect, changed scope and resource estimates. `/pivot approve` and `/pivot reject` recheck the committed proposal; chat output cannot issue these commands and a recorded approval does not start work. The Node UI suite passes 21 tests. A Windows-hosted 80-column PTY rendered `/bananloop demo`
offline with an empty profile; Pi warned that `fd` was absent, the project was
untrusted, and no models were available. Ctrl-D returned exit code 1. This was
not visual Windows Terminal or resize acceptance; interactive resize and keyboard/focus have not been verified in Windows Terminal. Real Codex account/limit
behavior remains unverified.

### Human-approved architecture pivots

Human-approved architecture pivots are implemented through one budget-gated
adviser call at plateau, a durable `PAUSED_NEEDS_INPUT` proposal, and exact
proposal ID/digest-bound approve/reject decisions. The Pi UI rereads the proposal
before submitting; rejection requires an explicit next-step choice. Approval
records consent but does not resume work or create a child campaign automatically.
A changed goal, evaluator, or scope requires a separately confirmed linked
successor spec/campaign; prior hashes and results remain immutable. Synthetic
Python/Node tests and a security review pass; no live model or campaign run was
performed. Windows Terminal resize and real app-server model behavior remain
unverified.

### Multi-step / composite hypotheses

Composite hypotheses are implemented as bounded two-to-four-step sequences.
Intermediate results remain on the sequence branch; unsafe steps abort without
advancing the tip, and the final combined result uses existing validation and
promotion gates. Holdout is not scheduled for intermediate steps. Deterministic
recovery and final accept/reject tests pass; a live campaign was not run.

### More convincing evaluations

The initial ten-task AutoResearchExam v1.1 DEV comparison and its limitations are inventoried in [`docs/benchmarks/operator-roadmap.md`](docs/benchmarks/operator-roadmap.md). The paired agent/harness report runner is available; the current target manifest is planned and has no trial rows. A one-arm LLM mini-matcher smoke is documented, but a deterministic-vs-LLM selector comparison still needs its own frozen portfolio, matched manifest and approved resource plan. No paired selector measurements exist. The original AutoResearchExam task bundle still contains the initial ten; `hf/expansion/operator-roadmap-v1/` adds three public starters, but they have no safely isolated gold/HOLDOUT evaluator and remain unmeasured. Do not claim selector or general scientific improvement without repeated independent evidence.

## Non-goals for the preview

- Automatic GitHub merges or deployments.
- Unrestricted remote-code execution or an OS-grade adversarial sandbox.
- Unverified "better than Codex" or generalized benchmark performance promises.
- Inventing trustworthy gold labels without reference evidence.
