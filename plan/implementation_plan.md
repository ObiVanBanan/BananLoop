# План реализации: BananLoop staged roadmap

## Общая информация

- Тип: correctness fixes и portable packaging.
- Источники: пользовательское исследование «BananLoop + Codex», research_report.md (обновление f062cba), design/adr-p0-correctness.md.
- Связанный PR: https://github.com/ObiVanBanan/BananLoop/pull/32.
- Один committable milestone: замкнутый набор P0-регрессий и исправлений без новой архитектуры. Запрос пользователя служит основанием для перечисленных изменений; push/merge и запуск GPU dogfood не входят в этот milestone.

## Дорожная карта (Milestones)

### M1: P0 product shell и достоверный учёт

- [x] Task 1.1: Добавить portable manifest и описать установочную структуру.
  - Файлы: plugins/bananloop/plugin.json, docs/codex-plugin.md; сохранить compatibility manifest.
  - DoD: root JSON соответствует официальному формату, skills обнаруживаются в root skills/, metadata согласованы; нет новых runtime dependencies.
- [x] Task 1.2: Защитить bootstrap от false goal/runtime requests.
  - Файлы: src/bananloop/bootstrap.py, tests/test_bootstrap.py.
  - DoD: bounded repair или BootstrapError вместо ложного needs_input; проверены exhausted repair, RU/EN формулировки, существующий interpreter, настоящие missing refs/credentials/packages/version. Готовность по-прежнему проверяет core.
- [x] Task 1.3: Развести измеренный outcome и попытку.
  - Файлы: src/bananloop/engine.py, src/bananloop/research.py (при необходимости), tests/test_engine.py, tests/test_completion.py, tests/test_research.py (по изменённым веткам).
  - DoD: NO_CHANGES/BLOCKED/UNGROUNDED/infra не увеличивают scientific plateau; реальный policy rejection увеличивает; phantom candidate остаётся PAUSED_INFRA; invalid evaluator/validation не становится scientific REJECTED; resume не дублирует учёт.
- [x] Task 1.4: Добавить прозрачный report accounting.
  - Файлы: src/bananloop/report.py, tests/test_attempt_accounting.py (или existing report tests), docs/codex-plugin.md.
  - DoD: JSON/Markdown показывают attempts и взаимоисключающие buckets; scientific count согласован с durable state; historical rows и recovered failure не считаются дважды; нет скрытых payloads в новых полях.
- [x] Task 1.5: Пройти testing, один review, security и deterministic workflow evaluation.
  - Файлы: plan/p0_completion.md, review_report.md, security_review.md; существующие regression tests.
  - DoD: scoped tests + full suite; negative/replay/infra fixtures; точные результаты и ограничения. Existing Codex source comments и untracked campaigns/uv.lock не включать в реализацию.

## Зависимости между задачами

Task 1.4 использует итоговую семантику Task 1.3. Task 1.5 выполняется после 1.1–1.4. Один implementation-agent выполняет M1, затем testing-agent, review-agent (один проход), security-agent; замечания исправляет implementation-agent с targeted rerun.

## Риски и заметки

- Имеющийся Python runtime не гарантирует нужную версию/пакеты. Не превращать любую строку со словом Python в false needs_input.
- Recovery с completed evaluator receipt не должен внезапно менять request hash. На несовместимые данные не выдавать научный verdict.
- Historical campaigns не мигрировать и не переоценивать silently.
- Rollback: отменить только P0 diff перечисленных product/test/docs файлов; schema/config/dependencies не меняются; исходный dirty codex.py сохранить.
- Baseline: полный набор тестов на f062cba запущен в отдельном `.tmp/p0-baseline-20261004-01`; результат будет внесён в completion. Новые basetemp должны иметь отдельные имена.
- Evaluation gate: deterministic command/stub scenarios против исходного поведения, без реальных model calls и без production model/prompt promotion. AnimeDiff dogfood остаётся следующим самостоятельным validation run.

## Completion record (2026-10-04)

