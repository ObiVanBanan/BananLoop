# Issue #64 candidate sandbox adapter progress

Base: `44614ea22bc5967adb7ee607bce2472cc66487ee`.

This change adds a small Docker command builder and bounded process wrapper in
`examples/autoresearchexam/sandbox.py`. It launches a candidate from a
digest-pinned image with no network, a read-only root and artifact mount,
non-root UID 65532, all capabilities dropped, `no-new-privileges`, bounded
CPU/memory/PIDs/tmpfs, and bounded input/output. The container receives no
campaign environment, grader mount, or evaluator files.

The module is deliberately only a candidate process boundary. It is not wired
into BananLoop's `CommandEvaluator` or bootstrap readiness flow, and it does not
implement the task scorers, disjoint case manifests, agent/model proxy, budget
admission, or USD accounting. It also does not prove the host's Docker Desktop
seccomp/device/daemon policy or constrain daemon metadata disk usage. Thus it
does not yet satisfy Issue #64's security gate and no experiment was run.

Verification: Windows local result was 16 passed, 1 skipped: the sandbox did
not allow creating a symlink for the negative-path test. The symlink test is
expected to run in Linux CI. An inert target-host Docker probe passed with
digest-pinned Python 3.13.18 and
reported UID 65532, `CapEff=0`, `NoNewPrivs=1`, `Seccomp=2`, read-only root and
candidate mounts, writable 256 MiB `/tmp`, 64 MiB `/dev/shm`, blocked external
networking, one CPU, 8 GiB memory, and 64 PIDs. Docker image inspection confirmed no implicit
volumes/credential-like env; the `local` logging driver accepted the 1 MiB
single-file cap. A separate one-second child-sleeper test timed out and left no
named container. No task/evaluator/grader/candidate benchmark or model code
ran.

This still does not complete the preflight: integrate the runner with each task
evaluator and BananLoop readiness path; prove candidate processes cannot read
private case labels or evaluator code; bind the approved model proxy; test
full-size public DEV resource behavior; enforce the total disk cap; and add the
negative secret, path traversal, symlink, stop/resume, and scoring tests. Do not
start Issue #64 arms until those gates pass.

The probe's mount-target scan only catches known forbidden destinations; the
single-mount claim comes from inspecting the runner argv and rejecting image
volumes. It is not an exhaustive host-secret scan, and same-user agent access
to the detached host artifact path remains unproven.
