# v0.1.0 Research Preview — release checklist

**Release intent (2026-10-29): closed Research Preview.** The repository endpoint reported public visibility on 2026-10-31 00:12 UTC, conflicting with the closed-preview intent. An apparent private result at 2026-10-30 22:09 UTC came from a branch/PR payload without repo-level visibility and is not valid evidence. Collaborator access and exposed-artifact review remain outstanding. This work does not change visibility. No announcement, package, tag, or release is authorized here. A reproducible head-to-head against ordinary iterative Codex and the other mandatory gates must pass before recommending any preview. Follow the [publication protocol](PUBLISH_PROTOCOL.md) and record exact-SHA evidence. Writing documentation is not a release.

## Code / evaluation

- [x] Integrate [PR #75](https://github.com/ObiVanBanan/BananLoop/pull/75) campaign cursor fix into `main` through [PR #81](https://github.com/ObiVanBanan/BananLoop/pull/81). Linux current-base suite passed; Windows acceptance remains open.
- [x] Integrate [PR #61](https://github.com/ObiVanBanan/BananLoop/pull/61) opt-in Codex token accounting into `main` through [PR #81](https://github.com/ObiVanBanan/BananLoop/pull/81). It is post-call accounting, not provider-enforced spending control; budget authorization and cross-platform acceptance remain open.
- [x] Independent QA of semantic selector patch on SHA `c698e4966a0cf8621dbfbff8145c1397b278c2e8`: 335 pytest passed, six fresh Luna replay selections accepted, one CPU mini matcher smoke passed.
- [x] Version in `pyproject.toml` is 0.1.0.
- [ ] Run full suite on the exact final main release commit (documentation-only changes should not alter logic but warrant verification).
- [ ] Check clean install and fixture on Linux / Windows; record actual stdout and versions.
- [ ] Verify the digest-bound CLI approval/rejection flow on the final tree. Draft candidate [PR #92](https://github.com/ObiVanBanan/BananLoop/pull/92) previews and confirms an approved child before initializing it, supports explicit terminal rejection, and serializes decisions with run/resume through the campaign lock. Exact head `ebbc066ba7dddbe82290e9606c5fa2e0f8232801` passed hosted matrix [#41250697408](https://github.com/ObiVanBanan/BananLoop/actions/runs/41250697408) on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12 (Windows: 460 passed, 1 skipped). Final-tree verification remains open.
- [ ] Review private evaluator and HOLDOUT isolation using the final tree.
- [ ] Resolve CI infrastructure/billing issue, or explicitly document verified substitute checks before publishing.

### Verification update — 2026-10-31

- On code base `44614ea22bc5967adb7ee607bce2472cc66487ee`, Linux/Python 3.14.7 full suite: **448 passed, 1 skipped**; seven CPU-only lifecycle/research E2E cases: **7 passed**.
- A non-editable wheel install, CLI `--help`, and offline fixture generation succeeded in temporary Linux/Python 3.14 environments. Windows and Python 3.11/3.12 clean-install/full-suite evidence for the final SHA remain open.
- Skip: selector preregistration source commit is unavailable. This does not verify that missing source snapshot.

### CI update — 2026-11-01

- PR #85 head `d7bf9d055da1557e9a5f584a5b9b834e82129894` is based on `main` SHA `44614ea22bc5967adb7ee607bce2472cc66487ee`. Hosted run `41238895406` passed on Ubuntu Python 3.11/3.12/3.13 and Windows/Python 3.12 (Windows: 448 passed, 1 skipped in 642.13s). PR #86/#87 matrices also passed. PR #88/#89 matrices passed, including the Windows lifecycle E2E and clean-install fixture smoke (Windows: 456 and 457 passed, respectively; each had 1 skip). The final release SHA is not selected.
- The exact release SHA is not selected. The final-SHA checklist items remain open.
- PR #85's updated history-scan and release-checklist docs at SHA `8d5862058d3509d8d0c3d1358839d381e96ae521` passed runs [41244438038](https://github.com/ObiVanBanan/BananLoop/actions/runs/41244438038) and [41244442129](https://github.com/ObiVanBanan/BananLoop/actions/runs/41244442129) on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows: 448 passed, 1 skipped. A later audit-recommendation documentation refresh is under verification.


- PR #91 head `3ab301ad499f17f1a9abb4ac7813743b6d4f1d1a` passed hosted run [#41246679803](https://github.com/ObiVanBanan/BananLoop/actions/runs/41246679803) on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12 (Windows: 458 passed, 1 skipped). Local FINAL_HOLDOUT recovery fixture passed: interruption canceled the active evaluator child, resume completed one accepted result/receipt/event, and no agent action exposed the HOLDOUT canary or resumed adaptive work. Full local suite: 457 passed, 2 skipped; the skips are the unavailable selector snapshot and network-dependent clean-install build isolation. Details and artifacts are in the [E2E report](reviews/core-research-e2e-report.md).

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
- [x] Verify README/banner display in GitHub dark **and** light theme, including mobile width; use [visual handoff](VISUAL_DIRECTION.md) without treating illustration as measured UI.
  - Checked the public GitHub README at 390×844 px in Dark default and Light default. Banner stayed within the content column; headings, status text, and navigation links wrapped without visible horizontal clipping. Restored the prior account preference: Sync with system, Light mode Soft dark, Dark mode Dark default.
- [ ] Review/copy the [suggested GitHub About text and preview notes](RELEASE_COPY.md) into repository settings when approved.
- [ ] Resolve the verified public-visibility vs closed-preview discrepancy, then review collaborator access and exposed artifacts before any preview recommendation. Do not change repository visibility in this work.
- [ ] Complete a comprehensive history-wide secrets, privacy and data-rights audit before any preview recommendation. Existing limited scans found zero matches for selected key/token patterns: the 2026-10-30 scan covered 3,999 reachable Git object IDs; the 2026-11-01 scan covered 1,588 reachable blobs, 487 tracked/untracked worktree files and 2,872 added lines across accessible PR diffs. A hosted Gitleaks v8.27.2 scan in draft [PR #90](https://github.com/ObiVanBanan/BananLoop/pull/90) covered full history and fetched branch, tag, and PR-head refs; it reported 14 potential generic-api-key findings. Manual review found 12 SHA-256 candidate content hashes and two deterministic unit-test confirmation-token values, with no actual credential confirmed. The scan remains non-passing and unsuppressed; see the [triage report](https://github.com/ObiVanBanan/BananLoop/blob/codex/secret-history-scan/docs/reviews/secret-history-scan.md). Benchmark data rights/privacy remain unresolved. These checks do not prove complete clearance. Do not change visibility as part of v0.1.0.

## Approve a closed preview

- [ ] Record the exact final `main` SHA and obtain explicit owner approval after all mandatory checks.
- [ ] Approve the intended audience, access and distribution method after the repository visibility discrepancy is resolved. **No public GitHub Release or visibility change.**
- [ ] Only after the actual visibility matches the approved audience and all gates pass, separately approve any tag or GitHub Release from the audited SHA. Do not imply PyPI distribution.
- [ ] Update README/CHANGELOG from the final measured report; distinguish exploratory DEV signals from independently evaluated comparisons.

**No cost-matched or independently held-out superiority over ordinary Codex has yet been established by the mini matcher or exploratory DEV comparisons.**
