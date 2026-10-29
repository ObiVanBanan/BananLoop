# Changelog

All notable user-facing changes are documented here. The project follows a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style and intends to follow semantic versioning as interfaces stabilize.

## [0.1.0] — Research Preview (release candidate)

**Status:** release candidate; no GitHub release or tag is implied by this file alone.

### Added

- Standalone goal-driven experiment supervisor with baseline/champion tracking.
- Independent DEV evaluation with optional validation and final-holdout boundaries.
- Persistent campaign events, budgets, pause/resume, and export reports.
- Codex, Pi, and command-adapter workflows.
- DEV-only research memory, failure analysis and explore/exploit/reframe.
- Candidate portfolio selection with hard deterministic gates.
- Opt-in LLM semantic selection, bounded evidence references, durable fallback.
- Quick-start and expert configuration paths, deterministic fixture, mini-matcher.
- Project presentation assets, community files and release documentation.

### Known limits

- Not a hardened sandbox for adversarial agent commands.
- May require an operator-supplied evaluator or trustworthy labels.
- Structured model token accounting can be incomplete depending on provider.
- Evidence of correctness on fixtures is not benchmark leadership.
- GitHub Actions may be unavailable because of account-level runner billing; independent test receipts remain necessary.

See [docs/DEMO.md](docs/DEMO.md) and [docs/ROADMAP.md](docs/ROADMAP.md).
