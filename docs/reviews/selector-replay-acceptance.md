# BL-006 · Selector replay acceptance

The selector v2 manifest pins commit
`cd753c97c62148c3ccd3f030251e96a4d19b23bd`. Git cannot resolve this
commit in the local BananLoop checkouts. The GitHub commit endpoint for this
repository returned HTTP 422, `No commit found for SHA`.

The archived Docker build context matches 36 of the 37 implementation hashes
after accounting for LF/CRLF. The remaining pin,
`benchmarks/selector_ab_v2/agent-profiles.toml`, differs under both conventions.
The current checkout matches 26 of 37. Neither establishes the exact
historical source revision or the complete runtime configuration.

The [run manifest schema](../../benchmarks/run-manifest.schema.json) documents
the archived selector manifest. Verification using
`tests/test_selector_report.py` and `tests/test_selector_preregistration.py`
gave 9 passed and 1 skipped. The skipped test requires the pinned Git object.

Replay and launcher acceptance remain blocked. The candidate launcher was
tested with a synthetic manifest. Its Docker invocation uses a mutable tag
after inspecting its ID, and its path checks do not reliably confine manifest
inputs to their expected directories. The production launch path requires
review against the recovered source, a digest-selected image and two
reconstructible runs. The saved manifest continues to reference the original
source SHA.


A follow-up scan of 372 local copies of `agent-profiles.toml`, including
trial and replay folders, found zero exact matches for the frozen
`54758a737437a66f66f0c410762ba1dcb373b08e51e0bdd06ad0bdf53998e441`.
No matching source snapshot was recovered from visible refs, reflogs,
or unreachable local Git objects.
