# BL-015 · Evidence packet (draft)

**Update 2026-11-23:** Docker access has been restored. A fresh read-only
preflight found all 12 pinned image IDs, but remains `PREP_BLOCKED` on nine
unsafe runtime overlays. See the [fresh report](../../BananLoop-autonomous-benchmark-v01-prep/benchmarks/autonomous_v01/OFFLINE-PREFLIGHT-2026-11-23.json).
References below to denied Docker access describe the 2026-11-22 snapshot.
The [review index](review-index.md) locates the available campaign evidence.

**State: BLOCKED for live-campaign acceptance; draft packet only.** This packet
does not accept BL-015 as a completed live paired campaign. It records offline
implementation evidence and the blockers visible in the linked local checkouts.
It is ready for an independent reviewer to verify and revise; it is not a
signed launch decision.

## Decision vocabulary

- **PASS** — directly supported by the cited artifact at its stated SHA and
  within the claim's exact scope.
- **FAIL** — cited evidence demonstrates that an acceptance condition failed.
- **BLOCKED** — the check or live action cannot currently complete because a
  named dependency or environment gate is unavailable or failing.
- **UNVERIFIED** — evidence is absent, stale, or insufficient to decide.

These labels apply per claim. A PASS for synthetic/offline behavior is never a
PASS for model outcomes, Harbor execution, or a live paired campaign.

## Snapshot and provenance

The two local repositories were inspected on 2026-11-22. The main checkout
`/home/sonik/Проекты/BananLoop` was at `559621dfa0e9e230b3dd5257399b6baa88bfbade`;
the preparation checkout
`/home/sonik/Проекты/BananLoop-autonomous-benchmark-v01-prep` was at
`be24a260615179f9437859e87fe8a8b2b5b12377`. Both working trees were dirty.
The main checkout had modified source, docs and tests plus untracked acceptance,
benchmark, review and OpenSpec artifacts. The preparation checkout likewise
had modified runner/CLI/tests and untracked benchmark, review and helper files.
Therefore neither HEAD SHA identifies the complete working tree inspected here;
do not combine their contents into a single source revision or imply the
preparation repo's preflight ran against the main repo's current edits.

The prep repository's dated preflight explicitly binds to its own
`be24a260615179f9437859e87fe8a8b2b5b12377` source SHA. The fake archive index
is separately indexed at
[`benchmarks/autonomous_v01/fake-24-arm-e2e-index.json`](../../BananLoop-autonomous-benchmark-v01-prep/benchmarks/autonomous_v01/fake-24-arm-e2e-index.json)
and describes synthetic arms. The prior Full archive described in
[`docs/reviews/autonomous-full-e2e-acceptance.md`](../../BananLoop-autonomous-benchmark-v01-prep/docs/reviews/autonomous-full-e2e-acceptance.md)
pins harness `ada1f18`; it is archival evidence from an earlier harness, not a
current-tree replay. Read the exact relative-path evidence links in
[`claims.md`](claims.md).

## What is ready

- Offline preparation, fake-arm orchestration and archive machinery have
  documented evidence. These support bounded engineering claims only.
- A 12-task preparation manifest/catalogue path and a synthetic 24-arm archive
  exist. Their current preparation gate is explicitly `PREP_BLOCKED`.
- Prior archived Full attempts and task-level public DEV receipts exist, but
  they are historical and do not convert the current blocked preflight into a
  live acceptance.

## Remaining blockers

- **BL-012 / BL-013 / BL-014: BLOCKED or UNVERIFIED for this packet.** The
  inspected local acceptance artifacts do not establish their closure against
  the current tree. Require each owning ticket's exact criterion, pinned
  evidence, and reviewer decision before treating dependencies as satisfied.
- **Docker: BLOCKED.** The current preflight reports denied Docker socket
  access for all 12 image-ID probes. Actual local image IDs are unknown; the
  evidence does not prove images are absent or mismatched.
- **BL-042: BLOCKED for live campaign.** Deadline/watchdog behavior has offline
  evidence, but Harbor-managed container cleanup is not verified, and the
  production paired launcher still must enforce the watchdog and recovery
  policy.
- **BL-043: BLOCKED.** Production paired launcher/preflight gate is tracked as
  incomplete in the prep README; no live-series launcher acceptance is shown.
- **BL-044: BLOCKED.** Offline failure-matrix and overlay work exists, while
  fresh controlled DEV cycles and protocol/descriptor reapproval remain
  blocked. Historical failures, including CARPS zero-dispatch, remain
  unresolved as documented.
- The 2026-11-22 preflight also reports nine unsafe runtime overlays,
  Codex-auth probe handling that does not accept the observed login output, and
  failing linked CI test jobs. Do not present `PREP_BLOCKED` as a PASS.
- Three tasks have disclosed prior private grader-context snippet exposure.
  Any diagnostic involving them must retain that disclosure; no blind,
  pristine, independent-holdout, or RPDI-14 claim is supported.

## Independent reviewer checklist

- [ ] Recheck each cited path and confirm it is from the stated repository.
- [ ] Confirm the exact SHA binding for every artifact; flag dirty-tree content
      that cannot be reconstructed from the commit alone.
- [ ] Keep fake/synthetic, archival, and current-tree evidence separate.
- [ ] Check every PASS is scoped to what its artifact actually exercises.
- [ ] Confirm Docker probe state is reported as unknown due to permission
      denial, not as missing/mismatched images.
- [ ] Verify BL-012/013/014 owner criteria and bring their evidence into this
      packet before upgrading their statuses.
- [ ] Verify BL-042 watchdog, timeout recovery, and Harbor container cleanup;
      verify BL-043 production launcher; verify BL-044 fresh overlay/fixture
      approval and controlled DEV cycle.
- [ ] Confirm no live model, Harbor, Docker, or grader activity is inferred
      from the offline and archived artifacts.
- [ ] Record reviewer name/date, exact reviewed tree SHA, findings, and a
      separate accept/reject decision before closing BL-015.

## Current conclusion

**BL-015 live acceptance: BLOCKED.** Offline evidence packet: **DRAFTED**.
No live campaign outcome or acceptance is claimed.
