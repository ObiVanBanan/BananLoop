# Roadmap

BananLoop is at **v0.1.0 Research Preview**. This roadmap distinguishes working capabilities from proposals. It is not a delivery schedule.

## Available in the preview

- Bounded goal-driven propose → implement → evaluate → learn loop.
- DEV-only research state and previous experiment evidence.
- Exploit / explore / reframe planning signals.
- Frozen evaluator governance, budgets, durable SQLite actions, resume.
- Role-specific models and supported coding-agent adapters.
- Opt-in LLM semantic selector with deterministic gates and fallback.

## Next

### Visual research interface

An optional interface for campaign timeline, hypothesis tree, observed metrics, budgets, action receipts, and champion diff. The CLI remains first-class.

### Human-approved architecture pivots

When progress stalls, propose a materially different architecture, **pause for explicit operator permission**, expand the approved scope/budget if authorized, and only then run a new bounded research plan. The current engine does **not** silently expand scope or rewrite evaluation objectives.

### Multi-step / composite hypotheses

Support dependent experiments that may have a temporarily neutral result individually but improve a combined architecture. Protect validation and holdout isolation.

### More convincing evaluations

Paired deterministic-vs-LLM selector comparisons on identical frozen portfolios and common DEV budgets; multiple real project classes; report actual costs and failure modes. No claims of general improvement without repeated independent evidence.

## Non-goals for the preview

- Automatic GitHub merges or deployments.
- Unrestricted remote-code execution or an OS-grade adversarial sandbox.
- Unverified "better than Codex" or generalized benchmark performance promises.
- Inventing trustworthy gold labels without reference evidence.
