# v0.1.0 Research Preview — release checklist

**Release decision (2026-10-29): private Research Preview only.** Keep repository visibility private; no public launch, announcement, package or automatic tag. A reproducible head-to-head against ordinary iterative Codex is mandatory **before** inviting preview users or approving a private release. Follow the [publication protocol](PUBLISH_PROTOCOL.md) and record exact-SHA evidence. Writing documentation is not a release.

## Code / evaluation

- [x] Independent QA of semantic selector patch on SHA `c698e4966a0cf8621dbfbff8145c1397b278c2e8`: 335 pytest passed, six fresh Luna replay selections accepted, one CPU mini matcher smoke passed.
- [x] Version in `pyproject.toml` is 0.1.0.
- [ ] Run full suite on the exact final main release commit (documentation-only changes should not alter logic but warrant verification).
- [ ] Check clean install and fixture on Linux / Windows; record actual stdout and versions.
- [ ] Review private evaluator and HOLDOUT isolation using the final tree.
- [ ] Resolve CI infrastructure/billing issue, or explicitly document verified substitute checks before publishing.

## Head-to-head benchmark — release blocker

- [ ] Freeze a fair **Codex Direct vs Codex + BananLoop** paired protocol (same model, task, evaluator and available DEV feedback; direct Codex may iterate).
- [ ] Run and archive the repeat with exact source/model/evaluator SHAs, task IDs, seeds, per-arm accounting and eligibility decisions.
- [ ] Independently score frozen final artifacts without feeding validation/HOLDOUT observations into agent planning.
- [ ] Report win/tie/loss outcomes, quality, model tokens/cost, wall time, known budget/protocol mismatches and failures. **No superiority claim is required to release; honest evidence is required.**
- [ ] Update the [benchmark evidence register](BENCHMARK_STATUS.md) and attach sanitized proof of the new run. Earlier reported exploratory AutoResearchExam 7:3 DEV results are **not** sufficient.

## GitHub presentation

- [x] Distinctive banner, short marketing README, honest DEV demo.
- [x] MIT license, contributing guide, security policy, code of conduct.
- [x] Issue and PR templates, changelog, roadmap.
- [ ] Verify README/banner display in GitHub dark **and** light theme, including mobile width; use [visual handoff](VISUAL_DIRECTION.md) without treating illustration as measured UI.
- [ ] Review/copy the [suggested GitHub About text and preview notes](RELEASE_COPY.md) into repository settings when approved.
- [ ] Confirm repo remains **private**; review collaborator access and redact leaked credentials, personal paths or protected evaluation data before granting any preview access.
- [ ] Keep a comprehensive history-wide secrets and rights audit as an explicit prerequisite for any **future public** opening; do not change visibility as part of v0.1.0.

## Approve private preview

- [ ] Record the exact final `main` SHA and obtain explicit owner approval after all mandatory checks.
- [ ] Approve the audience, private access/invitations and distribution method. **No public GitHub Release or visibility change.**
- [ ] Optionally, after separate approval, create a private `v0.1.0` tag and GitHub Release referencing the approved SHA. Do not imply PyPI distribution.
- [ ] Update README/CHANGELOG from the final measured report; distinguish exploratory DEV signals from independently evaluated comparisons.

**No cost-matched or independently held-out superiority over ordinary Codex has yet been established by the mini matcher or exploratory DEV comparisons.**
