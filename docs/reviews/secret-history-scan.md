# Secret history scan

## Scope

The draft workflow in [PR #90](https://github.com/ObiVanBanan/BananLoop/pull/90) uses Gitleaks CLI v8.27.2 over full Git history, fetched branches and tags, and GitHub pull-request head refs. The checkout is read-only inside the scanner container; the container has no network. Raw scanner output and its temporary JSON report stay on the ephemeral runner. CI logs expose only rule ID, path, line, commit, and fingerprint.

The first hosted scan was run on commit `c7928e67f9c6217bf3a57746dcfa1118c89f6b7a` in workflow run [41243943394](https://github.com/ObiVanBanan/BananLoop/actions/runs/41243943394). Gitleaks completed and reported 14 potential `generic-api-key` findings. The scanner exited nonzero; no findings were suppressed or allowlisted.

## Manual triage

All 14 findings were reviewed without copying their matched values into logs or this report:

- Two findings at lines 31 and 34 of `operator-ui/chat-pause-flow.test.mjs`, commit `b4abbf1488296ff43a4f4c3c639ed24e54a3764e`, are deterministic values in a unit-test fixture. The test injects a fixed token factory and checks pause-proposal parsing. Production code's default factory generates a fresh local confirmation token; the fixture value is not an external service credential.
- Twelve findings at lines 27, 52, 130, 155, 233, 258, 336, 361, 439, 464, 542, and 567 of `examples/calibration/proposer_ab/selector_calibration_v1/replay.json`, commit `a827f96f0b6d6584baa6d2d27805832f33e59a3b), are 64-character hexadecimal `winner_content_key` / `new_winner_content_key` fields. The replay generator's `content_key(candidate)` is SHA-256 over normalized serialized candidate content. These are content fingerprints, not credentials.

No actual credential was confirmed in these findings. The scanner still reports them as potential secrets; the history audit records that result rather than suppressing those fingerprints.

## Limits

This is evidence from Gitleaks' default detection rules over refs reachable in that CI checkout. It cannot prove that every secret type is absent, and it does not check GitHub's secret-scanning alert list or external credential validity. The report does not claim complete secret/privacy or benchmark-data-rights clearance.