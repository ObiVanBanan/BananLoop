# RPDI-00 executable spike

This stdlib-only prototype exercises the riskiest v0.2 contracts without changing the v0.1 application. It intentionally lives outside `src/bananloop`.

Run:

```sh
python3 -m unittest discover -s spikes/rpdi00 -v
```

See [CONTRACTS.md](CONTRACTS.md) for ownership, typed boundaries, lifecycle and safety decisions. Passing these tests is evidence for the RPDI-00 review gate, not a production readiness claim.
