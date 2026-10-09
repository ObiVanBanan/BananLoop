# M5 completion: execution profiles

## Delivered

- Optional `--agent-profiles <toml>` on `goal`, `start`, and expert `init`.
- Typed `[default]` and `[roles.bootstrap|propose|implement|analyze]` settings for backend, exact model ID, provider, and reasoning effort. The campaign stores the resolved config; resume does not reread the source TOML.
- Codex and Pi adapters apply the role settings. Without a profile or explicit model, the external CLI remains authoritative. No silent fallback is performed.
- Bootstrap plan and campaign event `AGENT_LAUNCH_PROFILE` record role, non-secret settings, and CLI version. TOML rejects arbitrary argv/commands and validation output omits rejected values.
- Codex named profiles are deliberately excluded: their mutable config files cannot be frozen by this campaign contract. Model availability is checked only by the actual adapter invocation, not by a paid preflight call.

## Verification

- Scoped regression after repairs: 92 passed.
- Full suite: 201 collected, 197 passed, 2 skipped, 2 failed. The failures are the existing Windows child-process cancellation tests for timeout/stop; the same two failures were present before M5.
- `python -m compileall -q src tests` passed.
- `git diff --check` passed (Git printed only the existing LF-to-CRLF worktree advisory).
- One review pass and one security pass completed. Initial findings and repairs are in [review report](review_report-execution-profiles.md) and [security report](security_review-execution-profiles.md). No second review was run.

## Limits

The external CLI does not expose a no-cost entitlement probe for every backend/model; access failures surface during a real invocation. BananLoop does not claim the user's Codex GUI model is automatically inherited by `codex exec`; when model is omitted, that external CLI's normal config chooses it.

No live model call, campaign run, commit, or push was performed.
