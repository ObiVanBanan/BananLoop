# v0.1.0 Research Preview — release checklist

**Release intent (2026-10-29): closed Research Preview.** The repository endpoint reported public visibility on 2026-10-31 05:42 UTC, conflicting with the closed-preview intent. An apparent private result on 2026-10-30 came from a branch/PR payload without repo-level visibility and was not valid evidence. Collaborator access and exposed-artifact review remain outstanding. This work does not change visibility. No announcement, package, tag, or release is authorized here. A reproducible head-to-head against ordinary iterative Codex and the other mandatory gates must pass before recommending any preview. Follow the [publication protocol](PUBLISH_PROTOCOL.md) and record exact-SHA evidence. Writing documentation is not a release.

## Code / evaluation

- [x] Merge [PR #75](https://github.com/ObiVanBanan/BananLoop/pull/75) campaign and snapshot cursor consistency fix.
- [x] Merge [PR #61](https://github.com/ObiVanBanan/BananLoop/pull/61) opt-in Codex token accounting. It is not provider-enforced and does not remove budget limitations.
- [x] Independent QA of semantic selector patch on SHA `c698e4966a0cf8621dbfbff8145c1397b278c2e8`: 335 pytest passed, six fresh Luna replay selections accepted, one CPU mini matcher smoke passed.
- [x] Version in `pyproject.toml` is 0.1.0.
- [x] Run full suite on code SHA `44614ea22bc5967adb7ee607bce2472cc66487ee`: Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12 each report `448 passed, 1 skipped` in workflow #706. The skip is the unavailable pinned selector source commit.
- [x] Check clean installation on Linux and Windows through CI `pip install -e ".[test]"`; each configuration completed installation before its passing suite. Local Windows full pytest is constrained by sandbox failures in child Git/MSYS; rely on exact-SHA hosted Windows evidence.
- [ ] Review private evaluator and HOLDOUT isolation using the final tree.
- [x] Run limited Issue #64 Docker control smoke using a digest-pinned official Python image; no benchmark code or model call. Full grader/agent isolation is still unverified; see `docs/BENCHMARK_STATUS.md` and `docs/reviews/are-isolation-smoke.md`.
- [x] Verify the final-main CI/install substitute: exact-SHA workflow #706 passed across Windows/Linux. Benchmark billing authorization remains outstanding under the H2H gate.

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
- [ ] Resolve the verified public-visibility vs closed-preview discrepancy, then review collaborator access and exposed artifacts before any preview recommendation. Do not change repository visibility in this work.
- [ ] Complete a comprehensive history-wide secrets and rights audit before any preview recommendation. The 2026-10-31 selected-format scan checked 4,232 reachable Git objects (1,542 blobs) and found no matches in selected key/token formats; it does not replace a dedicated scanner, GitHub secret-alert review, or a rights audit. Do not change visibility as part of v0.1.0.

## Approve a closed preview

- [ ] Record the exact final `main` SHA and obtain explicit owner approval after all mandatory checks.
- [ ] Approve the intended audience, access and distribution method after the repository visibility discrepancy is resolved. **No public GitHub Release or visibility change.**
- [ ] Only after the actual visibility matches the approved audience and all gates pass, separately approve any tag or GitHub Release from the audited SHA. Do not imply PyPI distribution.
- [ ] Update README/CHANGELOG from the final measured report; distinguish exploratory DEV signals from independently evaluated comparisons.

**No cost-matched or independently held-out superiority over ordinary Codex has yet been established by the mini matcher or exploratory DEV comparisons.**
