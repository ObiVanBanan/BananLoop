# Issue #64 — limited Docker isolation smoke

**Date:** 2026-10-31  
**Host:** Windows, Docker Desktop 29.9.2; Docker engine reports 12 CPUs and about 16 GiB RAM.  
**Image:** official `python:3.13-slim`, pulled and executed by digest `sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb`.

## Controls exercised

A disposable container ran only a harmless Python probe with these Docker settings:

```text
--network none
--read-only
--cap-drop ALL
--security-opt no-new-privileges
--pids-limit 64
--memory 8g
--cpus 2
--tmpfs /tmp:rw,noexec,nosuid,nodev,size=1g
```

The probe confirmed:

- outbound socket connection to `1.1.1.1:443` failed;
- writing to the container root failed while the temporary filesystem remained writable;
- the container had no host mounts in its invocation and no host secret path was present;
- effective capabilities were zero and `NoNewPrivs` was set;
- cgroup v1 exposed CPU quota/period `200000/100000`, memory limit `8589934592`, and process limit `64`;
- a child process was terminated and reaped.

No user data, credentials, benchmark inputs, generated candidate, grader, evaluator, or model process was mounted or run. No paid calls were made.

## What this does not establish

This is a basic Docker control smoke, not the Issue #64 end-to-end sandbox gate. The main tree does not currently contain the specified AutoResearchExam task adapter or pilot runner. This smoke did not test an agent/grader split, protected evaluator data, path traversal or symlink rejection, host-candidate isolation with a real task adapter, network allowlisting for model calls, disk quota, cleanup of a full campaign, or independent evaluator storage. No credential-forwarding proxy is configured for a model process inside the container. H2H remains `BLOCKED / INELIGIBLE` until those components and negative tests exist; no unsandboxed fallback is allowed.
