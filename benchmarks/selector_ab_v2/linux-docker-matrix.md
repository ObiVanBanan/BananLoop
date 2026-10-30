# Linux Docker compatibility matrix

## Scope

The full `main/tests` suite ran against a read-only bind mount of this checkout. Each test container had `--network none`, a read-only root filesystem, and only an executable temporary `/tmp` mount. No Codex auth or external service credentials were mounted. The two dependencies were installed during image build (`pytest==9.2.0`, `pydantic==2.14.2`).

| Python | Base image digest | Result | Duration | Log |
|---|---|---:|---:|---|
| 3.11.18 | `python:3.11-bookworm@sha256:84fa927127b46670e768371d1ad1be271d57ba93a6fbb3479045c7c6c452432f` | 433 passed | 59.92 s | `ubuntu-py311-full-suite.log` |
| 3.12.16 | `python:3.12-bookworm@sha256:a5bab0ff300f91231c2e68bf73a8a8d60762ac39ce1c50eaf6803e4c38a9062b` | 433 passed | 71.08 s | `ubuntu-py312-full-suite.log` |
| 3.13.18 | `python:3.13-bookworm@sha256:3856e3e7bf4d0ba061501a84ed2eb0d429663307c0f54f191dc4de03c7081c3e` | 433 passed | 65.45 s | `ubuntu-py313-full-suite.log` |

These are Debian Bookworm containers, not GitHub Actions Ubuntu runners. The CI workflow's actual Ubuntu cells remain externally unverified, so Task 8.1 remains open despite this local Linux/Python matrix.

The first 3.11 strict run used a read-only root with the default `/root` HOME and had one environment-only failure when a registry test tried to create `/root/.bananloop`; 432 tests passed. Setting HOME to the writable temporary mount fixed the environment, and the subsequent complete run passed all 433 tests.
