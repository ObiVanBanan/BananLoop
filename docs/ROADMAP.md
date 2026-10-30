# Roadmap

BananLoop is preparing a **v0.1.0 Research Preview candidate** (not tagged or published). The release intent is a closed preview, but GitHub metadata currently reports this repository as **public**; this discrepancy is unresolved. This roadmap distinguishes implemented capabilities, mandatory evidence, and deferred proposals. It is not a delivery schedule.

## Available in the preview

- Bounded goal-driven propose → implement → evaluate → learn loop.
- DEV-only research state and previous experiment evidence.
- Exploit / explore / reframe planning signals.
- Frozen evaluator governance, budgets, durable SQLite actions, resume.
- Role-specific models and supported coding-agent adapters.
- Opt-in LLM semantic selector with deterministic gates and fallback.

## Before approving the closed v0.1.0 preview

- Independently review the paired ordinary iterative Codex vs Codex + BananLoop benchmark, with matched resource constraints, actual usage and final evaluation. See [benchmark evidence register](BENCHMARK_STATUS.md).
- Verify the exact final SHA, clean installation, reproducible demo, evaluator isolation, budget/recovery paths, security and README rendering.
- Reconcile the public repository setting with the intended closed-preview audience before recommending a release. This work does not change visibility. A tag or GitHub Release requires separate owner approval.

## Next — targeted for v0.2.0 or later

### Interface boundary

The CLI is the v0.1 user interface. Do not develop or expand the experimental Pi terminal UI or Codex chat for this preview. Preserve the existing headless snapshots, persisted events and typed controls. A future graphical interface is a separate desktop application; no technology is selected and implementation is deferred to v0.2+.

### Human-approved architecture pivots (v0.2.0 target)

When progress stalls, propose a materially different architecture, **pause for explicit operator permission**, expand the approved scope/budget if authorized, and only then run a new bounded research plan. The current engine does **not** silently expand scope or rewrite evaluation objectives.

### Multi-step / composite hypotheses

Support dependent experiments that may have a temporarily neutral result individually but improve a combined architecture. Protect validation and holdout isolation.

### Extended evaluations after the v0.1.0 benchmark gate

Paired deterministic-vs-LLM selector comparisons on identical frozen portfolios and common DEV budgets; multiple real project classes; report actual costs and failure modes. No claims of general improvement without repeated independent evidence.

## Non-goals for the preview

- Automatic GitHub merges or deployments.
- Unrestricted remote-code execution or an OS-grade adversarial sandbox.
- Unverified "better than Codex" or generalized benchmark performance promises.
- Inventing trustworthy gold labels without reference evidence.
