# BL-042 · Time Only 3600s

Base revision `be24a260615179f9437859e87fe8a8b2b5b12377`. Acceptance remains open.

The owner decision gives each arm 3600 wall-clock seconds. The Full child
inherits its parent's absolute deadline. The time-only CLI also configures
technical ceilings of 1000 agent calls, 1000 evaluator calls, 100000 model
tokens, 1000 iterations and approval for up to 1000 calls. The ceilings
can terminate a run early. The backend classifies applicable terminal
receipts as `LIMIT_EXHAUSTED` and records observed usage separately.
Missing provider usage stays unknown.

The Full backend converts the supplied epoch deadline to a monotonic
deadline at entry. Exploration, both Harbor configuration paths and
Optimization use the same process supervisor and monotonic deadline.
The original epoch stays in the child config and binding. Setup Git
commands are clipped to the parent's remaining time, and the pivot expiry
check uses that same time. Partial stdout and stderr survive a timeout.
The remaining Harbor timeout is calculated after preparation.

The 23 runner tests cover monotonic supervision across a wall-clock
change, inherited pivot binding, incomplete output, and a fake Full
timeout during public-task configuration. The timeout case preserves the
partial preparation output and final candidate and never starts the
fake Harbor command. Corrupt child configuration is rejected and archived
as a typed failure. Configuration updates use atomic JSON writes.
The POSIX timeout path sends SIGKILL to surviving group members after
SIGTERM even if the session leader exits first. Both backends share this
cleanup. A regression also starts a real POSIX child that ignores SIGTERM. After
cleanup, EOF on its inherited pipe confirms that it exited. Docker-managed
processes outside that group remain unverified.

## Parent watchdog

`benchmarks.autonomous_v01.arm_watchdog.run_arm_command` launches a
prepared worker in a POSIX process group. The parent exports its original
epoch and monotonic deadline to the worker. Full and Codex Direct reuse
that monotonic value and place their local child processes in the same
group. A worker with an invalid or mismatched inherited deadline fails
before starting its stages. At expiry the parent kills the group and
preserves stdout/stderr, including partial writes.

The six watchdog tests cover launch failure, expiry before launch,
partial logs and group termination, inheritance across a wall-clock
change, refusal of a mismatched deadline, and nested Full stage group
membership. The combined dry-run, watchdog, runner and v2 CLI suite passed
95 tests.

## Remaining acceptance

- The paired production launcher (BL-043) still needs to use the watchdog
  and use a recovery policy for real arms after a hard timeout. The POSIX
  `dry-run` CLI exercises the command watchdog in all 24 synthetic arms.
  A real subprocess test preserves its partially written candidate bytes
  after the hard timeout. The in-process test injection path remains available.
- Docker workloads managed by Harbor may outlive the POSIX process group.
  The launcher needs to record and verify cleanup of task-owned containers.
  Docker socket access is denied in this environment.

The offline verification used synthetic subprocesses and retained ordinary
Supervised HIL behavior.

## Контейнеры · аудит 22.11.2026

Public DEV runners создают контейнеры с уникальными именами и при
обнаруженном таймауте передают конкретное имя в Docker kill/rm.
CovType использует cidfile. Эти пути покрыты offline-тестами.
Harbor lifecycle не сохраняет идентификатор созданного контейнера в
доступном parent receipt. При аварийном завершении группы процессов
подтвердить остановку контейнера по текущим артефактам нельзя.
Нужна привязка Harbor lifecycle к уникальному идентификатору arm
и проверка его очистки. Docker socket в этой среде возвращает EPERM.
Тесты public DEV и watchdog прошли, 23 passed.

## Full launcher verification · 24 Nov 2026

The production single Full arm launcher now uses the parent watchdog. It
preserves the worker's captured artifact and checks cleanup of the exact
Compose projects derived from the Harbor trial config. For a single completed
trial without retries, matching terminal job and trial records establish the
project inventory. Incomplete inventory keeps the overall cleanup status
UNKNOWN, even when removal succeeds for all identified projects.

At `272182f`, 124 tests passed across the watchdog, launcher, cleanup,
project identification, runner, and campaign CLI. The CARPS diagnostic on
`2093475` ended with a terminal Harbor trial and cleanup CLEANED. That run
exercised a candidate evaluator timeout while Harbor returned normally.

Docker Engine 29.8.2 was available during this check. In a separate synthetic
probe, the watchdog killed the worker and cleanup removed the remaining
labeled Docker container object. The object was already stopped when checked.
Proof of container cleanup after the parent watchdog interrupts an active
Harbor trial remains open, as does 24-arm paired production acceptance.

### Real Harbor timeout without model calls

An isolated Harbor 0.25.0 trial used the built-in Oracle agent to run a
sleep script inside Docker. Verification was disabled. The BananLoop parent
watchdog interrupted Harbor after 55 seconds and recorded `TIMED_OUT` with
return code `-9`. The resulting job and trial configs identified exactly
`task__v2ake6q__agent` and `task__v2ake6q__env`. Production cleanup removed
two containers from the environment project and verified that both identified
projects had no containers left. The incomplete Harbor inventory still does
not prove overall cleanup beyond these projects.

Receipt: `/tmp/bl042-harbor-probe-l62lcgzj/retry/probe.json`.
