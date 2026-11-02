# RPDI-00 executable spike

This prototype exercises the riskiest v0.2 contracts without changing the v0.1 Engine. The core spike uses only the Python standard library; its migration fixture imports the project runtime to build a representative v4 database through the real `CampaignStore`. It intentionally lives outside `src/bananloop`; pytest is configured to discover this suite alongside the existing tests.

Run:

```sh
python3 -m unittest discover -s spikes/rpdi00 -v
```

See [CONTRACTS.md](CONTRACTS.md) for ownership, typed boundaries, lifecycle and safety decisions. Passing these tests is evidence for the RPDI-00 review gate, not a production readiness claim.