- Portable manifest: root and compatibility JSON parsed; identity and user-facing descriptions match.
- Focused regression: `tests/test_bootstrap.py tests/test_engine.py` — 44 passed; attempt accounting — 4 passed after legacy-state review fix; two affected engine regressions — 2 passed.
- Full suite: 142 passed, 2 failed in Windows child-process cancellation (`tests/test_process.py` timeout/stop). The same two cases failed against unmodified f062cba baseline (131 passed, 2 failed).
- Review: one compatibility issue found and fixed; no second review pass. Security review: no new findings. Existing follow-up: consider a post-evaluator fingerprint check for frozen assets (P2).
- AnimeDiff `doctor --json` found `C:\Python313\python.exe` 3.13.1. `goal --prepare-only` could not start because the source checkout is dirty; no campaign state or bootstrap plan was created. The checkout was left intact.
- No commit, push, live model campaign, or GPU dogfood was performed in this milestone.

## Следующий этап после P0: stochastic readiness calibration (#34)

### M2: bounded A/A noise calibration

- [x] Task 2.1: Уточнить core-owned A/A calibration receipt.
  - Файлы: `src/bananloop/readiness.py`, `tests/test_readiness.py`.
  - DoD: readiness запускает ограниченное число независимых smoke измерений с теми же snapshot, case/input и protocol; receipt показывает число повторов, median, empirical noise bound и сравнение с `min_improvement`. Unstable identity или превышение noise gate не получает статус PASSED.
  - Оценка: 2–4 часа.
  - Зависимости: завершённый P1 EvaluatorBinding (#33).
- [x] Task 2.2: Проверить интеграцию readiness receipt в bootstrap repair и подготовленный plan.
  - Файлы: `src/bananloop/bootstrap.py`, `src/bananloop/agents/codex.py`, `src/bananloop/agents/pi.py`, `tests/test_bootstrap.py`.
  - DoD: при noisy/unstable smoke core передаёт точную диагностику в bounded repair; после исчерпания попыток кампания не инициализируется; успешная подготовка сохраняет calibration receipt без изменения `CampaignSpec` hash semantics.
  - Оценка: 2–4 часа.
  - Зависимости: Task 2.1.
- [x] Task 2.3: Провести проверки milestone, review и security gate.
  - Файлы: `plan/stochastic-readiness-completion.md`, `plan/review_report-stochastic-readiness.md`, `plan/security_review-stochastic-readiness.md`.
  - DoD: readiness/bootstrap tests и полный suite запущены; platform-baseline failures отделены; выполнен один review и отдельный focused security pass; записаны rollback и ограничения. Это подтверждает repeatability/noise, а не предметную валидность scorer.
  - Оценка: 2 часа.
  - Зависимости: Task 2.1–2.2.

**Ограничение M2:** это этап оценки измерительного шума на A/A повторах. Signal-sanity для known-good/bad examples или controlled degradation остаётся отдельной частью roadmap и не подразумевается receipt данного milestone.

### Completion record (2026-10-04)

- Readiness/bootstrap regression set: 27 passed, 1 skipped (Windows denied symlink creation with WinError 1314).
- Full suite: two failures remain in `tests/test_process.py::test_cancellation_stops_child_work[timeout]` and `[stop]`; both are the previously observed Windows child-process tree cancellation baseline failures. No other failures were reported.
- One review pass found and repaired ignored workspace-state leakage and a paired-metric threshold scale mismatch. No second review pass was run.
- Focused security gate found no blocking/major findings. A minor symlink cleanup concern was fixed; the platform-specific sentinel test was skipped because symlink creation is unavailable here. Existing evaluator stdout/stderr persistence into bootstrap repair remains advisory.
- `empirical_noise_bound` is the observed max-min range across three smoke runs, not a statistical confidence bound. This milestone proves repeatability/noise only, not scorer signal validity.
- Rollback: revert only the M2 readiness/bootstrap prompt/test changes and this section, preserving M1 P0/P1 changes and user-owned dirty files. No schema migration or new dependency was added.
- No commit or push was performed.

## Оставшиеся этапы архитектурного ADR

### M3: проверка чувствительности evaluator (#35)

- [x] Task 3.1: Добавить project-specific positive/negative case contract и core-owned signal sanity.
  - Файлы: `src/bananloop/bootstrap.py`, `src/bananloop/readiness.py`, `src/bananloop/agents/codex.py`, `src/bananloop/agents/pi.py`, `tests/test_readiness.py`.
  - DoD: bootstrap может сослаться на подтверждённые положительные/отрицательные кейсы из evaluator output; core сам считает направленный separation primary metric на одном protocol и snapshot. Неверная метрика/постоянный scorer/пропущенные кейсы fail readiness и идут в bounded repair. При отсутствии defensible labels фиксируется явный `UNAVAILABLE`/DEV_ONLY без выдуманных labels.
  - Оценка: 4–6 часов.
  - Зависимости: M2.
- [x] Task 3.2: Заморозить signal-sanity результат в campaign identity и operator report.
  - Файлы: `src/bananloop/contracts.py`, `src/bananloop/bootstrap.py`, `src/bananloop/evaluator_binding.py`, `src/bananloop/report.py`, `tests/test_bootstrap.py`, `tests/test_engine.py`.
  - DoD: readiness status/receipt digest привязаны к immutable campaign/evaluator binding; legacy approved hashes остаются прежними; отчёт показывает уровень evidence и не выдаёт scorer без успешного signal check за независимо подтверждённую quality-цель.
  - Оценка: 3–5 часов.
  - Зависимости: Task 3.1.
- [x] Task 3.3: Проверки, один review и security gate signal sanity.
  - Файлы: `plan/signal-sanity-completion.md`, соответствующие review/security reports.
  - DoD: positive control проходит; reversed/constant/wrong-metric/missing-case контроль отвергается; unavailable path не изобретает GOLD; весь тестовый suite и gates записаны.
  - Оценка: 2–3 часа.
  - Зависимости: Task 3.1–3.2.

### Completion record (2026-10-05)

- Signal-sanity contract supports project-declared positive/negative case IDs, directed primary-metric separation, bounded A/A repetitions, and explicit `UNAVAILABLE` with a reason. Wrong metric, inverted/constant separation, missing cases, duplicate/overlapping labels, and malformed unavailable declarations are rejected.
- Signal readiness receipt and digest are persisted in `CampaignSpec`; omitted defaults preserve historical approved hashes. Reports expose status, digest, and evidence level.
- Security/review found that bootstrap-selected labels and evaluator-produced values are not independently provenance-verified. Passing group separation is therefore `DIAGNOSTIC_ONLY` / `diagnostic_only_unverified_labels`; it cannot upgrade campaign assessment. `UNAVAILABLE` retains `DEV_ONLY` behavior. See `docs/signal-sanity.md`.
- Core regression set: 50 passed, 1 skipped (Windows denied symlink creation, `WinError 1314`). Full suite: 173 collected; 170 passed, 2 failed in the known Windows child-process cancellation cases (`tests/test_process.py::test_cancellation_stops_child_work[timeout]` and `[stop]`), matching the pre-M3 baseline; 1 symlink test skipped.
- Review was one pass. It found the unverified-label evidence-upgrade issue; the implementation agent corrected it and added a regression proving diagnostic labels do not alter final assessment. No second review pass was run. Security review found no remaining path/input escape issue after the trust-boundary correction.
- Limitation: this verifies arithmetic separation for agent-declared groups, not the truth of labels or the evaluator's construct validity. Promotion to independent evidence requires core-verifiable fixture/label provenance.
- Rollback: revert only M3 signal-sanity contract/readiness/report/prompt/test/docs changes and this completion record, preserving M1/M2 and user-owned dirty files. No schema migration or new dependency was added.
- No commit or push was performed.

### M4: несколько целей на один repository

- [x] Task 4.1: Добавить repo-level SQLite registry и path-safe campaign state layout.
  - Файлы: `src/bananloop/registry.py` (новый), `src/bananloop/contracts.py`, `src/bananloop/lock.py` (только если нужен project registry lock), registry tests.
  - DoD: registry индексирует campaign ID/request key/fingerprint/state path; unique request key и active binding обновляются транзакционно; IDs и relative paths безопасны; registry не дублирует campaign outcome.
  - Оценка: 3–5 часов.
- [x] Task 4.2: Подключить goal/start resolution и selector-aware CLI.
  - Файлы: `src/bananloop/cli.py`, `src/bananloop/bootstrap.py` при необходимости, `tests/test_cli.py`, новые registry CLI tests.
  - DoD: повтор request ID или canonical goal выбирает ту же запись; конфликт request ID с другим goal завершает ошибкой; новая цель получает собственный state; `campaigns list` и `--campaign-id` работают; явный `--state-dir` оставляет старое поведение.
  - Оценка: 3–5 часов.
- [x] Task 4.3: Сохранить legacy path и восстановление после crash/concurrency.
  - Файлы: `RuntimePaths`/registry resolver, `CampaignLock` integration, tests для old DB, crash points, lock owners.
  - DoD: старый `campaign.sqlite3` остаётся на месте и доступен как `legacy`; registry insert до bootstrap безопасно повторяется; один ключ не получает два owner; разные цели могут сосуществовать без shared mutable workspace.
  - Оценка: 3–5 часов.
- [x] Task 4.4: Выполнить targeted tests, один review и security pass.
  - Файлы: `plan/campaign-registry-completion.md`, review/security reports.
  - DoD: unit/integration/CLI/migration/crash/concurrency tests; полный suite; rollback и точные ограничения описаны.
  - Оценка: 2–3 часа.

#### Completion record (2026-10-05)

- Added repo-local SQLite campaign registry, deterministic hash-only campaign IDs/state paths, goal/request key identity, atomic active binding, lazy legacy registration and `campaigns list`.
- `goal/start` reserve before bootstrap; identical canonical goal reuses its campaign, optional `--request-id` can intentionally distinguish another run, conflicting request ID + different goal is rejected. Paused campaign resumes, terminal campaign returns its existing result. Different goals have separate workspaces and campaign locks.
- `run/resume/status/stop/report/export` accept `--campaign-id`; default uses active binding. Explicit `--state-dir` keeps the old single-campaign behavior. Legacy `campaign.sqlite3` is read-only fingerprinted and remains at the original path as campaign ID `legacy`.
- Registry reservation is durable before bootstrap; retry after a process interruption reuses the same record and directory. Campaign DB paths are rejected if they are symlinks/reparse points; corrupt registry bytes are preserved and reported.
- Focused integration (`test_registry.py`, `test_cli.py`, `test_completion.py`, `test_export.py`): 44 passed, 1 skipped before the final registry-key collision regression; post-fix registry/CLI rerun: 29 passed, 1 skipped. Full suite: 194 tests collected; 190 passed, 2 failed, 2 skipped. The two failures are the previously recorded Windows child-process cancellation failures (`tests/test_process.py` timeout/stop); both were present in the earlier baseline. Symlink safety checks were skipped because this host denied symlink creation (`WinError 1314`).
- One review pass found legacy goal re-entry could create a duplicate managed campaign; fixed by read-only legacy spec fingerprinting and synthetic legacy request key. Security pass found campaign DB symlink following; fixed with lstat/reparse rejection. No second review pass was run. Review agent also found no other actionable issue.
- Limitation: filesystem locks prevent duplicate owners for one state directory; destructive local users with write access to the same BananLoop state remain outside the trust model. Registry state contains indexes/active selection only; each campaign DB remains authoritative.
- Rollback: remove only `registry.py`, M4 CLI/tests/docs/ADR/planning changes and this record; preserve old campaign DB/workspace untouched and all M1–M3 changes. No campaign schema migration or runtime dependency was added.
- No commit or push was performed.

### M5: единый execution profile для моделей и ролей

- [x] Добавить опциональный TOML `default` + overrides только существующих ролей `bootstrap`/`propose`/`implement`/`analyze`; заморозить в CampaignSpec и сохранить безопасные launch receipts.
- [x] Не менять текущую модель Codex молча; отсутствие явных настроек наследует конфиг внешней CLI. Явная модель не получает fallback; entitlement проверяется внешней CLI во время вызова, без платного probe.
- [x] Покрыть argv, TOML schema/unknown roles, secret-key rejection, role inheritance, и backward-compatible approved hash для старых AgentConfig.
- [x] Полный suite и независимые review/security gates; repair findings и завершить milestone record.

#### M5 completion record (2026-10-05)

- Added optional typed TOML profiles via `--agent-profiles`: default backend/model/provider/reasoning plus overrides for actual bootstrap/propose/implement/analyze call sites. Exact model IDs are frozen in CampaignSpec; mutable Codex named profiles and arbitrary command/argv are excluded. No config preserves external CLI default selection.
- Bootstrap plan and durable `AGENT_LAUNCH_PROFILE` event record non-secret effective typed settings and CLI version; no raw args/credentials. Model entitlement is not probed with a paid call; the external CLI validates access on invocation and BananLoop does not fallback.
- Scoped M5 regression: 92 passed. Full suite: 201 collected, 197 passed, 2 skipped, 2 failed in existing Windows child-process cancellation checks (`tests/test_process.py` timeout/stop); these same two failures were observed before M5. `compileall` and `git diff --check` passed.
- One review pass found and closed mutable named-profile freeze gap (P1) and ADR schema mismatch (P2). Security pass found and closed secret echo and typed-vs-raw override issues (P2); no remaining scoped findings. No second review pass.
- No live model calls or campaign run; no commit/push in this milestone.

### M6: stable Codex lifecycle bridge

- [x] Реализовать один стабильный Stop hook/dispatcher, который связывает repository/session с registry и проверяет только durable BananLoop status/receipt; project-specific логика остаётся в замороженном evaluator bundle.
- [x] Обработать trust disabled/error/timeout, nested workers, повторный callback, stop и несколько кампаний; дорогое измерение не запускать из hook при каждом ходе.
- [x] Подтвердить Windows Codex CLI и plugin hook packaging по официальному контракту; Desktop trust-approved E2E отдельно отмечен как не проверенный и необязательный hook не меняет прямой CLI путь.

#### M6 completion record (2026-10-05)

- Added a Windows-aware plugin Stop hook and hidden CLI dispatcher. It binds the exact campaign marker to a SHA-256 session hash, reads campaign status, and asks for one continuation only while a known campaign is RUNNING, not stop-requested, and has no owner lock. The hook does not resume/run agents, evaluators, or campaigns. Unknown/error/timeout/terminal/repeated/locked cases fail open; direct `bananloop resume` remains the fallback.
- Older registry files upgrade lazily when the first validated marker is bound. Registry, managed state directories, campaign DBs and lock path are checked against symlinks/reparse points before sensitive opens. Hook persists only the hashed session ID and campaign ID; campaign DB stays read-only.
- Focused hook/registry/CLI tests: 46 passed, 3 skipped (symlink privilege unavailable, `WinError 1314`). `compileall` and `git diff --check` passed.
- Full suite: 218 collected; 212 passed, 4 skipped (Windows symlink privilege), 2 failed in `tests/test_process.py::test_cancellation_stops_child_work[timeout|stop]`; these are the same pre-existing Windows child-process cancellation failures recorded before M6.
- One review pass found a major pre-M6 registry upgrade binding bug; fixed and regression-tested, no second review. Security pass found two P2 symlink risks and a docs mismatch; fixed and covered with targeted tests. Symlink creation is denied in this Windows process, so symlink refusal cases could not execute here.
- `codex-cli 0.158.0` is installed locally and the plugin declares the supported hook wiring. Codex Desktop/CLI hook trust approval and live Stop execution remain external/user-controlled and were not verified. See `plan/codex-hook-completion.md` and the review/security reports.
- No live model/evaluator calls, campaign runs, commits or pushes were performed.
- Rollback: see `plan/codex-hook-completion.md`; the registry addition is additive and campaign DBs/contracts are unchanged.

### M7: end-to-end acceptance на типах реальных проектов

- [ ] Проверить repo + goal на bugfix, performance и output-quality сценариях с одной зафиксированной целью/бюджетом на сценарий. Cancellation goal дал sandbox-only failure и прошёл в host evaluator; error-redaction baseline подтвердил дефект, но readiness не создал CampaignSpec. Performance/output-quality требуют backend.
- [ ] Снять время до baseline, вопросы, ручные правки, repair rate, evaluator validity, campaign decision, artifact provenance и итоговую evidence level. Частичные наблюдения и пробелы записаны в `plan/m7-acceptance-progress.md`.
- [ ] Не объявлять качество/holdout доказанным fixture-тестами; live GPU/LLM campaign запускается только в разрешённом бюджете, все outputs и failures сохраняются, champion не публикуется автоматически.

#### Progress record (2026-10-05)

- Исправлена Windows отмена дерева дочерних процессов; process/bootstrap regressions, полный suite, compileall и security gate прошли. Это `LOCAL_REGRESSION_ONLY`, а не autonomous campaign acceptance.
- `rag_tender` acceptance не запускался: Qdrant/Docker недоступны, а исторические benchmark splits/seeds уже потреблены.
- Для AnimeDiff изучен только исторический null-candidate отчет с нулевой дельтой и грубой fidelity метрикой; он не доказывает улучшение semantic output quality.
- Clean-snapshot Codex bootstrap был разрешён после согласования точного payload/destination. Два BananLoop bootstraps выявили readiness/time-budget ограничения; подробности и receipts: `plan/m7-acceptance-progress.md`.

### Негативный список

Из ADR не переносить в MVP замену Python core на Codex-native loop, динамически генерируемые per-project hooks, обязательный MCP server или sandbox/OS isolation. Они не нужны для выполнения утверждённой границы: один plugin skill, project-specific frozen evaluator, stable supervisor и проверяемый lifecycle bridge.
