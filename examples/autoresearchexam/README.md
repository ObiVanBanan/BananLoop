# AutoResearchExam candidate sandbox

`sandbox.py` provides the process boundary for running a frozen candidate on a
single evaluator-supplied case. It is an infrastructure component, not an
AutoResearchExam task adapter or an H2H runner.

The trusted evaluator calls `run_candidate(...)` with the candidate artifact
directory and exactly one serialized case input. The container receives only
that read-only artifact mount and stdin. It has no evaluator/grader mount, no
host environment variables, and no network. The trusted evaluator must keep
labels, reference answers, other cases, and grader files outside the artifact
directory and must never put them in `case_input`.

The container policy requires a digest-pinned image and applies a read-only
root, a non-root UID, dropped Linux capabilities, `no-new-privileges`, no
network, bounded `/tmp` and `/dev/shm` tmpfs mounts, CPU/memory/PID limits, an
input limit, and bounded stdout/stderr. Docker image metadata is checked before every run to
reject implicit image volumes and credential-like image environment variables;
the entrypoint is overridden. Container logs use the `local` driver with a
1 MiB single-file cap; `/dev/shm` is capped at 64 MiB. The candidate artifact
path is the only bind mount.
Docker's default seccomp profile is relied on; the host preflight still must
verify the effective seccomp mode and the actual Docker Desktop runtime.
The mount probe checks known forbidden target paths, while the generated argv
and image-volume check establish the one explicit bind mount. This is not an
exhaustive scan of every host secret or daemon-injected mount. The detached
artifact copy is not inaccessible to another same-user host process; stage only
after the agent stops and keep it outside the agent workspace.

This helper does **not** implement the trusted evaluator, case partitioning,
agent isolation, model proxy, USD accounting, disk quota for Docker daemon
metadata, task-level timeouts, or integration with BananLoop's command
evaluator/readiness path. An inert Docker preflight passed on the target
Windows host, but this does not qualify the Issue #64 benchmark. No task,
benchmark candidate, model, DEV, VALIDATION, or HOLDOUT run was performed.

Tests use a fake Docker process and verify the generated policy, allowlisted
artifact staging, and fail-closed limits. For an inert target-host probe, stage
only `sandbox_probe.py`, set `command=("python", "-I",
"/candidate/sandbox_probe.py")`, and call `run_candidate(spec, case_input=b"")`.
The probe checks UID, capabilities, `no_new_privs`, seccomp, root/artifact
read-only mounts, tmpfs write access, egress denial, and cgroup CPU/memory/PID
limits. It does not import the candidate or grader. The verified image was
`python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb`;
the probe reported UID 65532, zero effective capabilities, seccomp mode 2, one
CPU, 8 GiB memory, 64 PIDs, read-only root/artifact, writable 256 MiB `/tmp`,
64 MiB `/dev/shm`, and blocked external networking. A separate one-second
timeout probe started a sleeping child; the runner timed out and `docker ps -a` showed no remaining
`bananloop-candidate-*` container.

The Docker daemon's overall disk usage is not capped by this helper. Writable
container paths and per-container logs are bounded, but H2H eligibility still
requires measuring the full target workload and available disk before each
arm.

A separate target-host preflight is still required before any benchmark run:
integrate the runner into each task adapter, add and verify the grader/candidate
process separation, and pass the full negative preflight from Issue #64 on the
target Windows host.
