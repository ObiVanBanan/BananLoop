# v0.1.0 Research Preview — release checklist

**Release intent (2026-10-29): closed Research Preview.** GitHub reported public visibility earlier on 2026-10-30 and private visibility at 2026-10-30 22:09 UTC. The current setting matches the closed-preview intent; collaborator access and exposed-artifact review remain outstanding. This work does not change visibility. No announcement, package, tag, or release is authorized here. A reproducible head-to-head against ordinary iterative Codex and the other mandatory gates must pass before recommending any preview. Follow the [publication protocol](PUBLISH_PROTOCOL.md) and record exact-SHA evidence. Writing documentation is not a release.

## Code / evaluation

- [ ] Review [PR #75](https://github.com/ObiVanBanan/BananLoop/pull/75) for campaign state/snapshot cursor consistency; its local CPU-only E2E and Windows suite evidence is branch evidence, not a final-main acceptance run.
- [ ] Review the updated [PR #61](https://github.com/ObiVanBanan/BananLoop/pull/61) for opt-in Codex token metering. It remains Draft, is not provider-enforced, and has not been integrated into `main`.
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
- [ ] Resolve the verified public-visibility vs closed-preview discrepancy and review access/exposed artifacts before any preview recommendation. Do not change repository visibility in this work.
- [ ] Complete the history-wide secret/privacy audit. A limited 2026-10-30 pattern scan found zero selected-format matches in 3,999 reachable object IDs, but is not a complete scanner/clearance.
- [ ] Complete a comprehensive history-wide secrets and rights audit before any preview recommendation. The limited selected-format scan recorded in the evidence docs is not full clearance. Do not change visibility as part of v0.1.0.

## Approve a closed preview

- [ ] Record the exact final `main` SHA and obtain explicit owner approval after all mandatory checks.
- [ ] Approve the intended audience, access and distribution method after the repository visibility discrepancy is resolved. **No public GitHub Release or visibility change.**
- [ ] Only after the actual visibility matches the approved audience and all gates pass, separately approve any tag or GitHub Release from the audited SHA. Do not imply PyPI distribution.
- [ ] Update README/CHANGELOG from the final measured report; distinguish exploratory DEV signals from independently evaluated comparisons.

**No cost-matched or independently held-out superiority over ordinary Codex has yet been established by the mini matcher or exploratory DEV comparisons.**
