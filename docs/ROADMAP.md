# Roadmap

BananLoop is preparing a **private v0.1.0 Research Preview candidate** (not tagged or published). This roadmap distinguishes implemented capabilities, mandatory pre-preview evidence, and future proposals. It is not a delivery schedule.

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
- Keep the repository private. A tag or private GitHub Release is optional and requires explicit owner approval.

## Next — targeted for v0.2.0 or later

### Visual research interface

An optional interface for campaign timeline, hypothesis tree, observed metrics, budgets, action receipts, and champion diff. The CLI remains first-class.

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
